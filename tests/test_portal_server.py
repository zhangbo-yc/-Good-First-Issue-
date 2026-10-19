import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from gfi_prototype.portal import Portal
from gfi_prototype.portal_server import create_server, chat_events


class PortalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.portal = Portal(cls.root)
        cls.temp = tempfile.TemporaryDirectory()
        cls.web = Path(cls.temp.name)
        (cls.web / 'index.html').write_text('<html>新人任务门户</html>', encoding='utf-8')
        cls.server = create_server(cls.portal, cls.web, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        data = json.dumps(body).encode() if body is not None else None
        connection.request('POST' if body is not None else 'GET', path, data,
                           headers or {'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, response.getheader('Content-Type'), response.read().decode('utf-8')
        connection.close()
        return result

    def test_catalog_static_and_task_routes(self):
        status, _, body = self.request('/api/catalog')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['totals']['tasks'], 945)
        self.assertEqual(self.request('/')[0], 200)
        status, _, body = self.request('/api/issues?project=dask%2Fdask&page_size=2')
        self.assertEqual(len(json.loads(body)['items']), 2)
        status, _, body = self.request('/api/issue?project=ManimCommunity%2Fmanim&number=4983')
        self.assertEqual(json.loads(body)['state'], 'open')

    def test_static_confinement_and_bad_request(self):
        self.assertEqual(self.request('/../README.md')[0], 404)
        self.assertEqual(self.request('/api/issue?project=missing&number=1')[0], 404)
        self.assertEqual(self.request('/api/issues?page_size=1000')[0], 400)
        self.assertEqual(self.request('/api/issues?page=abc')[0], 400)

    def test_local_chat_stream_has_tokens_and_completion(self):
        status, content_type, body = self.request('/api/chat', {'repo': 'ManimCommunity/manim',
            'number': 4983, 'question': '为什么推荐下一个任务？', 'mode': 'local', 'history': []})
        self.assertEqual(status, 200)
        self.assertIn('text/event-stream', content_type)
        self.assertIn('event: token', body)
        self.assertIn('event: done', body)
        self.assertNotIn('event: error', body)

    def test_model_missing_key_empty_question_and_foreign_origin(self):
        body = {'repo': 'ManimCommunity/manim', 'number': 4983, 'question': '问题', 'mode': 'model'}
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': ''}):
            self.assertEqual(self.request('/api/chat', body)[0], 422)
        body['question'] = ''
        self.assertEqual(self.request('/api/chat', body)[0], 400)
        self.assertEqual(self.request('/api/chat', body,
            {'Content-Type': 'application/json', 'Origin': 'https://elsewhere.example'})[0], 403)

    def test_untrusted_host_cannot_authorize_its_own_origin(self):
        body = {'repo': 'ManimCommunity/manim', 'number': 4983, 'question': '怎么开始？', 'mode': 'local'}
        status, _, _ = self.request('/api/chat', body, {'Content-Type': 'application/json',
            'Host': 'attacker.example', 'Origin': 'http://attacker.example'})
        self.assertEqual(status, 403)

    def test_fake_model_stream_and_failure_do_not_leak_exceptions_or_keys(self):
        class FakeModel:
            def stream(self, messages):
                yield type('Token', (), {'content': '这是一段中文回答。'})()
                raise RuntimeError('secret-provider-key')
        payload = {'repo': 'ManimCommunity/manim', 'number': 4983, 'question': '怎么开始？',
                   'mode': 'model', 'api_key': 'fake-key-not-sent'}
        events = list(chat_events(self.portal, payload, model_factory=lambda key: FakeModel()))
        self.assertTrue(any(kind == 'token' for kind, data in events))
        self.assertEqual(events[-1][0], 'error')
        self.assertNotIn('secret-provider-key', str(events))
        self.assertNotIn('fake-key-not-sent', str(events))


if __name__ == '__main__':
    unittest.main()
