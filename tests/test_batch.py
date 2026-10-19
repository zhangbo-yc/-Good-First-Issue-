import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BatchAnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'batch_analyze.py').exists():
            raise unittest.SkipTest('Batch analyzer module is missing')

    def setUp(self):
        import batch_analyze
        from gfi_prototype import analysis_cache
        self.batch, self.cache = batch_analyze, analysis_cache
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        (self.root / 'technology_rules.json').write_text(
            json.dumps({'rule_version': 'test', 'rules': [
                {'id': 'python', 'name': 'Python', 'category': 'language', 'weight': 1,
                 'pattern': r'\bpython\b'}]}), encoding='utf-8')
        self.calls = []

    def stub_store(self):
        class FakeStore:
            def __init__(self, folder):
                pass

            def build_index(self):
                return {}

            def evidence_bundle(self, number, max_sources=8):
                return {'repo': 'sympy/sympy', 'issue_number': number, 'source_commit': 'c' * 40,
                        'source_evidence_found': 1,
                        'evidence': [{'id': 'I1', 'url': 'https://github.com/example/project/issues/1',
                                      'kind': 'issue'}]}
        self.batch.EvidenceStore = FakeStore

    def project(self, repo='sympy/sympy', number=10, body='Example issue body'):
        folder = self.root / 'data' / repo.replace('/', '__')
        folder.mkdir(parents=True)
        (folder / 'issues').mkdir()
        bundle = {'repo': repo, 'complete': True, 'fetched_at': '2026-10-16T00:00:00Z',
                  'issue': {'number': number, 'title': 'Example task', 'body': body, 'state': 'open',
                            'html_url': f'https://github.com/{repo}/issues/{number}',
                            'labels': [{'name': 'good first issue'}]}, 'comments': []}
        (folder / f'issues/{number}.json').write_text(json.dumps(bundle), encoding='utf-8')
        (folder / 'source_snapshot.json').write_text(
            json.dumps({'repo': repo, 'commit_sha': 'c' * 40}), encoding='utf-8')
        return folder

    def model_factory(self):
        def factory():
            from langchain_core.runnables import RunnableLambda
            def model(prompt):
                self.calls.append(prompt)
                return self.valid_analysis()
            return RunnableLambda(model)
        return factory

    def valid_analysis(self):
        return json.dumps({'schema_version': 2,
                           'task_goal': {'text': '完成示例任务', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                           'task_scope': '实现示例改动',
                           'technology_stack': [{'technology_id': 'python-lang', 'name': 'Python', 'category': '语言',
                                                 'role_in_task': '编写实现代码', 'evidence_ids': ['I1'], 'basis': 'explicit'}],
                           'required_knowledge': ['Python'],
                           'related_files': [], 'related_tests': [],
                           'suitable_background': '熟悉Python的贡献者',
                           'newcomer_stage': {'stage': '基础入门', 'entry_conditions': ['能运行测试'],
                                              'rationale': '修改局部', 'evidence_ids': ['I1']},
                           'getting_started': ['阅读代码'],
                           'acceptance_criteria': [{'text': '行为符合预期', 'evidence_ids': ['I1'], 'basis': 'explicit'}],
                           'unknowns': ['上游状态未知'],
                           'evidence': ['I1'], 'demo_assumption': True})

    def test_dry_run_makes_zero_model_calls(self):
        self.project()
        rows = self.batch.run_batch(self.root, [{'repo': 'sympy/sympy', 'issue': 10}],
                                    self.model_factory(), dry_run=True)
        self.assertEqual(rows[0]['status'], 'cache_miss')
        self.assertEqual(self.calls, [])

    def test_cached_task_makes_zero_model_calls(self):
        folder = self.project()
        run = folder / 'prototype_runs' / '20261016T120000Z_10_batch_abc123'
        run.mkdir(parents=True)
        (run / 'run_manifest.json').write_text(json.dumps({
            'status': 'analysis_ready_pending_human_review', 'schema_version': 2,
            'prompt_version': self.batch.PROMPT_VERSION, 'model': 'deepseek-flash'}), encoding='utf-8')
        (run / 'analysis.json').write_text(json.dumps({'schema_version': 2, 'analysis': {}}), encoding='utf-8')
        (run / 'evidence_bundle.json').write_text(json.dumps({'evidence': [{'id': 'I1',
            'url': 'https://github.com/sympy/sympy/issues/10'}]}), encoding='utf-8')
        key = self.cache.compute_analysis_key(folder, 10, 'deepseek-flash', self.batch.PROMPT_VERSION, 2)
        entry = {'run_dir': run.relative_to(folder).as_posix(), 'schema_version': 2,
                 'prompt_version': self.batch.PROMPT_VERSION, 'model': 'deepseek-flash',
                 'issue_content_sha256': key['issue_content_sha256'], 'source_commit': key['source_commit'],
                 'analysis_sha256': '00', 'rules_sha256': '00', 'profile_ready': True}
        self.cache.write_analysis_cache(folder, {'sympy/sympy#10': entry})
        rows = self.batch.run_batch(self.root, [{'repo': 'sympy/sympy', 'issue': 10}],
                                    self.model_factory(), dry_run=False)
        self.assertEqual(rows[0]['status'], 'cache_hit')
        self.assertEqual(self.calls, [])

    def test_failure_is_recorded_and_batch_continues(self):
        self.stub_store()
        self.project('sympy/sympy', 10)
        self.project('napari/napari', 11)
        from langchain_core.runnables import RunnableLambda
        state = {'count': 0}
        def model(prompt):
            state['count'] += 1
            raise ValueError('bad output')
        def factory():
            return RunnableLambda(model)
        rows = self.batch.run_batch(self.root, [{'repo': 'sympy/sympy', 'issue': 10},
                                                {'repo': 'napari/napari', 'issue': 11}],
                                    factory, dry_run=False)
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertEqual(rows[0]['error_type'], 'ValueError')
        self.assertEqual(rows[1]['status'], 'failed')

    def test_completed_task_updates_analysis_cache(self):
        self.stub_store()
        folder = self.project()
        rows = self.batch.run_batch(self.root, [{'repo': 'sympy/sympy', 'issue': 10}],
                                    self.model_factory(), dry_run=False)
        self.assertEqual(rows[0]['status'], 'completed')
        entries = self.cache.read_analysis_cache(folder)
        self.assertIn('sympy/sympy#10', entries)
        self.assertTrue(entries['sympy/sympy#10']['profile_ready'])
        self.assertTrue(entries['sympy/sympy#10']['run_dir'].startswith('prototype_runs/'))
        run_folder = folder / entries['sympy/sympy#10']['run_dir']
        manifest = json.loads((run_folder / 'run_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['source_commit'], 'c' * 40)
        rules = json.loads((self.root / 'technology_rules.json').read_text(encoding='utf-8'))['rules']
        profile = self.cache.profile_from_cached_analysis(folder, 'sympy/sympy#10', rules)
        self.assertEqual(profile['analysis_origin'], 'real_model_analysis_v2')

    def test_cli_task_file_is_limited_to_known_repositories(self):
        with self.assertRaises(ValueError):
            self.batch.run_batch(self.root, [{'repo': 'unknown/repo', 'issue': 1}],
                                 self.model_factory(), dry_run=True)


if __name__ == '__main__':
    unittest.main()
