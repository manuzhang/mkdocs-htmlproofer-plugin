from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import threading
import time

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
    # HTTPServer reverse-resolves its bind address for a display name. The fixture needs
    # no external DNS, and hosted macOS resolvers can stall even on loopback PTR queries.
    monkeypatch.setattr(socket, 'getfqdn', lambda host='': host or 'localhost')

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
