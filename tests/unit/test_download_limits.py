import time

import pytest

pytestmark = pytest.mark.timeout(10)


def test_redirect_body_is_not_downloaded(configured_plugin, servers):
    server, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], request_timeout=0.5)
    assert plugin.resolve_web_scheme(url + '/redirect') == 200
    assert server.paths == ['/redirect', '/ok']


@pytest.mark.parametrize('path', ['/slow-headers', '/slow-body'])
def test_total_deadline_interrupts_progressing_socket_reads(configured_plugin, servers, path):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, request_timeout=0.2)
    start = time.monotonic()
    assert plugin.resolve_web_scheme(url + path) == 504
    assert time.monotonic() - start < 1.5


def test_later_request_has_a_watchdog_when_server_ignores_close(configured_plugin, servers):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, request_timeout=0.2)
    assert plugin.resolve_web_scheme(url + '/keepalive') == 200
    start = time.monotonic()
    assert plugin.resolve_web_scheme(url + '/keepalive-slow-body') == 504
    assert time.monotonic() - start < 1.5


def test_body_skipping_is_the_default(configured_plugin, servers):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], request_timeout=0.2)
    assert plugin.resolve_web_scheme(url + '/slow-body') == 200


@pytest.mark.parametrize('path', ['/large', '/ok', '/encoded'])
def test_body_budget_and_encoding(configured_plugin, servers, path):
    _, url = servers()
    plugin = configured_plugin(allow_private_hosts=['localhost'], skip_downloads=False, max_download_bytes=4)
    assert plugin.resolve_web_scheme(url + path) == -1
