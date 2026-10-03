from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pathlib
import re
import socket
import ssl
import threading
import time
from unittest.mock import patch

import pytest
import requests
import trustme
from urllib3.connection import HTTPConnection

from htmlproofer.network import CheckedAdapter, connect_checked, public_address
from htmlproofer.plugin import HtmlProoferPlugin

pytestmark = pytest.mark.timeout(10)


def configured_plugin(**config):
    plugin = HtmlProoferPlugin()
    errors, _ = plugin.load_config(config)
    assert not errors
    return plugin


def address_info(ip):
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 80)


@pytest.mark.parametrize('ip', [
    '127.0.0.2', '0.0.0.0', '10.0.0.1', '172.16.0.1', '192.168.0.1',
    '169.254.169.254', '100.64.0.1', '224.0.0.1', '240.0.0.1',
    '::1', '::', 'fc00::1', 'fe80::1', 'ff02::1', '::ffff:127.0.0.1',
    '64:ff9b::a00:1', '2002:0a00:0001::1', 'fec0::1', '192.0.0.8', '192.88.99.1', '3fff::1',
])
def test_non_public_addresses_are_rejected_before_connecting(ip):
    adapter = CheckedAdapter([])
    adapter.begin(1.0)
    try:
        with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info(ip)]), \
                patch('htmlproofer.network.socket.socket') as sock:
            with pytest.raises(requests.exceptions.InvalidURL):
                connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
            sock.assert_not_called()
    finally:
        adapter.end()


def test_mixed_public_and_private_dns_answers_are_rejected():
    adapter = CheckedAdapter([])
    adapter.begin(1.0)
    try:
        answers = [address_info('8.8.8.8'), address_info('127.0.0.1')]
        with patch('htmlproofer.network.socket.getaddrinfo', return_value=answers), \
                patch('htmlproofer.network.socket.socket') as sock:
            with pytest.raises(requests.exceptions.InvalidURL):
                connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
            sock.assert_not_called()
    finally:
        adapter.end()


def test_connection_uses_inspected_address_without_resolving_again():
    adapter = CheckedAdapter([])
    adapter.begin(1.0)
    try:
        # A second DNS lookup would return loopback. There must only be one lookup.
        with patch('htmlproofer.network.socket.getaddrinfo',
                   side_effect=[[address_info('8.8.8.8')], [address_info('127.0.0.1')]]) as dns, \
                patch('htmlproofer.network.socket.socket') as sock:
            connection = HTTPConnection('selected.example', timeout=1.0)
            connect_checked(connection, adapter)
            dns.assert_called_once()
            sock.return_value.connect.assert_called_once_with(('8.8.8.8', 80))
            assert connection.host == 'selected.example'
    finally:
        adapter.end()


def test_public_address_fallback_after_connect_timeout():
    adapter = CheckedAdapter([])
    adapter.begin(1.0)
    try:
        with patch('htmlproofer.network.socket.getaddrinfo',
                   return_value=[address_info('8.8.8.8'), address_info('1.1.1.1')]), \
                patch('htmlproofer.network.socket.socket') as sock:
            sock.return_value.connect.side_effect = [socket.timeout(), None]
            connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
            assert [call.args for call in sock.return_value.connect.call_args_list] == [
                (('8.8.8.8', 80),), (('1.1.1.1', 80),)]
    finally:
        adapter.end()


@pytest.mark.parametrize('ip', ['8.8.8.8', '2606:4700:4700::1111', '::ffff:8.8.8.8'])
def test_public_addresses(ip):
    assert public_address(ip)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.respond()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    def respond(self):
        self.server.paths.append(self.path)
        if self.path == '/slow-headers':
            self.drip(bytes([byte]) for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
            return
        if self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', self.server.target)
            self.end_headers()
            # Redirect responses should be closed, without downloading their bodies.
            self.drip([b'x'] * 20)
            return
        if self.path.startswith('/keepalive'):
            self.protocol_version = 'HTTP/1.1'
            self.close_connection = False  # Deliberately ignore the client's close header.
        self.send_response(200)
        if self.path.startswith('/keepalive'):
            self.send_header('Content-Length', '40' if self.path.endswith('slow-body') else '5')
        if self.path == '/large':
            self.send_header('Content-Length', '1000')
        if self.path == '/encoded':
            self.send_header('Content-Encoding', 'gzip')
        self.end_headers()
        if self.path.endswith('slow-body'):
            self.drip([b'x'] * 40)
        else:
            self.wfile.write(b'hello')

    def drip(self, chunks):
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
            time.sleep(0.05)

    def log_message(self, *args):
        pass


@pytest.fixture
def servers(monkeypatch):
    running = []
    resolve = socket.getaddrinfo

    def listener_addresses(host, port, **kwargs):
        # These fixtures listen on IPv4 only. Windows can spend seconds attempting the
        # absent IPv6 localhost listener on every redirect, obscuring the behavior under test.
        if host == 'localhost':
            kwargs['family'] = socket.AF_INET
        return resolve(host, port, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', listener_addresses)

    def start(context=None):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.paths = []
        server.target = '/ok'
        if context:
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        running.append((server, thread))
        return server, f'{"https" if context else "http"}://localhost:{server.server_port}'

    yield start
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_private_destination_denied_and_explicit_host_allowed(servers):
    server, url = servers()
    assert configured_plugin().resolve_web_scheme(url) == -1
    assert server.paths == []
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url) == 200
    assert server.paths == ['/']


@pytest.mark.parametrize('target', ['http://127.0.0.1/', 'http://169.254.169.254/', 'file:///tmp/test'])
def test_redirect_destination_is_rechecked(servers, target):
    server, url = servers()
    server.target = target
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url + '/redirect') == -1
    assert server.paths == ['/redirect']


def test_redirect_body_is_not_downloaded(servers):
    server, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], request_timeout=0.5)
    assert plugin.resolve_web_scheme(url + '/redirect') == 200
    assert server.paths == ['/redirect', '/ok']


def test_redirect_limit(servers):
    server, url = servers()
    server.target = '/redirect'
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url + '/redirect') == -1
    assert len(server.paths) == 6


@pytest.mark.parametrize('path', ['/slow-headers', '/slow-body'])
def test_total_deadline_interrupts_progressing_socket_reads(servers, path):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, request_timeout=0.2)
    start = time.monotonic()
    assert plugin.resolve_web_scheme(url + path) == 504
    assert time.monotonic() - start < 1.5


def test_later_request_has_a_watchdog_when_server_ignores_close(servers):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, request_timeout=0.2)
    assert plugin.resolve_web_scheme(url + '/keepalive') == 200
    start = time.monotonic()
    assert plugin.resolve_web_scheme(url + '/keepalive-slow-body') == 504
    assert time.monotonic() - start < 1.5


def test_body_skipping_is_the_default(servers):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], request_timeout=0.2)
    assert plugin.resolve_web_scheme(url + '/slow-body') == 200


@pytest.mark.parametrize('path', ['/large', '/ok', '/encoded'])
def test_body_budget_and_encoding(servers, path):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, max_download_bytes=4)
    assert plugin.resolve_web_scheme(url + path) == -1


def test_ambient_proxies_and_credentials_are_not_used(monkeypatch, servers, tmp_path):
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


def test_url_credentials_are_rejected(servers):
    server, url = servers()
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(
        url.replace('://', '://user:password@')) == -1
    assert server.paths == []


def test_https_authenticates_certificates_and_hostnames(servers, tmp_path):
    ca = trustme.CA()
    ca_path = tmp_path / 'ca.pem'
    ca.cert_pem.write_to_path(ca_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert('localhost').configure_cert(context)
    _, url = servers(context)
    assert configured_plugin(allow_private_hosts=['localhost']).resolve_web_scheme(url) == -1
    trusted = configured_plugin(allow_private_hosts=['localhost'], ca_bundle=str(ca_path))
    assert trusted.resolve_web_scheme(url) == 200

    wrong_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert('elsewhere.example').configure_cert(wrong_context)
    _, wrong_url = servers(wrong_context)
    assert trusted.resolve_web_scheme(wrong_url) == -1

    bounded = configured_plugin(allow_private_hosts=['localhost'], ca_bundle=str(ca_path),
                                skip_downloads=False, request_timeout=0.2)
    start = time.monotonic()
    assert bounded.resolve_web_scheme(url + '/slow-body') == 504
    assert time.monotonic() - start < 1.5


def test_https_redirect_cannot_downgrade(servers, tmp_path):
    ca = trustme.CA()
    ca_path = tmp_path / 'ca.pem'
    ca.cert_pem.write_to_path(ca_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert('localhost').configure_cert(context)
    server, url = servers(context)
    server.target = 'http://localhost/'
    plugin = configured_plugin(allow_private_hosts=['localhost'], ca_bundle=str(ca_path))
    assert plugin.resolve_web_scheme(url + '/redirect') == -1
    assert server.paths == ['/redirect']


def test_release_actions_are_pinned():
    path = pathlib.Path(__file__).resolve().parents[2] / '.github/workflows/publish.yml'
    actions = re.findall(r'uses:\s*(\S+)', path.read_text())
    assert actions
    assert all(re.fullmatch(r'[^@]+@[0-9a-f]{40}', action) for action in actions)
