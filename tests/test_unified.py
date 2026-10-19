import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


RULES = [
    {'id': 'python', 'name': 'Python', 'category': 'language', 'weight': 1, 'pattern': r'\bpython\b'},
    {'id': 'cli', 'name': 'CLI选项', 'category': 'tool', 'weight': 2, 'pattern': r'click|命令行'},
    {'id': 'async', 'name': '异步', 'category': 'concept', 'weight': 3, 'pattern': r'asyncio|事件循环'},
]


class UnifiedAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'gfi_prototype/unified.py').exists():
            raise unittest.SkipTest('Unified adapter module is missing')

    def setUp(self):
        from gfi_prototype import unified, analysis_cache
        self.unified, self.cache = unified, analysis_cache
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'example__project'
        self.folder.mkdir()
        (self.folder / 'issues').mkdir()
        self.bundle = {'repo': 'example/project', 'complete': True, 'fetched_at': '2026-10-16T00:00:00Z',
                       'issue': {'number': 1, 'title': 'Fix async CLI', 'body': 'Improve asyncio handling in the CLI.',
                                 'state': 'open', 'html_url': 'https://github.com/example/project/issues/1',
                                 'labels': [{'name': 'good first issue'}]}, 'comments': []}
        (self.folder / 'issues/1.json').write_text(json.dumps(self.bundle), encoding='utf-8')
        (self.folder / 'source_snapshot.json').write_text(
            json.dumps({'repo': 'example/project', 'commit_sha': 'c' * 40}), encoding='utf-8')
        self.evidence = [{'id': 'I1', 'url': 'https://github.com/example/project/issues/1', 'kind': 'issue'},
                         {'id': 'S1', 'url': 'https://github.com/example/project/blob/master/cli.py', 'path': 'cli.py',
                          'kind': 'source', 'start_line': 1, 'end_line': 2, 'text': 'import asyncio\n'}]

    def analysis(self, stack=None):
        if stack is None:
            stack = [{'technology_id': 'asyncio-lib', 'name': 'asyncio', 'category': '概念',
                      'role_in_task': '处理事件循环', 'evidence_ids': ['S1'], 'basis': 'explicit'},
                     {'technology_id': 'python-lang', 'name': 'Python', 'category': '语言',
                      'role_in_task': '实现脚本主体', 'evidence_ids': ['I1'], 'basis': 'inferred'}]
        return {'schema_version': 2,
                'task_goal': {'text': '修复CLI事件循环', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                'task_scope': '调整CLI中asyncio调用方式',
                'technology_stack': stack,
                'required_knowledge': ['Python'],
                'related_files': [{'path': 'cli.py', 'evidence_ids': ['S1']}],
                'related_tests': [],
                'suitable_background': '熟悉Python异步的开发者',
                'newcomer_stage': {'stage': '具备开发基础', 'entry_conditions': ['理解事件循环'], 'rationale': '涉及调用关系',
                                   'evidence_ids': ['I1']},
                'getting_started': ['阅读cli.py'],
                'acceptance_criteria': [{'text': '事件循环正确关闭', 'evidence_ids': ['S1'], 'basis': 'explicit'}],
                'unknowns': ['目标Python版本未确认'],
                'evidence': ['I1', 'S1'],
                'demo_assumption': True}

    def run_dir(self):
        run = self.folder / 'prototype_runs' / '20261016T120000Z_1_analyze_abc123'
        run.mkdir(parents=True)
        return run

    def test_tags_follow_technology_stack_not_v1_skill_order(self):
        result = self.unified.normalize_analysis_tags(self.analysis(), RULES, self.evidence)
        ids = [t['id'] for t in result['technology_tags']]
        self.assertEqual(ids, ['async', 'python'])
        self.assertEqual(result['unmapped_indexes'], [])
        self.assertEqual(result['technology_tags'][0]['requirement_indexes'], [1])
        self.assertEqual(result['technology_tags'][0]['evidence_ids'], ['S1'])

    def test_adapter_rejects_v1_analysis_without_technology_stack(self):
        with self.assertRaises(ValueError):
            self.unified.normalize_analysis_tags(
                {'task_goal': {'text': 'v1', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                 'required_skills': [], 'related_files': []}, RULES, self.evidence)

    def test_profile_origin_and_stage_are_preserved(self):
        meta = {'repo': 'example/project', 'issue_number': 1, 'title': 'Fix async CLI',
                'url': 'https://github.com/example/project/issues/1', 'source_commit': 'c' * 40,
                'issue_content_sha256': 'd' * 64}
        profile = self.unified.analysis_profile(self.analysis(), meta, RULES, self.evidence)
        self.assertEqual(profile['analysis_origin'], 'real_model_analysis_v2')
        self.assertEqual(profile['newcomer_stage']['stage'], '具备开发基础')
        self.assertEqual(profile['goal'], '修复CLI事件循环')
        self.assertEqual(profile['task_id'], 'example/project#1')
        self.assertTrue(profile['demo_assumption'])
        self.assertFalse(profile['is_ground_truth'])

    def test_analysis_key_changes_with_commit_model_and_prompt(self):
        key = self.cache.compute_analysis_key(self.folder, 1, 'deepseek-flash', 'p1', 2)
        self.assertEqual(key['issue_number'], 1)
        self.assertEqual(key['source_commit'], 'c' * 40)
        self.assertNotEqual(key['issue_content_sha256'], '')
        other_model = self.cache.compute_analysis_key(self.folder, 1, 'deepseek-flash', 'p2', 2)
        self.assertNotEqual(key, other_model)
        (self.folder / 'issues/1.json').write_text(
            json.dumps({**self.bundle, 'issue': {**self.bundle['issue'], 'body': 'changed'}}), encoding='utf-8')
        changed = self.cache.compute_analysis_key(self.folder, 1, 'deepseek-flash', 'p1', 2)
        self.assertNotEqual(changed['issue_content_sha256'], key['issue_content_sha256'])

    def test_cache_lists_only_complete_runs(self):
        complete = self.run_dir()
        (complete / 'run_manifest.json').write_text(
            json.dumps({'status': 'analysis_ready_pending_human_review', 'schema_version': 2}), encoding='utf-8')
        running = self.folder / 'prototype_runs' / '20261016T130000Z_1_analyze_def456'
        running.mkdir(parents=True)
        (running / 'run_manifest.json').write_text(json.dumps({'status': 'running'}), encoding='utf-8')
        found = self.cache.find_complete_analysis_runs(self.folder)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['status'], 'analysis_ready_pending_human_review')

    def _seed_cache(self, run, analysis):
        result = {'analysis': analysis, 'schema_version': 2, 'prompt_version': 'analysis_v2_2026-10-16',
                  'attempts': 1, 'validation_failures': [],
                  'validation_scope': 'schema_and_citation_references_only',
                  'semantic_review_status': 'pending_human_review', 'is_ground_truth': False,
                  'current_availability_verified': False}
        (run / 'run_manifest.json').write_text(
            json.dumps({'status': 'analysis_ready_pending_human_review', 'schema_version': 2,
                        'prompt_version': 'analysis_v2_2026-10-16', 'model': 'deepseek-flash'}), encoding='utf-8')
        (run / 'analysis.json').write_text(json.dumps(result), encoding='utf-8')
        (run / 'evidence_bundle.json').write_text(json.dumps({'evidence': self.evidence}), encoding='utf-8')
        key = self.cache.compute_analysis_key(self.folder, 1, 'deepseek-flash', 'analysis_v2_2026-10-16', 2)
        entry = {'run_dir': run.relative_to(self.folder).as_posix(), 'schema_version': 2,
                 'prompt_version': 'analysis_v2_2026-10-16', 'model': 'deepseek-flash',
                 'issue_content_sha256': key['issue_content_sha256'], 'source_commit': key['source_commit'],
                 'analysis_sha256': hashlib.sha256((run / 'analysis.json').read_bytes()).hexdigest(),
                 'rules_sha256': hashlib.sha256(json.dumps(RULES, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                 'profile_ready': True}
        self.cache.write_analysis_cache(self.folder, {'example/project#1': entry})
        return key

    def test_profile_from_cached_analysis_rejects_input_hash_change(self):
        run = self.run_dir()
        self._seed_cache(run, self.analysis())
        (self.folder / 'issues/1.json').write_text(
            json.dumps({**self.bundle, 'issue': {**self.bundle['issue'], 'body': 'changed'}}), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.cache.profile_from_cached_analysis(self.folder, 'example/project#1', RULES)

    def test_profile_cache_tracks_analysis_and_rules_hash(self):
        run = self.run_dir()
        self._seed_cache(run, self.analysis())
        profile = self.cache.profile_from_cached_analysis(self.folder, 'example/project#1', RULES)
        self.assertEqual(profile['analysis_origin'], 'real_model_analysis_v2')
        with self.assertRaises(ValueError):
            self.cache.profile_from_cached_analysis(self.folder, 'example/project#1', RULES + [{
                'id': 'extra', 'name': 'Extra', 'category': 'x', 'weight': 1, 'pattern': r'extra'}])


if __name__ == '__main__':
    unittest.main()
