"""Transport controls for content-selected URLs, including the actual socket destination."""

import ipaddress
import re
import socket
from typing import Dict, List, Optional
import urllib.parse

import requests
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

# Keep special-purpose ranges excluded on Python versions with older IANA tables.
SPECIAL_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    '192.0.0.0/24', '192.88.99.0/24', '64:ff9b:1::/48',
    '3fff::/20', 'fec0::/10',
))
NAT64_NETWORK = ipaddress.ip_network('64:ff9b::/96')


class DestinationBlocked(Exception):
    """A permanent policy rejection, kept outside urllib3's OSError retry handling."""


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if ip in NAT64_NETWORK:
            embedded = ipaddress.IPv4Address(int(ip) & 0xffffffff)
        if embedded is not None:
            return public_address(str(embedded))
        if ip.teredo is not None:
            return False
    return (ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)
            and not any(ip in network for network in SPECIAL_NETWORKS))


def normalize_host(host: str) -> str:
    """Validate a bare hostname/IP and use the same IDNA conversion as Requests."""
    if not host or any(char.isspace() or char in '/\\@?#*[]%' for char in host):
        raise ValueError('expected a bare hostname or IP without a scheme, port, path, or wildcard')
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if ':' in host:
        raise ValueError('expected a bare hostname or IP without a port')
    prepared = requests.PreparedRequest()
    prepared.prepare_url('http://' + host, None)
    assert isinstance(prepared.url, str)
    normalized = urllib.parse.urlsplit(prepared.url).hostname or ''
    normalized = normalized.removesuffix('.')
    labels = normalized.split('.')
    if (len(normalized) > 253 or any(not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', label)
                                     for label in labels)):
        raise ValueError('invalid hostname')
    return normalized


def url_host(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('an HTTP(S) hostname is required')
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('credentials in URLs are not supported')
        if parsed.port == 0:
            raise ValueError('port must be positive')
        return normalize_host(parsed.hostname)
    except (ValueError, UnicodeError) as error:
        raise requests.exceptions.InvalidURL(str(error)) from error


def checked_addresses(host, port, allowed_hosts):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if normalize_host(host) not in allowed_hosts and any(not public_address(str(info[4][0])) for info in addresses):
        raise DestinationBlocked('destination is not a public address')
    return addresses


def connect_checked(connection, allowed_hosts):
    try:
        addresses = checked_addresses(connection._dns_host, connection.port, allowed_hosts)
    except socket.gaierror as dns_error:
        raise NewConnectionError(connection, str(dns_error)) from dns_error
    # Use the inspected sockaddr itself; resolving again would allow DNS rebinding.
    error = None
    for family, kind, proto, _, sockaddr in addresses:
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(connection.timeout)
            for option in connection.socket_options or []:
                sock.setsockopt(*option)
            if connection.source_address:
                sock.bind(connection.source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as caught:
            if sock is not None:
                sock.close()
            error = caught
    if isinstance(error, socket.timeout):
        raise ConnectTimeoutError(connection, 'connection timed out') from error
    raise NewConnectionError(connection, str(error))


class CheckedHTTPConnection(HTTPConnection):
    def __init__(self, *args, allowed_hosts, **kwargs):
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return connect_checked(self, self.allowed_hosts)


class CheckedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, allowed_hosts, **kwargs):
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        return connect_checked(self, self.allowed_hosts)


class CheckedHTTPPool(HTTPConnectionPool):
    ConnectionCls = CheckedHTTPConnection


class CheckedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = CheckedHTTPSConnection


class CheckedPoolManager(PoolManager):
    def __init__(self, *args, allowed_hosts, **kwargs):
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)
        self.pool_classes_by_scheme = {'http': CheckedHTTPPool, 'https': CheckedHTTPSPool}

    def _new_pool(self, scheme, host, port, request_context=None):
        pool = super()._new_pool(scheme, host, port, request_context)
        # Inject connection-only settings after pool-key construction. Pools and connections
        # retain immutable policy data, without references back to the adapter.
        pool.conn_kw['allowed_hosts'] = self.allowed_hosts
        return pool


class CheckedAdapter(requests.adapters.HTTPAdapter):
    """Preserve Host/SNI, check direct sockets, and enforce policy on proxy requests."""

    def __init__(self, allowed_hosts: List[str], proxies: Optional[Dict[str, str]] = None):
        self.allowed_hosts = frozenset(normalize_host(host) for host in allowed_hosts)
        self.trusted_proxies = frozenset((proxies or {}).values())
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = CheckedPoolManager(num_pools=connections, maxsize=maxsize, block=block,
                                              allowed_hosts=self.allowed_hosts, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        if proxy not in self.trusted_proxies:
            raise requests.exceptions.InvalidURL('proxy must be explicitly configured in proxies')
        return super().proxy_manager_for(proxy, **proxy_kwargs)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        try:
            proxy = requests.utils.select_proxy(request.url, proxies or {})
            if proxy:
                # A proxy resolves the destination itself. Check locally as well, and only
                # delegate to an operator-configured proxy trusted to enforce its own DNS policy.
                if proxy not in self.trusted_proxies:
                    raise DestinationBlocked('proxy must be explicitly configured in proxies')
                parsed = urllib.parse.urlsplit(request.url)
                checked_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                  self.allowed_hosts)
            return super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        except DestinationBlocked as error:
            # InvalidURL inherits OSError: raise it here, outside urllib3's wrapping layer.
            raise requests.exceptions.InvalidURL(str(error)) from error
        except socket.gaierror as error:
            raise requests.exceptions.ConnectionError(str(error)) from error


class CheckSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Even allow_redirects=False normally prepares a next request and drains its body.
        # The plugin handles each hop itself, closing redirect responses without reading them.
        return iter(())
