"""Check the actual gateway boundary and response headers, without touching app data."""
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def fetch(base, path, headers=None):
    try:
        response = urlopen(Request(base + path, headers=headers or {}), timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def main(base):
    for path in ('/', '/config.js', '/app.js'):
        status, headers, body = fetch(base, path)
        assert status == 200, (path, status)
        assert "frame-ancestors 'none'" in headers.get('Content-Security-Policy', ''), path + ': missing CSP'
        assert headers.get('Referrer-Policy') == 'no-referrer'
        assert headers.get('X-Content-Type-Options') == 'nosniff'
        assert headers.get('Cache-Control') == 'no-store'
    assert fetch(base, '/api/v1/session', {'Origin': 'https://attacker.invalid'})[0] == 403
    assert fetch(base, '/health', {'Host': 'attacker.invalid'})[0] == 403
    assert fetch(base, '/api/v1/session', {'Host': 'attacker.invalid', 'Origin': 'http://attacker.invalid'})[0] == 403
    assert fetch(base, '/config.js')[2].find(b"mode: 'server'") >= 0
    assert fetch(base, '/api/bootstrap')[0] == 404  # Never silently use the standalone demo API.
    print('PASS: gateway headers, Host/Origin restrictions, server config, no demo API fallback')


if __name__ == '__main__':
    main(sys.argv[1].rstrip('/'))
