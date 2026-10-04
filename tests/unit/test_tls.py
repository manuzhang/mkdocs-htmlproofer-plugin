import ssl
import time

import pytest
import trustme

pytestmark = pytest.mark.timeout(10)


def test_https_authenticates_certificates_and_hostnames(configured_plugin, servers, tmp_path):
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


def test_https_redirect_cannot_downgrade(configured_plugin, servers, tmp_path):
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
