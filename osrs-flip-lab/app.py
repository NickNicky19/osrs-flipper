"""Run: python app.py --mode demo --open
Local research app, not a public hosting server. Collection is a separate opt-in command.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import webbrowser
from urllib.parse import urlsplit, parse_qs
from core import Ledger, ValidationError, scan_fixture, summary, utc_now
from market import strict_json
from service import ResearchService

ROOT = Path(__file__).resolve().parent


def make_server(ledger: Ledger, port: int = 8765, research=None) -> ThreadingHTTPServer:
    research = research or ResearchService(ledger, ROOT)
    token = secrets.token_urlsafe(32)
    fixture = json.loads((ROOT / 'fixtures' / 'market.json').read_text())

    class Handler(BaseHTTPRequestHandler):
        def allowed_host(self) -> bool:
            actual = self.server.server_address[1]
            return self.headers.get('Host') in {f'127.0.0.1:{actual}', f'localhost:{actual}'}

        def respond(self, status: int, data: bytes, content_type='application/json', download=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def json_response(self, status: int, payload: dict):
            self.respond(status, json.dumps(payload, allow_nan=False).encode())

        def do_GET(self):
            if not self.allowed_host():
                return self.json_response(403, {'error': 'Only the local hostname is allowed.'})
            path = urlsplit(self.path).path
            if path == '/':
                text = (ROOT / 'static' / 'index.html').read_text().replace('__TOKEN__', token)
                return self.respond(200, text.encode(), 'text/html; charset=utf-8')
            if path in ('/app.js', '/research.js', '/style.css'):
                mime = 'text/javascript' if path.endswith('js') else 'text/css'
                return self.respond(200, (ROOT / 'static' / path[1:]).read_bytes(), mime)
            if path == '/api/state':
                state = ledger.get()
                return self.json_response(200, dict(mode=ledger.mode, recorded_at=utc_now(), state=state,
                    summary=summary(state), opportunities=scan_fixture(fixture, state) if ledger.mode == 'demo' else [],
                    fixture_label=fixture['label'], fixture_as_of=fixture['as_of'],
                    source_status='FIXTURE_ONLY' if ledger.mode == 'demo' else 'LIVE_SOURCE_NOT_VERIFIED'))
            if path in ('/api/research','/api/evidence','/api/market/export'):
                try:
                    if path == '/api/research':
                        params=parse_qs(urlsplit(self.path).query)
                        window=int(params.get('window',['3600'])[0])
                        return self.json_response(200,research.report(window))
                    if path == '/api/evidence':
                        return self.json_response(200,research.evidence())
                    return self.respond(200,json.dumps(research.store.export(),indent=2).encode(),
                                        download=f'osrs-research-{ledger.mode}-export.json')
                except (ValidationError,ValueError,KeyError,TypeError,OSError) as exc:
                    return self.json_response(400,{'error':str(exc)})
            if path == '/api/export':
                return self.respond(200, json.dumps(ledger.export(), indent=2).encode(),
                                    download=f'osrs-flip-lab-{ledger.mode}-backup.json')
            return self.json_response(404, {'error': 'Not found.'})

        def do_POST(self):
            actual = self.server.server_address[1]
            origin = self.headers.get('Origin')
            if (not self.allowed_host() or self.headers.get('X-Local-Token') != token
                    or origin not in (None, f'http://127.0.0.1:{actual}', f'http://localhost:{actual}')):
                return self.json_response(403, {'error': 'Local request verification failed.'})
            if self.path not in ('/api/event','/api/scenario','/api/research/freeze'):
                return self.json_response(404, {'error': 'Not found.'})
            if self.headers.get_content_type() != 'application/json':
                return self.json_response(415, {'error': 'JSON required.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 100_000:
                    raise ValidationError('Invalid request size.')
                data = strict_json(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValidationError('A JSON object is required.')
                if self.path == '/api/scenario':
                    return self.json_response(200,research.scenario(data))
                if self.path == '/api/research/freeze':
                    return self.json_response(200,research.freeze(data))
                research.validate_link(data)
                state = ledger.record(data)
                self.json_response(200, {'state': state, 'summary': summary(state)})
            except (ValidationError, ValueError, TypeError, KeyError) as exc:
                self.json_response(400, {'error': str(exc)})
            except Exception:
                self.json_response(500, {'error': 'Local operation failed. No successful change is claimed; check the database before retrying.'})

        def log_message(self, format, *args):
            # No account details or form bodies in logs.
            return

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server


def main():
    parser = argparse.ArgumentParser(description='OSRS Flip Lab: independent, manual, local starter')
    parser.add_argument('--mode', choices=['demo', 'journal'], default='demo')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--starting-cash', type=int, default=500_000_000)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--market-database',type=Path,help='Public market store populated by collector.py')
    parser.add_argument('--item-rules',type=Path,help='Explicit reviewed item-rule registry')
    parser.add_argument('--open', action='store_true', help='Open the local website in your browser')
    parser.add_argument('--restore', type=Path, help='Restore a backup into a NEW --database path and exit')
    args = parser.parse_args()
    if args.restore:
        if args.database is None:
            parser.error('--restore requires a new --database destination')
        try:
            Ledger.restore(args.database, json.loads(args.restore.read_text()))
        except (ValidationError, ValueError, KeyError, OSError) as exc:
            parser.error(str(exc))
        print(f'Restored and replay-verified: {args.database}')
        return
    database = args.database or ROOT / 'data' / f'{args.mode}.sqlite3'
    try:
        ledger = Ledger(database, args.mode, args.starting_cash)
        server = make_server(ledger, args.port, ResearchService(ledger, ROOT, args.market_database, args.item_rules))
    except (OSError, ValidationError) as exc:
        parser.error(str(exc))
    url = f'http://127.0.0.1:{server.server_address[1]}'
    print(f'OSRS Flip Lab — {args.mode.upper()} — {url}', flush=True)
    print(f'Database: {database.resolve()}', flush=True)
    print('No vendor login. No game interaction. The website reads saved data only; collector.py is a separate opt-in command. Ctrl+C stops the app.', flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
