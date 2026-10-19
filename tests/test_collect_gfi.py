import importlib.util
import io
import json
import stat
import tempfile
import threading
import time
import unittest
import warnings
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'collect_gfi.py'


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('collector', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertIsNotNone(self.module, 'collect_gfi.py has not been implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_duplicate_credentials_share_one_budget(self):
        credentials = self.module.credentials_from_env({'GITHUB_TOKEN_1': 'same', 'GITHUB_TOKEN_2': 'same'})
        self.assertEqual(len(credentials), 1)
        self.assertNotIn('same', repr(credentials[0]))

    def test_malformed_credentials_are_rejected_without_echoing_secret(self):
        for token in ['private-value\nHeader: x', 'private-value\r', 'private-value 中文']:
            with self.assertRaises(ValueError) as caught:
                self.module.Credential(token)
            self.assertNotIn('private-value', str(caught.exception))

    def test_exception_details_are_redacted_before_persistence(self):
        client = self.fake_client(True)
        client.credential = self.module.Credential('secret-value', 0)
        def fail(path):
            raise ValueError('network failure for secret-value')
        client.get = fail
        result = self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False)
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertNotIn('secret-value', (self.root / 'sympy__sympy/manifest.json').read_text())

    def test_hidden_prompt_refuses_echo_fallback(self):
        from gfi_prototype import common
        self.assertTrue(hasattr(self.module, 'read_token'), 'safe hidden-input reader missing')
        continued = []
        def fallback(prompt):
            warnings.warn('Cannot hide input', common.getpass.GetPassWarning)
            continued.append(True)
            return 'would-be-echoed'
        with patch.object(common.getpass, 'getpass', fallback):
            with self.assertRaises(RuntimeError):
                self.module.read_token('Token: ')
        self.assertEqual(continued, [])

    def test_pagination_preserves_every_page_and_rejects_external_next(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(inner):
                calls.append(inner.path)
                inner.send_response(200)
                if inner.path == '/items':
                    inner.send_header('Link', f'<http://127.0.0.1:{inner.server.server_port}/items?page=2>; rel="next"')
                if inner.path == '/external':
                    inner.send_header('Link', '<https://example.com/steal>; rel="next"')
                inner.end_headers()
                inner.wfile.write(json.dumps([{'page': 2 if '?' in inner.path else 1}]).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = self.module.GitHubClient(self.module.Credential('test-secret', 0), api_base=f'http://127.0.0.1:{server.server_port}')
        self.assertEqual(client.pages('/items'), [{'page': 1}, {'page': 2}])
        with self.assertRaises(ValueError):
            client.pages('/external')
        self.assertEqual(calls, ['/items', '/items?page=2', '/external'])

    def fake_client(self, fail_timeline=False):
        class Client:
            timeline_calls = 0
            def get(self, path):
                return {'full_name': 'sympy/sympy', 'default_branch': 'master'}
            def pages(inner, path, params=None):
                if path.endswith('/issues'):
                    return [
                        {'number': 10, 'updated_at': '2026-10-01', 'state': 'open', 'title': 'real', 'comments': 1, 'labels': [{'name': 'Easy to Fix'}]},
                        {'number': 11, 'pull_request': {}, 'state': 'open'},
                    ]
                if path.endswith('/comments'):
                    return [{'id': 7, 'body': 'context'}]
                inner.timeline_calls += 1
                if fail_timeline:
                    raise RuntimeError('temporary failure')
                return [{'event': 'labeled'}]
        return Client()

    def test_label_union_filters_prs_deduplicates_and_exports(self):
        result = self.module.collect_project(self.module.PROJECTS[0], self.fake_client(), self.root, source=False)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['issue_count'], 1)
        folder = self.root / 'sympy__sympy'
        bundle = json.loads((folder / 'issues/10.json').read_text(encoding='utf-8'))
        self.assertEqual(bundle['comments'][0]['body'], 'context')
        self.assertEqual(len((folder / 'issues.jsonl').read_text(encoding='utf-8').splitlines()), 1)
        self.assertFalse((folder / 'issues/11.json').exists())

    def test_resume_skips_complete_issues_and_refresh_fetches_again(self):
        client = self.fake_client()
        self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False)
        self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False)
        self.assertEqual(client.timeline_calls, 1)
        self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False, refresh=True)
        self.assertEqual(client.timeline_calls, 2)

    def test_changed_issue_payload_invalidates_checkpoint(self):
        client = self.fake_client()
        self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False)
        path = self.root / 'sympy__sympy/issues/10.json'
        bundle = json.loads(path.read_text(encoding='utf-8'))
        bundle['issue']['updated_at'] = 'old'
        path.write_text(json.dumps(bundle), encoding='utf-8')
        self.module.collect_project(self.module.PROJECTS[0], client, self.root, source=False)
        self.assertEqual(client.timeline_calls, 2)

    def test_partial_issue_failure_is_not_reported_complete_and_can_resume(self):
        result = self.module.collect_project(self.module.PROJECTS[0], self.fake_client(True), self.root, source=False)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['failed_issue_count'], 1)
        result = self.module.collect_project(self.module.PROJECTS[0], self.fake_client(), self.root, source=False)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['issue_count'], 1)

    def test_safe_zip_rejects_traversal_and_extracts_valid_source(self):
        for bad in ['repo/../../escape.py', 'repo/C:/escape.py', 'repo/..\\escape.py']:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr(bad, 'bad')
            data.seek(0)
            with self.assertRaises(ValueError):
                self.module.extract_source(data, self.root / 'unsafe')
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('repo-123/pkg/__init__.py', 'x = 1\n')
        data.seek(0)
        self.module.extract_source(data, self.root / 'safe')
        self.assertEqual((self.root / 'safe/pkg/__init__.py').read_text(), 'x = 1\n')

    def test_internal_file_symlink_is_materialized_and_checked_as_target_content(self):
        path = self.root/'source.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('repo/CODE_OF_CONDUCT.md', 'Code of conduct\n')
            link = zipfile.ZipInfo('repo/docs/source/conduct.md')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, '../../CODE_OF_CONDUCT.md')
        destination = self.root/'source'
        self.module.extract_source(path, destination)
        self.assertEqual((destination/'docs/source/conduct.md').read_text(), 'Code of conduct\n')
        self.assertFalse((destination/'docs/source/conduct.md').is_symlink())
        self.assertTrue(self.module.source_is_complete(path, destination))

    def test_external_or_cyclic_symlink_is_rejected_before_file_writes(self):
        for target in ['../../../escape.md', '/absolute.md', 'b.md']:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                for name, value in [('repo/docs/a.md', target), ('repo/docs/b.md', 'a.md')]:
                    link = zipfile.ZipInfo(name)
                    link.create_system = 3
                    link.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(link, value)
            data.seek(0)
            with self.assertRaises(ValueError):
                self.module.extract_source(data, self.root/'rejected')
            data.seek(0)
            with self.assertRaises(ValueError):
                self.module.extract_source(data, self.root/'rejected', preserve_missing_links=True)

    def test_internal_directory_symlink_materializes_archive_subtree(self):
        path = self.root/'directory-link.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('repo/.agents/skills/example.md', 'instructions as source data')
            link = zipfile.ZipInfo('repo/.claude')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, '.agents')
        destination = self.root/'directory-source'
        self.module.extract_source(path, destination)
        self.assertEqual((destination/'.claude/skills/example.md').read_text(), 'instructions as source data')
        self.assertTrue(self.module.source_is_complete(path, destination))

    def test_missing_internal_links_can_be_preserved_as_text_without_following_them(self):
        path = self.root / 'missing-link.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('repo/real.py', 'print(1)\n')
            link = zipfile.ZipInfo('repo/missing.py')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, 'generated.py')
        with self.assertRaises(ValueError):
            self.module.extract_source(path, self.root / 'strict-missing')
        destination = self.root / 'preserved-missing'
        self.module.extract_source(path, destination, preserve_missing_links=True)
        self.assertEqual((destination / 'missing.py').read_text(), 'generated.py')
        self.assertFalse((destination / 'missing.py').is_symlink())
        self.assertFalse((destination / 'generated.py').exists())
        self.assertTrue(self.module.source_is_complete(path, destination, preserve_missing_links=True))

    def test_long_rate_reset_is_not_silently_retried_early(self):
        delay = self.module.retry_delay(403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '200'}, now=100)
        self.assertGreaterEqual(delay, 100)
        self.assertIsNone(self.module.retry_delay(403, {}, now=100))
        self.assertEqual(self.module.retry_delay(429, {'Retry-After': '12'}, now=100), 12)

    def test_shared_cooldown_extends_an_already_waiting_worker(self):
        credential = self.module.Credential('secret', 0)
        credential.next_request = time.monotonic() + 0.15
        started = time.monotonic()
        worker = threading.Thread(target=credential.wait)
        worker.start()
        time.sleep(0.04)
        credential.cooldown(0.3)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_source_receipt_survives_interrupted_publication_and_validates_archive(self):
        sha = 'a' * 40
        archive_path = self.root / f'source-{sha}.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('repo/pkg.py', 'x = 1')
        class Client:
            def get(self, path):
                return {'sha': sha}
        real_replace = Path.replace
        def interrupt(path, target):
            if Path(target).name == 'source':
                raise OSError('simulated interruption')
            return real_replace(path, target)
        with patch.object(Path, 'replace', interrupt):
            with self.assertRaises(OSError):
                self.module.source_snapshot(Client(), 'sympy/sympy', {'default_branch': 'master'}, self.root)
        self.assertTrue((self.root / 'source_snapshot.json').exists())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', ResourceWarning)
            result = self.module.source_snapshot(Client(), 'sympy/sympy', {'default_branch': 'master'}, self.root)
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])
        self.assertEqual(result['commit_sha'], sha)
        self.assertEqual((self.root / 'source/pkg.py').read_text(), 'x = 1')
        archive_path.write_bytes(b'corrupted')
        with self.assertRaises((ValueError, RuntimeError)):
            self.module.source_snapshot(Client(), 'sympy/sympy', {'default_branch': 'master'}, self.root)

    def test_issue_workers_fetch_distinct_tasks_concurrently_and_export_in_order(self):
        barrier = threading.Barrier(2)
        class Client:
            def get(self, path):
                return {'full_name': 'sympy/sympy', 'default_branch': 'master'}
            def pages(self, path, params=None):
                if path.endswith('/issues'):
                    return [{'number': n, 'title': 'task', 'state': 'open', 'labels': [{'name': 'Easy to Fix'}]}
                            for n in [2, 1]]
                if path.endswith('/timeline'):
                    barrier.wait(timeout=1)
                    return [{'event': 'labeled'}]
                return []
        result = self.module.collect_project(self.module.PROJECTS[0], Client(), self.root,
                                             source=False, issue_workers=2)
        self.assertEqual(result['status'], 'complete')
        path = self.root/'sympy__sympy/issues.jsonl'
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['issue']['number'] for r in records], [1, 2])


if __name__ == '__main__':
    unittest.main()
