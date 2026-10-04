import socket
from unittest.mock import patch

import pytest
import requests
from urllib3.connection import HTTPConnection

from htmlproofer.network import CheckedAdapter, connect_checked, public_address

pytestmark = pytest.mark.timeout(10)


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
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=[address_info(ip)]), \
            patch('htmlproofer.network.socket.socket') as sock:
        with pytest.raises(requests.exceptions.InvalidURL):
            connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
        sock.assert_not_called()


def test_mixed_public_and_private_dns_answers_are_rejected():
    adapter = CheckedAdapter([])
    answers = [address_info('8.8.8.8'), address_info('127.0.0.1')]
    with patch('htmlproofer.network.socket.getaddrinfo', return_value=answers), \
            patch('htmlproofer.network.socket.socket') as sock:
        with pytest.raises(requests.exceptions.InvalidURL):
            connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
        sock.assert_not_called()


def test_connection_uses_inspected_address_without_resolving_again():
    adapter = CheckedAdapter([])
    # A second DNS lookup would return loopback. There must only be one lookup.
    with patch('htmlproofer.network.socket.getaddrinfo',
               side_effect=[[address_info('8.8.8.8')], [address_info('127.0.0.1')]]) as dns, \
            patch('htmlproofer.network.socket.socket') as sock:
        connection = HTTPConnection('selected.example', timeout=1.0)
        connect_checked(connection, adapter)
        dns.assert_called_once()
        sock.return_value.connect.assert_called_once_with(('8.8.8.8', 80))
        assert connection.host == 'selected.example'


def test_public_address_fallback_after_connect_timeout():
    adapter = CheckedAdapter([])
    with patch('htmlproofer.network.socket.getaddrinfo',
               return_value=[address_info('8.8.8.8'), address_info('1.1.1.1')]), \
            patch('htmlproofer.network.socket.socket') as sock:
        sock.return_value.connect.side_effect = [socket.timeout(), None]
        connect_checked(HTTPConnection('selected.example', timeout=1.0), adapter)
        assert [call.args for call in sock.return_value.connect.call_args_list] == [
            (('8.8.8.8', 80),), (('1.1.1.1', 80),)]


@pytest.mark.parametrize('ip', ['8.8.8.8', '2606:4700:4700::1111', '::ffff:8.8.8.8'])
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
