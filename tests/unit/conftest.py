from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

import pytest

from htmlproofer.plugin import HtmlProoferPlugin


@pytest.fixture
def configured_plugin():
    def create(**config):
        plugin = HtmlProoferPlugin()
        errors, _ = plugin.load_config(config)
        assert not errors
        return plugin
    return create


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        self.server.paths.append(self.path)
        self.server.peers.append(self.client_address)
        try:
            if self.path.startswith('/redirect'):
                self.send_response(302)
                self.send_header('Location', self.server.target)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'hello')
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def servers(monkeypatch):
    running = []
    resolve = socket.getaddrinfo

    def listener_addresses(host, port, family=0, type=0, proto=0, flags=0):
        # These fixtures listen on IPv4 only. Match getaddrinfo's positional signature too.
        if host == 'localhost':
            family = socket.AF_INET
        return resolve(host, port, family, type, proto, flags)

    monkeypatch.setattr(socket, 'getaddrinfo', listener_addresses)
    monkeypatch.setattr(socket, 'getfqdn', lambda host='': host or 'localhost')

    def start(handler=Handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        server.paths = []
        server.peers = []
        server.target = '/ok'
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        running.append((server, thread))
        return server, f'http://localhost:{server.server_port}'

    yield start
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
