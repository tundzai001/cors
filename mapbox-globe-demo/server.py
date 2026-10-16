"""Local static preview with a public Mapbox token loaded from .env."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import json

ROOT = Path(__file__).resolve().parent


def public_token():
    for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
        key, separator, value = line.strip().partition('=')
        if separator and key == 'MAPBOX_PUBLIC_TOKEN':
            token = value.strip().strip('\"\'')
            if token.startswith('pk.'):
                return token
    raise ValueError('MAPBOX_PUBLIC_TOKEN must contain a public pk. token')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if any(part.startswith('.') for part in path.split('/') if part):
            self.send_error(404)
        elif path == '/config.js':
            try:
                body = ('export default ' + json.dumps(public_token()) + ';\n').encode()
            except (OSError, ValueError):
                body = b"export default '';\n"
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_HEAD(self):
        path = unquote(urlsplit(self.path).path)
        if any(part.startswith('.') for part in path.split('/') if part):
            self.send_error(404)
        else:
            super().do_HEAD()


if __name__ == '__main__':
    print('Mapbox preview: http://127.0.0.1:8766', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), Handler).serve_forever()
