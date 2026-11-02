import errno
from http.server import BaseHTTPRequestHandler
import socket
from unittest.mock import Mock, patch
import weakref

import pytest
import requests
from urllib3.connection import HTTPConnection

from htmlproofer.network import (
    CheckedAdapter,
    DestinationBlocked,
    connect_checked,
    normalize_host,
    public_address,
)
from htmlproofer.plugin import HtmlProoferPlugin

pytestmark = pytest.mark.timeout(10)


def address_info(ip):
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ((ip, 80, 0, 0) if ':' in ip else (ip, 80))


@pytest.mark.parametrize('ip', [
    '127.0.0.2', '0.0.0.0', '10.0.0.1', '172.16.0.1', '192.168.0.1',
    '169.254.169.254', '100.64.0.1', '224.0.0.1', '240.0.0.1',
    '::1', '::', 'fc00::1', 'fe80::1', 'ff02::1', '::ffff:127.0.0.1',
    '64:ff9b::a00:1', '2002:0a00:0001::1', 'fec0::1', '192.0.0.8', '192.88.99.1', '3fff::1',
])
def test_non_public_addresses_are_rejected_before_connecting(ip):
    adapter = CheckedAdapter([])
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info(ip)]), \
            patch('htmlproofer.network.socket.socket') as sock:
        with pytest.raises(DestinationBlocked):
            connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter.allowed_hosts)
        sock.assert_not_called()


def test_mixed_public_and_private_dns_answers_are_rejected():
    adapter = CheckedAdapter([])
    answers = [address_info('8.8.8.8'), address_info('127.0.0.1')]
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=answers), \
            patch('htmlproofer.network.socket.socket') as sock:
        with pytest.raises(DestinationBlocked):
            connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter.allowed_hosts)
        sock.assert_not_called()


def test_connection_uses_inspected_address_without_resolving_again():
    adapter = CheckedAdapter([])
    # A second DNS lookup would return loopback. There must only be one lookup.
    with patch('htmlproofer.network.socket.getaddrinfo',
               side_effect=[[address_info('8.8.8.8')], [address_info('127.0.0.1')]]) as dns, \
            patch('htmlproofer.network.socket.socket') as sock:
        connection = HTTPConnection('selected.example', timeout=1.0)
        connect_checked(connection, adapter.allowed_hosts)
        dns.assert_called_once()
        sock.return_value.connect.assert_called_once_with(('8.8.8.8', 80))
        assert connection.host == 'selected.example'


def test_public_address_fallback_after_connect_timeout():
    adapter = CheckedAdapter([])
    with patch('htmlproofer.network.socket.getaddrinfo',
               return_value=[address_info('8.8.8.8'), address_info('1.1.1.1')]), \
            patch('htmlproofer.network.socket.socket') as sock:
        sock.return_value.connect.side_effect = [socket.timeout(), None]
        connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter.allowed_hosts)
        assert [call.args for call in sock.return_value.connect.call_args_list] == [
            (('8.8.8.8', 80),), (('1.1.1.1', 80),)]


@pytest.mark.parametrize('ip', [
    '8.8.8.8', '2606:4700:4700::1111', '::ffff:8.8.8.8',
    '64:ff9b::808:808', '2002:0808:0808::1',
])
def test_public_addresses(ip):
    assert public_address(ip)


def test_private_destination_denied_and_explicit_host_allowed(configured_plugin, servers):
    server, url = servers()
    assert configured_plugin().resolve_web_scheme(url) == -1
    assert server.paths == []
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url) == 200
    assert server.paths == ['/']


@pytest.mark.parametrize('target', ['http://127.0.0.1/', 'http://169.254.169.254/', 'file:///tmp/test'])
def test_redirect_destination_is_rechecked(configured_plugin, servers, target):
    server, url = servers()
    server.target = target
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url + '/redirect') == -1
    assert server.paths == ['/redirect']


def test_redirect_limit(configured_plugin, servers):
    server, url = servers()
    server.target = '/redirect'
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url + '/redirect') == -1
    assert len(server.paths) == 6


def test_ambient_proxies_and_credentials_are_not_used(configured_plugin, monkeypatch, servers, tmp_path):
    _, url = servers()
    netrc = tmp_path / 'netrc'
    netrc.write_text('machine localhost login secret password secret\n')
    monkeypatch.setenv('NETRC', str(netrc))
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    plugin = configured_plugin(allow_private_hosts=['localhost'])
    session = plugin._get_session()
    prepared = session.prepare_request(requests.Request('GET', url))
    assert 'Authorization' not in prepared.headers
    assert not session.trust_env
    assert plugin.resolve_web_scheme(url) == 200


def test_url_credentials_are_rejected(configured_plugin, servers):
    server, url = servers()
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(
        url.replace('://', '://user:password@')) == -1
    assert server.paths == []


def test_socket_creation_failure_falls_back_to_ipv4():
    adapter = CheckedAdapter([])
    connected = Mock()
    answers = [address_info('2606:4700:4700::1111'), address_info('8.8.8.8')]
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=answers), \
            patch('htmlproofer.network.socket.socket',
                  side_effect=[OSError(errno.EAFNOSUPPORT, 'IPv6 disabled'), connected]) as sockets:
        assert connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter.allowed_hosts) is connected
    assert sockets.call_count == 2
    connected.connect.assert_called_once_with(('8.8.8.8', 80))
    connected.close.assert_not_called()


@pytest.mark.parametrize('ip', ['127.0.0.1', '169.254.169.254', '::1', '64:ff9b::a00:1', '2002:0a00:0001::1'])
def test_blocked_destination_is_not_retried(configured_plugin, ip):
    plugin = configured_plugin(retry_max_times=3)
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info(ip)]) as dns, \
            patch('htmlproofer.network.socket.socket') as sock, \
            patch('htmlproofer.plugin.time.sleep') as sleep:
        # Exercise Session -> adapter -> urllib3 -> connection, not a mocked Session.get.
        assert plugin.resolve_web_scheme('http://selected.example/') == -1
    dns.assert_called_once()
    sock.assert_not_called()
    sleep.assert_not_called()


def test_localhost_policy_rejection_is_not_retried(configured_plugin, servers):
    server, url = servers()
    plugin = configured_plugin(retry_max_times=3)
    resolve = socket.getaddrinfo
    with patch('htmlproofer.network.socket.getaddrinfo', wraps=resolve) as dns, \
            patch('htmlproofer.plugin.time.sleep') as sleep:
        assert plugin.resolve_web_scheme(url) == -1
    dns.assert_called_once()
    sleep.assert_not_called()
    assert server.paths == []


def test_redirect_policy_rejection_is_not_retried(configured_plugin, servers):
    server, url = servers()
    server.target = 'http://127.0.0.1/'
    with patch('htmlproofer.plugin.time.sleep') as sleep:
        assert configured_plugin(allow_private_hosts=['localhost'], retry_max_times=3).resolve_web_scheme(
            url + '/redirect') == -1
    sleep.assert_not_called()
    assert server.paths == ['/redirect']


def test_utf8_redirect_location(configured_plugin, servers):
    server, url = servers()
    # http.client exposes UTF-8 header bytes as Latin-1 text.
    server.target = '/wiki/Café'.encode('utf-8').decode('latin-1')
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url + '/redirect') == 200
    assert server.paths == ['/redirect', '/wiki/Caf%C3%A9']


@pytest.mark.parametrize('host, encoded', [
    ('موقع1.example', 'xn--1-znc0alp.example'),
    ('straße.example', 'xn--strae-oqa.example'),
    ('EXAMPLE.COM.', 'example.com'),
])
def test_international_allowlist_matches_requests(configured_plugin, servers, monkeypatch, host, encoded):
    server, url = servers()
    resolve = socket.getaddrinfo
    seen = []

    def addresses(name, port, *args, **kwargs):
        seen.append(name)
        assert name.rstrip('.') == encoded
        return resolve('localhost', port, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', addresses)
    plugin = configured_plugin(allow_private_hosts=[host])
    assert plugin.config['allow_private_hosts'] == [encoded]
    assert plugin.resolve_web_scheme(url.replace('localhost', host)) == 200
    assert server.paths == ['/']
    assert len(seen) == 1


@pytest.mark.parametrize('host', [
    'localhost:8000', 'http://host', '*.corp', 'a..b', 'a..', '', 'host/path',
    'user@host', '[::1]', 'host?query', 'host#fragment', ' host', 'host ', 'host\\path',
])
def test_invalid_allowlist_is_a_config_error(host):
    plugin = HtmlProoferPlugin()
    errors, _ = plugin.load_config({'allow_private_hosts': [host]})
    assert len(errors) == 1
    assert errors[0][0] == 'allow_private_hosts'


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '::1', 'app_server', 'docs.internal.example'])
def test_valid_allowlist(configured_plugin, host):
    assert configured_plugin(allow_private_hosts=[host]).config['allow_private_hosts'] == [normalize_host(host)]


def test_malformed_url_is_rejected_before_requesting(configured_plugin):
    with patch('requests.Session.get') as get, patch('htmlproofer.plugin.time.sleep') as sleep:
        assert configured_plugin(retry_max_times=3).resolve_web_scheme('http://') == -1
    get.assert_not_called()
    sleep.assert_not_called()


def test_connection_reuse_keeps_socket_policy(configured_plugin, servers):
    server, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'])
    resolve = socket.getaddrinfo
    with patch('htmlproofer.network.socket.getaddrinfo', wraps=resolve) as dns:
        assert plugin.resolve_web_scheme(url + '/first') == 200
        assert plugin.resolve_web_scheme(url + '/second') == 200
    assert server.paths == ['/first', '/second']
    assert len(set(server.peers)) == 1
    dns.assert_called_once()
    plugin._get_session().close()


def test_pools_share_classes_and_keep_independent_policy():
    first, second = CheckedAdapter(['first.example']), CheckedAdapter(['second.example'])
    for scheme in ('http', 'https'):
        pool_one = first.poolmanager.connection_from_url(scheme + '://example.com')
        pool_two = second.poolmanager.connection_from_url(scheme + '://example.com')
        connection_one, connection_two = pool_one._new_conn(), pool_two._new_conn()
        assert type(connection_one) is type(connection_two)
        assert connection_one.allowed_hosts == frozenset({'first.example'})
        assert connection_two.allowed_hosts == frozenset({'second.example'})


def test_retained_pool_does_not_keep_adapter_alive():
    adapter = CheckedAdapter([])
    pool = adapter.poolmanager.connection_from_url('http://example.com')
    reference = weakref.ref(adapter)
    del adapter
    assert reference() is None
    assert pool.conn_kw['allowed_hosts'] == frozenset()


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.paths.append(self.path)
        redirect = self.path.endswith('/redirect')
        self.send_response(302 if redirect else 200)
        if redirect:
            self.send_header('Location', self.server.target)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_CONNECT(self):
        self.server.paths.append(self.path)
        self.send_error(502, 'Test proxy does not tunnel')

    def log_message(self, *args):
        pass


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_unconfigured_proxy_cannot_bypass_policy(configured_plugin, servers, scheme):
    server, proxy_url = servers(handler=ProxyHandler)
    session = configured_plugin()._get_session()
    # Enabling trust_env or injecting a per-request proxy must not bypass the adapter.
    with patch('htmlproofer.network.socket.getaddrinfo') as dns:
        with pytest.raises(requests.exceptions.InvalidURL, match='explicitly configured'):
            session.get(scheme + '://example.com', proxies={scheme: proxy_url})
    dns.assert_not_called()
    assert server.paths == []


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_configured_proxy_rejects_private_target(configured_plugin, servers, scheme):
    server, proxy_url = servers(handler=ProxyHandler)
    plugin = configured_plugin(proxies={scheme: proxy_url}, retry_max_times=3)
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info('127.0.0.1')]) as dns, \
            patch('htmlproofer.plugin.time.sleep') as sleep:
        assert plugin.resolve_web_scheme(scheme + '://selected.example/') == -1
    dns.assert_called_once()
    sleep.assert_not_called()
    assert server.paths == []


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_configured_proxy_checks_public_target(configured_plugin, servers, monkeypatch, scheme):
    server, proxy_url = servers(handler=ProxyHandler)
    plugin = configured_plugin(proxies={scheme: proxy_url})
    resolve = socket.getaddrinfo
    seen = []

    def addresses(host, port, *args, **kwargs):
        seen.append(host)
        if host == 'selected.example':
            return [address_info('8.8.8.8')]
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', addresses)
    status = plugin.resolve_web_scheme(scheme + '://selected.example/path')
    assert status == (200 if scheme == 'http' else -1)
    assert seen[0] == 'selected.example'
    assert server.paths == (['http://selected.example/path'] if scheme == 'http' else ['selected.example:443'])


@pytest.mark.parametrize('proxies', [
    {'ftp': 'http://proxy'}, {'http': 1}, {'https': 'socks5://proxy'},
    {'http': 'proxy:8000'}, {'http': 'http://proxy:0'}, {'http': 'http://proxy:99999'},
    {'http': 'http://a..b'}, {'http': 'http://proxy/path'},
])
def test_invalid_proxy_is_a_config_error(proxies):
    plugin = HtmlProoferPlugin()
    errors, _ = plugin.load_config({'proxies': proxies})
    assert len(errors) == 1
    assert errors[0][0] == 'proxies'


@pytest.mark.parametrize('ip', ['64:ff9b::808:808', '2002:0808:0808::1'])
def test_public_transition_address_is_used_without_resolving_again(ip):
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info(ip)]) as dns, \
            patch('htmlproofer.network.socket.socket') as sock:
        connect_checked(HTTPConnection('selected.example', timeout=1.0), frozenset())
    dns.assert_called_once()
    sock.return_value.connect.assert_called_once_with((ip, 80, 0, 0))


def test_dns_failure_is_still_retried(configured_plugin):
    with patch('htmlproofer.network.socket.getaddrinfo', side_effect=socket.gaierror('DNS failure')) as dns, \
            patch('htmlproofer.plugin.time.sleep') as sleep:
        assert configured_plugin(retry_max_times=3).resolve_web_scheme('http://selected.example/') == -1
    assert dns.call_count == 4
    assert [call.args for call in sleep.call_args_list] == [(2,), (4,), (8,)]


def test_configured_proxy_rechecks_redirect(configured_plugin, servers, monkeypatch):
    server, proxy_url = servers(handler=ProxyHandler)
    server.target = 'http://127.0.0.1/'
    plugin = configured_plugin(proxies={'http': proxy_url}, retry_max_times=3)
    resolve = socket.getaddrinfo

    def addresses(host, port, *args, **kwargs):
        if host == 'selected.example':
            return [address_info('8.8.8.8')]
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', addresses)
    with patch('htmlproofer.plugin.time.sleep') as sleep:
        assert plugin.resolve_web_scheme('http://selected.example/redirect') == -1
    sleep.assert_not_called()
    assert server.paths == ['http://selected.example/redirect']


def test_configured_proxy_permits_allowlisted_private_target(configured_plugin, servers):
    server, proxy_url = servers(handler=ProxyHandler)
    plugin = configured_plugin(allow_private_hosts=['localhost'], proxies={'http': proxy_url})
    assert plugin.resolve_web_scheme('http://localhost:12345/private') == 200
    assert server.paths == ['http://localhost:12345/private']
