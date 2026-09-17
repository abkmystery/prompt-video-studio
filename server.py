from __future__ import annotations
import argparse
import hmac
import json
import mimetypes
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from studio.codex_bridge import CodexBridge
from studio.jobs import JobManager
from studio.common import safe_error

ROOT = Path(__file__).resolve().parent
FILES = {'video.mp4', 'thumbnail.png', 'project.blend', 'project.zip', 'plan.json', 'manifest.json', 'script.md', 'credits.md', 'captions.srt', 'audio-info.json', 'render-info.json', 'app-verification.json'}

class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, root=ROOT, bridge=None):
        self.root = root
        self.csrf = secrets.token_urlsafe(32)
        self.codex = bridge if bridge is not None else CodexBridge(root)
        self.manager = JobManager(root, self.codex)
        self.status_lock = threading.Lock()
        self.status_cache = (0, {})
        super().__init__(address, Handler)

    def codex_status(self):
        with self.status_lock:
            now = time.monotonic()
            if now - self.status_cache[0] > 5:
                try:
                    value = self.codex.status()
                except Exception as exc:
                    value = {'available':False, 'connected':False, 'error':safe_error(exc)}
                self.status_cache = (now, value)
            return self.status_cache[1]

class Handler(BaseHTTPRequestHandler):
    server_version = 'PromptVideoStudio/0.2.0'
    def log_message(self, *args):
        # No request body, prompt or credential logging.
        pass

    def _allowed(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in hosts and (not origin or origin in {f'http://{h}' for h in hosts}) and self.headers.get('Sec-Fetch-Site') != 'cross-site'

    def _headers(self, code, content_type, length=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _json(self, value, code=200):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self._headers(code, 'application/json; charset=utf-8', len(body))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._allowed():
            return self._json({'error':'Only requests from this local app are allowed.'}, 403)
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/api/health':
                return self._json({'app':'Prompt Video Studio', 'version':'0.2.0'})
            if path == '/api/status':
                return self._json({'csrf_token':self.server.csrf, 'output_dir':str(self.server.manager.outputs),
                                   'capabilities':self.server.manager.capabilities(), 'codex':self.server.codex_status()})
            if path == '/api/jobs':
                return self._json({'jobs':self.server.manager.list()})
            if path == '/api/assets':
                return self._json({'assets':self.server.manager.library.list()})
            match = re.fullmatch(r'/api/assets/([a-f0-9]{16})', path)
            if match:
                return self._json(self.server.manager.library.get(match[1]))
            if path == '/api/imports':
                return self._json({'imports':self.server.manager.imports.list()})
            match = re.fullmatch(r'/api/imports/([a-f0-9]{16})', path)
            if match:
                return self._json(self.server.manager.imports.get(match[1]))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{16})', path)
            if match:
                return self._json(self.server.manager.get(match[1]))
            match = re.fullmatch(r'/outputs/([a-f0-9]{16})/([a-zA-Z0-9.-]+)', path)
            if match and match[2] in FILES:
                file = self.server.manager.outputs / match[1] / match[2]
                if file.is_file() and file.resolve().is_relative_to(self.server.manager.outputs.resolve()):
                    return self._file(file, download=match[2].endswith(('.blend','.zip','.srt','.json','.md')))
            match = re.fullmatch(r'/library/([a-f0-9]{16})/([a-zA-Z0-9.-]+)', path)
            if match:
                return self._file(self.server.manager.library.file(match[1],match[2]),download=match[2].endswith(('.blend','.glb','.md')))
            match = re.fullmatch(r'/imports/([a-f0-9]{16})/([a-zA-Z0-9.-]+)', path)
            if match:
                return self._file(self.server.manager.imports.file(match[1],match[2]),download=True)
            if path in {'/','/index.html','/styles.css','/features.css','/app.js','/app-v2.js'}:
                file = self.server.root / 'web' / ('index.html' if path == '/' else path[1:])
                if file.is_file():
                    return self._file(file)
            self._json({'error':'Not found.'},404)
        except KeyError:
            self._json({'error':'Item not found.'},404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self._json({'error':safe_error(exc)},500)

    def _file(self, file, download=False):
        size = file.stat().st_size
        start, end = 0, size - 1
        code = 200
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)',header)
            if not match or not any(match.groups()):
                return self._range_error(size)
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]),size-1) if match[2] else size-1
            else:
                start = max(0,size-int(match[2]))
            if start > end or start >= size:
                return self._range_error(size)
            code = 206
        mime = 'application/octet-stream' if file.suffix == '.blend' else mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
        self._headers(code,mime,end-start+1)
        self.send_header('Accept-Ranges','bytes')
        if code == 206:
            self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        if download:
            self.send_header('Content-Disposition',f'attachment; filename="{file.name}"')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with file.open('rb') as stream:
            stream.seek(start)
            remaining = end-start+1
            while remaining > 0:
                chunk = stream.read(min(256*1024,remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _range_error(self,size):
        self._headers(416,'text/plain',0)
        self.send_header('Content-Range',f'bytes */{size}')
        self.end_headers()

    def do_POST(self):
        if not self._allowed() or not hmac.compare_digest(self.headers.get('X-Studio-Token',''),self.server.csrf):
            return self._json({'error':'Refresh the app before trying again.'},403)
        try:
            raw_length = self.headers.get('Content-Length')
            if raw_length is None or not re.fullmatch(r'\d+',raw_length):
                return self._json({'error':'A valid Content-Length is required.'},411)
            length = int(raw_length)
            split = urlsplit(self.path)
            path = split.path
            if path == '/api/imports':
                if self.headers.get('Transfer-Encoding'):
                    return self._json({'error':'Chunked uploads are not supported.'},400)
                query = parse_qs(split.query,keep_blank_values=True)
                kind = query.get('kind',[''])[0]
                name = query.get('name',[''])[0]
                limit = self.server.manager.imports and {'video':512*1024*1024,'script':1024*1024}.get(kind)
                if not limit or not 1 <= length <= limit:
                    return self._json({'error':'The upload is empty, too large or has an invalid kind.'},413)
                self.connection.settimeout(60)
                return self._json(self.server.manager.imports.save(self.rfile,length,kind,name),201)
            if self.headers.get_content_type() != 'application/json':
                return self._json({'error':'Expected JSON.'},415)
            if not 1 <= length <= 32768:
                return self._json({'error':'Request is empty or too large.'},413)
            self.connection.settimeout(15)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('Expected a JSON object.')
            if path == '/api/shutdown':
                self._json({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if path == '/api/jobs':
                return self._json(self.server.manager.submit(body),202)
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{16})/(cancel|resume)',path)
            if match:
                result = self.server.manager.cancel(match[1]) if match[2] == 'cancel' else self.server.manager.resume(match[1],body.get('api_key'))
                return self._json(result)
            approval = re.fullmatch(r'/api/approvals/([a-zA-Z0-9_-]+)',path)
            if approval:
                if type(body.get('approved')) is not bool:
                    raise ValueError('Choose Approve or Decline for this action.')
                self.server.codex.resolve_approval(approval[1],body['approved'])
                return self._json({'ok':True})
            if path == '/api/codex/login':
                result = self.server.codex.login()
                self.server.status_cache = (0,{})
                return self._json(result)
            if path == '/api/codex/cancel':
                login_id = body.get('login_id')
                if not isinstance(login_id,str) or not 1 <= len(login_id) <= 200:
                    raise ValueError('Invalid login session.')
                self.server.codex.cancel_login(login_id)
                self.server.status_cache = (0,{})
                return self._json({'ok':True})
            self._json({'error':'Not found.'},404)
        except KeyError:
            self._json({'error':'Item not found.'},404)
        except (ValueError, TypeError) as exc:
            self._json({'error':safe_error(exc)},400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self._json({'error':safe_error(exc)},500)

    def do_OPTIONS(self):
        self._json({'error':'Cross-origin access is disabled.'},403)


def main():
    parser = argparse.ArgumentParser(description='Run Prompt Video Studio locally.')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--open',action='store_true')
    args = parser.parse_args()
    server = StudioServer(('127.0.0.1',args.port))
    url = f'http://127.0.0.1:{args.port}'
    print(f'Prompt Video Studio: {url}',flush=True)
    print(f'Videos: {server.manager.outputs}',flush=True)
    if args.open:
        threading.Timer(.7,webbrowser.open,args=(url,)).start()
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.close()
        server.server_close()
        server.codex.close()

if __name__ == '__main__':
    main()
