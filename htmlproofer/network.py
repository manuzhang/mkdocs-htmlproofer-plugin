"""Transport controls for content-selected URLs, including the actual socket destination."""

import ipaddress
import socket
from typing import List
import urllib.parse

import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

# Keep special-purpose and translation ranges excluded even on Python versions whose
# ipaddress tables predate newer IANA classifications.
SPECIAL_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    '192.0.0.0/24', '192.88.99.0/24', '64:ff9b::/96', '64:ff9b:1::/48',
    '2002::/16', '3fff::/20', 'fec0::/10',
))


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return public_address(str(ip.ipv4_mapped))
        if ip.teredo is not None:
            return False
    return (ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)
            and not any(ip in network for network in SPECIAL_NETWORKS))


def url_host(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('an HTTP(S) hostname is required')
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('credentials in URLs are not supported')
        # Validate ports here too, before preparing or opening a request.
        if parsed.port == 0:
            raise ValueError('port must be positive')
        return parsed.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    except (ValueError, UnicodeError) as error:
        raise requests.exceptions.InvalidURL(str(error)) from error


def checked_addresses(host, port, allowed_hosts):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise requests.exceptions.ConnectionError(str(error)) from error
    trusted = host.lower().rstrip('.') in allowed_hosts
    if not addresses or (not trusted and any(not public_address(str(info[4][0])) for info in addresses)):
        raise requests.exceptions.InvalidURL('destination is not a public address')
    return addresses


def connect_checked(connection, adapter):
    addresses = checked_addresses(connection._dns_host, connection.port, adapter.allowed_hosts)
    # Use the inspected sockaddr itself. Calling create_connection(host) here would resolve
    # again, allowing the destination to change between validation and connection.
    error = None
    for family, kind, proto, _, sockaddr in addresses:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(connection.timeout)
            for option in connection.socket_options or []:
                sock.setsockopt(*option)
            if connection.source_address:
                sock.bind(connection.source_address)
            sock.connect(sockaddr)
            return sock
        except requests.exceptions.RequestException:
            sock.close()
            raise
        except OSError as caught:
            sock.close()
            error = caught
    if isinstance(error, socket.timeout):
        raise ConnectTimeoutError(connection, 'connection timed out') from error
    raise NewConnectionError(connection, str(error))


class CheckedAdapter(requests.adapters.HTTPAdapter):
    """Preserve the URL hostname for Host/SNI while checking the connected IP address."""

    def __init__(self, allowed_hosts: List[str]):
        self.allowed_hosts = {host.encode('idna').decode('ascii').lower().rstrip('.') for host in allowed_hosts}
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        adapter = self

        class CheckedHTTPConnection(HTTPConnection):
            def _new_conn(self):
                return connect_checked(self, adapter)

        class CheckedHTTPSConnection(HTTPSConnection):
            def _new_conn(self):
                return connect_checked(self, adapter)

        class HTTPPool(HTTPConnectionPool):
            ConnectionCls = CheckedHTTPConnection

        class HTTPSPool(HTTPSConnectionPool):
            ConnectionCls = CheckedHTTPSConnection

        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}


class CheckSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Even allow_redirects=False normally prepares a next request and drains its body.
        # The plugin handles each hop itself, closing redirect responses without reading them.
        return iter(())
