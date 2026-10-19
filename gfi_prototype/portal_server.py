"""Local HTML product server with a small read-only API and streamed chat."""
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gfi_prototype.chat import chat_events

def create_server(portal, web_root, host='127.0.0.1', port=8503):
    web_root = web_root.resolve()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def _trusted_request(self):
            port = self.server.server_port
            allowed = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}',
                       f'{host.lower()}:{port}'}
            authority = (self.headers.get('Host') or '').strip().lower()
            if authority not in allowed:
                return False
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlparse(origin)
                return parsed.scheme in {'http', 'https'} and parsed.netloc.lower() in allowed
            return True

        def log_message(self, format, *args):
            # No question bodies, keys, provider output or request URLs in logs.
            return

        def _bytes(self, status, data, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status, value):
            self._bytes(status, json.dumps(value, ensure_ascii=False).encode('utf-8'))

        def do_GET(self):
            if not self._trusted_request():
                self._json(403, {'message': '请通过本地地址打开项目。'})
                return
            parsed = urlparse(self.path)
            args = parse_qs(parsed.query)
            value = lambda key, default='': args.get(key, [default])[0]
            try:
                if parsed.path == '/api/catalog':
                    self._json(200, portal.catalog())
                elif parsed.path == '/api/issues':
                    self._json(200, portal.issues(value('project') or None, value('q'), value('kind', 'task'),
                        value('ready') == 'true', value('label'), int(value('page', '1')), int(value('page_size', '12'))))
                elif parsed.path == '/api/issue':
                    self._json(200, portal.issue(value('project'), int(value('number'))))
                elif parsed.path == '/api/settings':
                    self._json(200, {'model_ready': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
                                     'model': 'DeepSeek Flash'})
                elif parsed.path in {'/', '/index.html', '/app.css', '/app.js'}:
                    path = web_root / ('index.html' if parsed.path == '/' else parsed.path.lstrip('/'))
                    if not path.is_file() or not path.resolve().is_relative_to(web_root):
                        raise KeyError('Not found')
                    self._bytes(200, path.read_bytes(), (mimetypes.guess_type(path.name)[0] or 'application/octet-stream') + '; charset=utf-8')
                else:
                    raise KeyError('Not found')
            except KeyError:
                self._json(404, {'message': '没有找到这项内容，请返回列表重新选择。'})
            except (ValueError, TypeError):
                self._json(400, {'message': '筛选条件不正确，请重置后再试。'})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                self._json(500, {'message': '读取内容时遇到问题，请稍后重试。'})

        def do_POST(self):
            if urlparse(self.path).path != '/api/chat':
                self._json(404, {'message': '没有找到这个操作。'})
                return
            if not self._trusted_request():
                self._json(403, {'message': '请从本地项目页面使用助手。'})
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    self._json(413, {'message': '问题过长，请精简后重试。'})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid payload')
                question = payload.get('question')
                if not isinstance(question, str) or not 0 < len(question.strip()) <= 2000:
                    raise ValueError('Invalid question')
                if type(payload.get('number')) is not int or not isinstance(payload.get('repo'), str):
                    raise ValueError('Invalid identity')
                if payload.get('mode', 'local') not in {'model', 'local'}:
                    raise ValueError('Invalid mode')
                if not isinstance(payload.get('api_key', ''), str) or len(payload.get('api_key', '')) > 512:
                    raise ValueError('Invalid credential')
                history = payload.get('history', [])
                if not isinstance(history, list):
                    raise ValueError('Invalid history')
                payload['history'] = [{'role': m['role'], 'text': m['text'][:1500]} for m in history[-8:]
                    if isinstance(m, dict) and m.get('role') in {'user', 'assistant'} and isinstance(m.get('text'), str)]
                portal.issue(payload['repo'], payload['number'])
                if payload.get('mode') == 'model' and not (payload.get('api_key', '').strip() or os.environ.get('DEEPSEEK_API_KEY', '').strip()):
                    self._json(422, {'message': '请先在模型设置中填写 API Key，再发送问题。'})
                    return
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {'message': '请输入有效的问题，并从任务页面打开助手。'})
                return
            except KeyError:
                self._json(404, {'message': '当前任务不存在，请从列表重新选择。'})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.close_connection = True
            try:
                for kind, data in chat_events(portal, payload):
                    encoded = f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode('utf-8')
                    self.wfile.write(encoded)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return ThreadingHTTPServer((host, port), Handler)
