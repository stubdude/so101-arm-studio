"""Loopback-only dashboard API. Browser writes require an origin check and token."""
import argparse
import json
import mimetypes
import secrets
import signal
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import engine as engine_module
from engine import Engine, DATA, SPEED_DEFAULTS, SPEED_MAX, SPEED_MIN
from cameras import Cameras

ROOT = Path(__file__).resolve().parent
MODULES = [
 {'id':'operate','name':'Operate','available':True,'description':'Live joint control and robot health'},
 {'id':'sessions','name':'Motion sessions','available':True,'description':'Joint-only recording and export'},
 {'id':'cameras','name':'Cameras','available':True,'description':'Optional front and wrist preview profiles'},
 {'id':'learning','name':'Learning','available':False,'description':'Future synchronized LeRobot datasets, training jobs and policy evaluation'},
 {'id':'settings','name':'Settings','available':True,'description':'Saved robot profile and calibration'}]


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, engine, demo=False):
        super().__init__(address, Handler)
        self.engine, self.cameras, self.demo = engine, Cameras(engine.data), demo
        self.token = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server_version = 'SO101Console/1.0'
    def log_message(self, *args):
        pass

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def send_bytes(self, code, data, content_type='application/json', headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, code, obj):
        self.send_bytes(code, json.dumps(obj).encode())

    def do_GET(self):
        if not self.allowed_host():
            return self.json(403, {'error':'Host not allowed.'})
        path = urlparse(self.path).path
        try:
            if path == '/api/bootstrap':
                return self.json(200, {'token':self.server.token,'modules':MODULES,'demo':self.server.demo,
                    'speeds':{'defaults':SPEED_DEFAULTS,'max':SPEED_MAX,'min':SPEED_MIN}})
            if path == '/api/state':
                state = self.server.engine.snapshot()
                state['cameras'] = self.server.cameras.snapshot()
                return self.json(200, state)
            if path.startswith('/api/camera/') and path.endswith('.jpg'):
                role = path.split('/')[-1][:-4]
                with self.server.cameras.lock:
                    frame = self.server.cameras.frames.get(role)
                return self.send_bytes(200, frame, 'image/jpeg') if frame else self.json(404, {'error':'No preview frame.'})
            if path.startswith('/api/session/'):
                ident = path.split('/')[-1]
                entries = self.server.engine.sessions()
                if not any(s['id'] == ident for s in entries):
                    return self.json(404, {'error':'Session not found.'})
                data = (self.server.engine.data / 'sessions' / (ident + '.jsonl')).read_bytes()
                return self.send_bytes(200, data, 'application/x-ndjson', {'Content-Disposition':f'attachment; filename="{ident}.jsonl"'})
            target = (ROOT / 'static' / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
            if not target.is_relative_to(ROOT / 'static') or not target.is_file():
                return self.json(404, {'error':'Not found.'})
            return self.send_bytes(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        except (OSError, ValueError) as exc:
            self.json(500, {'error':str(exc)})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.allowed_host() or origin not in allowed or self.headers.get('X-Control-Token') != self.server.token:
            return self.json(403, {'error':'This action must come from the local dashboard.'})
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.json(415, {'error':'JSON required.'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 <= length <= 16384:
                return self.json(413, {'error':'Request too large.'})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Expected an object.')
            action = urlparse(self.path).path.removeprefix('/api/')
            if action == 'heartbeat':
                import time
                self.server.engine.heartbeat = time.monotonic()
                return self.json(200, {'ok':True})
            if action == 'camera_save':
                self.server.cameras.save(body)
            elif action == 'camera_start':
                self.server.cameras.start(body.get('role'))
            elif action == 'camera_stop':
                self.server.cameras.stop(body.get('role'))
            else:
                self.server.engine.submit(action, body)
            return self.json(202, {'ok':True})
        except (ValueError, KeyError, TypeError) as exc:
            self.json(400, {'error':str(exc)})
        except Exception as exc:
            self.json(409, {'error':str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--demo',action='store_true',help='Use simulated arms; no USB access.')
    args = parser.parse_args()
    if args.demo:
        from simulator import fake_factory, fake_ports, SERIALS
        engine_module.LOCK = DATA / 'demo' / 'hardware.lock'
        engine = Engine(DATA / 'demo', fake_factory, fake_ports)
        for role in ('leader', 'follower'):
            engine.profile[role]['serial'] = SERIALS[role]
    else:
        engine = Engine()
    try:
        server = AppServer(('127.0.0.1',args.port),engine,args.demo)
    except OSError:
        print(f'Port {args.port} is already in use. If this dashboard is running, open http://127.0.0.1:{args.port}',flush=True)
        raise SystemExit(1)
    engine.start_worker()
    def stop(*_):
        engine.closed.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    url = f'http://127.0.0.1:{server.server_port}'
    print(('SO-101 dashboard (DEMO, simulated arms): ' if args.demo else 'SO-101 dashboard: ') + url,flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        engine.closed.set()
        for role in ('front','wrist'):
            server.cameras.stop(role)
        if engine.thread:
            engine.thread.join(timeout=8)
        server.server_close()


if __name__ == '__main__':
    main()
