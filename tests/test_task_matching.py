import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gfi_prototype.task_matching import demo_record, technology_profile, match_tasks
from gfi_prototype.task_matching import match_cache_key
from recommend_gfi import generate_demo


RULES = [
    {'id': 'python', 'name': 'Python', 'category': 'language', 'weight': 1, 'pattern': r'Python'},
    {'id': 'numpy', 'name': 'NumPy', 'category': 'library', 'weight': 3, 'pattern': r'NumPy|numpy'},
    {'id': 'pytest', 'name': 'pytest', 'category': 'tool', 'weight': 1, 'pattern': r'pytest'},
]


class TaskMatchingTests(unittest.TestCase):
    def draft(self, number=1, repo='a/b', skills=None):
        return {'repo': repo, 'issue_number': number, 'title': 'Task', 'issue_url': f'https://github.com/{repo}/issues/{number}',
                'source_commit': 'a' * 40, 'goal': 'Task goal', 'suitable_background_draft': '已有Python基础',
                'required_skills_draft': [{'name': name, 'rationale': reason, 'evidence_ids': ['I1']}
                                          for name, reason in (skills or [('Python数组', 'NumPy索引')])],
                'evidence': [{'id': 'I1', 'url': f'https://github.com/{repo}/issues/{number}'}]}

    def profile(self, number=1, repo='a/b', skills=None):
        return technology_profile(self.draft(number, repo, skills), RULES)

    def seed_model_cache(self, project, number=1):
        from gfi_prototype import analysis_cache
        bundle = {'repo': 'a/b', 'complete': True, 'fetched_at': '2026-10-16T00:00:00Z',
                  'issue': {'number': number, 'title': 'Model task', 'state': 'open',
                            'html_url': f'https://github.com/a/b/issues/{number}',
                            'labels': [{'name': 'good first issue'}]}, 'comments': []}
        (project / 'issues').mkdir(exist_ok=True)
        (project / f'issues/{number}.json').write_text(json.dumps(bundle), encoding='utf-8')
        run = project / 'prototype_runs' / f'20261016T120000Z_{number}_batch_abc123'
        run.mkdir(parents=True)
        evidence = [{'id': 'I1', 'url': f'https://github.com/a/b/issues/{number}', 'kind': 'issue'}]
        analysis = {'schema_version': 2,
                    'task_goal': {'text': '模型分析目标', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                    'task_scope': '模型分析范围',
                    'technology_stack': [{'technology_id': 'python-lang', 'name': 'Python', 'category': '语言',
                                          'role_in_task': '编写代码', 'evidence_ids': ['I1'], 'basis': 'explicit'}],
                    'required_knowledge': ['Python'],
                    'related_files': [], 'related_tests': [],
                    'suitable_background': '会Python的贡献者',
                    'newcomer_stage': {'stage': '基础入门', 'entry_conditions': ['能运行测试'],
                                       'rationale': '修改局部', 'evidence_ids': ['I1']},
                    'getting_started': ['阅读代码'],
                    'acceptance_criteria': [{'text': '行为符合预期', 'evidence_ids': ['I1'], 'basis': 'explicit'}],
                    'unknowns': ['上游状态未知'], 'evidence': ['I1'], 'demo_assumption': True}
        (run / 'run_manifest.json').write_text(json.dumps({
            'status': 'analysis_ready_pending_human_review', 'schema_version': 2,
            'prompt_version': 'analysis_v2_2026-10-16', 'model': 'deepseek-flash'}), encoding='utf-8')
        (run / 'analysis.json').write_text(json.dumps({'schema_version': 2, 'analysis': analysis}), encoding='utf-8')
        (run / 'evidence_bundle.json').write_text(json.dumps({'evidence': evidence}), encoding='utf-8')
        key = analysis_cache.compute_analysis_key(project, number, 'deepseek-flash',
                                                  'analysis_v2_2026-10-16', 2)
        entry = {'run_dir': run.relative_to(project).as_posix(), 'schema_version': 2,
                 'prompt_version': 'analysis_v2_2026-10-16', 'model': 'deepseek-flash',
                 'issue_content_sha256': key['issue_content_sha256'], 'source_commit': key['source_commit'],
                 'analysis_sha256': hashlib.sha256((run / 'analysis.json').read_bytes()).hexdigest(),
                 'rules_sha256': hashlib.sha256(json.dumps(RULES, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                 'profile_ready': True}
        analysis_cache.write_analysis_cache(project, {f'a/b#{number}': entry})

    def root_with_data(self, folder, triage_types=None, model_cache=False):
        root = Path(folder)
        project = root / 'data/a__b'
        (project / 'annotations').mkdir(parents=True)
        (project / 'processed').mkdir()
        raw = [{'repo': 'a/b', 'complete': True,
                'issue': {'number': n, 'title': f'Task {n}', 'state': 'closed' if n == 2 else 'open',
                          'html_url': f'https://github.com/a/b/issues/{n}',
                          'labels': [{'name': 'good first issue'}]}} for n in (1, 2)]
        (project / 'issues.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in raw), encoding='utf-8')
        (project / 'source_snapshot.json').write_text(json.dumps({'commit_sha': 'a' * 40}), encoding='utf-8')
        (project / 'annotations/task_analysis_drafts.jsonl').write_text(
            ''.join(json.dumps(self.draft(n)) + '\n' for n in (1, 2)), encoding='utf-8')
        kinds = triage_types or {'1': 'task', '2': 'task'}
        (project / 'processed/triage.jsonl').write_text(
            ''.join(json.dumps({'repo': 'a/b', 'number': n, 'record_type': kinds[str(n)]}) + '\n' for n in (1, 2)),
            encoding='utf-8')
        (root / 'technology_rules.json').write_text(json.dumps({'rule_version': 'test', 'rules': RULES}),
                                                    encoding='utf-8')
        if model_cache:
            self.seed_model_cache(project)
        return root

    def test_demo_state_overrides_closed_without_mutating_raw(self):
        raw = {'repo': 'a/b', 'issue': {'number': 1, 'state': 'closed', 'assignees': [{'login': 'someone'}]}}
        before = copy.deepcopy(raw)
        result = demo_record(raw, 'task')
        self.assertEqual(result['effective_state'], 'open')
        self.assertEqual(result['effective_resolution'], 'unresolved')
        self.assertEqual(result['state_at_snapshot'], 'closed')
        self.assertTrue(result['demo_assumption'])
        self.assertEqual(raw, before)

    def test_tags_come_from_requirements_not_unrelated_issue_text(self):
        draft = self.draft(skills=[('Python', '阅读语法')])
        draft['goal'] = 'An unrelated NumPy mention'
        result = technology_profile(draft, RULES)
        self.assertEqual([t['id'] for t in result['technology_tags']], ['python'])
        self.assertEqual(result['technology_tags'][0]['evidence_ids'], ['I1'])

    def test_closed_assigned_candidate_is_kept_in_demo(self):
        a = self.profile()
        b = dict(self.profile(2), state_at_snapshot='closed', availability='possibly_resolved', assignees=['user'])
        result = match_tasks(a, [a, b])
        self.assertEqual(result[0]['task_id'], 'a/b#2')
        self.assertTrue(result[0]['demo_assumption'])

    def test_same_repository_and_not_itself(self):
        a = self.profile()
        result = match_tasks(a, [a, self.profile(2), self.profile(3, 'other/repo')])
        self.assertEqual([r['task_id'] for r in result], ['a/b#2'])

    def test_specific_technology_match_precedes_generic_python(self):
        a = self.profile()
        specific = self.profile(2)
        generic = self.profile(3, skills=[('Python', '阅读语法')])
        result = match_tasks(a, [generic, specific])
        self.assertEqual([r['task_id'] for r in result], ['a/b#2', 'a/b#3'])
        self.assertEqual(result[1]['match_strength'], 'generic_only')

    def test_reused_added_tags_and_evidence_are_task_scoped(self):
        a = self.profile()
        b = self.profile(2, skills=[('NumPy', 'pytest测试')])
        result = match_tasks(a, [b])[0]
        self.assertEqual([t['id'] for t in result['shared_technologies']], ['numpy'])
        self.assertEqual([t['id'] for t in result['additional_technologies']], ['pytest'])
        pair = result['shared_technologies'][0]
        self.assertNotEqual(pair['source_evidence'][0]['url'], pair['candidate_evidence'][0]['url'])

    def test_no_overlap_returns_no_recommendation(self):
        self.assertEqual(match_tasks(self.profile(), [self.profile(2, skills=[('pytest', '测试')])]), [])

    def test_unknown_evidence_and_duplicate_rules_rejected(self):
        draft = self.draft()
        draft['required_skills_draft'][0]['evidence_ids'] = ['BAD']
        with self.assertRaises(ValueError):
            technology_profile(draft, RULES)
        with self.assertRaises(ValueError):
            technology_profile(self.draft(), RULES + [RULES[0]])

    def test_duplicate_task_ids_rejected(self):
        b = self.profile(2)
        with self.assertRaises(ValueError):
            match_tasks(self.profile(), [b, b])

    def test_deterministic_ties(self):
        a, b, c = self.profile(), self.profile(2), self.profile(3)
        self.assertEqual(match_tasks(a, [b, c]), match_tasks(a, [c, b]))

    def test_guide_is_not_a_candidate(self):
        candidate = dict(self.profile(2), record_type='guide')
        self.assertEqual(match_tasks(self.profile(), [candidate]), [])

    def test_generate_demo_includes_closed_tasks_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'data/a__b'
            (project / 'annotations').mkdir(parents=True)
            (project / 'processed').mkdir()
            raw = [{'repo': 'a/b', 'complete': True, 'issue': {'number': n, 'state': 'closed' if n == 2 else 'open'}} for n in (1, 2)]
            (project / 'issues.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in raw), encoding='utf-8')
            (project / 'source_snapshot.json').write_text(json.dumps({'commit_sha': 'a' * 40}), encoding='utf-8')
            (project / 'annotations/task_analysis_drafts.jsonl').write_text(''.join(json.dumps(self.draft(n)) + '\n' for n in (1, 2)), encoding='utf-8')
            (project / 'processed/triage.jsonl').write_text(''.join(json.dumps({'repo': 'a/b', 'number': n, 'record_type': 'task'}) + '\n' for n in (1, 2)), encoding='utf-8')
            (root / 'technology_rules.json').write_text(json.dumps({'rule_version': 'test', 'rules': RULES}), encoding='utf-8')
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            output, manifest = generate_demo(root, 'a/b', 1)
            self.assertEqual(manifest['demo_record_count'], 2)
            result = json.loads((output / 'recommendations.json').read_text(encoding='utf-8'))
            self.assertEqual(result['recommendations'][0]['task_id'], 'a/b#2')
            self.assertTrue(result['demo_assumption'])
            self.assertEqual(manifest['model_calls'], 0)
            self.assertTrue((output / 'DEMO_REPORT.md').exists())
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_model_profile_replaces_draft_for_same_task(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder, model_cache=True)
            output, manifest = generate_demo(root, 'a/b', 1)
            result = json.loads((output / 'recommendations.json').read_text(encoding='utf-8'))
            source = result['source_task']
            self.assertEqual(source['analysis_origin'], 'real_model_analysis_v2')
            self.assertIn('existing_assistant_task_draft_with_rule_normalization', source['available_origins'])
            self.assertEqual(manifest['profiled_task_count'], 2)
            self.assertEqual(result['recommendations'][0]['analysis_origin'], 'real_model_analysis_v2')
            self.assertEqual(result['recommendations'][0]['candidate_analysis_origin'],
                             'existing_assistant_task_draft_with_rule_normalization')
            all_rows = [json.loads(line) for line in
                        (output / 'all_task_matches.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
            draft_entry = next(r for r in all_rows if r['source_task_id'] == 'a/b#2')
            self.assertTrue(draft_entry['recommendations'])
            self.assertEqual(draft_entry['recommendations'][0]['analysis_origin'],
                             'existing_assistant_task_draft_with_rule_normalization')

    def test_draft_only_profiles_keep_draft_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder)
            output, manifest = generate_demo(root, 'a/b', 1)
            result = json.loads((output / 'recommendations.json').read_text(encoding='utf-8'))
            self.assertEqual(result['source_task']['analysis_origin'],
                             'existing_assistant_task_draft_with_rule_normalization')

    def test_demo_report_marks_match_origins(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder, model_cache=True)
            output, _ = generate_demo(root, 'a/b', 1)
            text = (output / 'DEMO_REPORT.md').read_text(encoding='utf-8')
            self.assertIn('真实模型分析', text)
            self.assertIn('助手草稿（规则归一化）', text)

    def test_non_task_cached_analysis_is_not_a_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder, triage_types={'1': 'umbrella', '2': 'task'}, model_cache=True)
            output, manifest = generate_demo(root, 'a/b', 2)
            result = json.loads((output / 'recommendations.json').read_text(encoding='utf-8'))
            self.assertEqual(result['recommendations'], [])

    def test_match_cache_hit_reuses_rows_and_rule_change_recomputes(self):
        source = self.profile(1)
        candidate = self.profile(2)
        key = match_cache_key(source, candidate, 'test', 3)
        self.assertEqual(key, match_cache_key(source, candidate, 'test', 3))
        self.assertNotEqual(key, match_cache_key(source, candidate, 'other', 3))
        self.assertNotEqual(key, match_cache_key(source, candidate, 'test', 2))
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder)
            generate_demo(root, 'a/b', 1)
            self.assertTrue((root / 'data/a__b/match_cache.json').exists())

    def test_all_matches_rows_are_ranked_by_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.root_with_data(folder)
            output, _ = generate_demo(root, 'a/b', 1)
            rows = [json.loads(line) for line in
                    (output / 'all_task_matches.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
            entry = next(r for r in rows if r['source_task_id'] == 'a/b#1')
            scores = [r['similarity_score'] for r in entry['recommendations']]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_production_rules_do_not_confuse_scipy_with_ci(self):
        rules = json.loads((Path(__file__).resolve().parents[1] / 'technology_rules.json').read_text(encoding='utf-8'))['rules']
        result = technology_profile(self.draft(skills=[('SciPy样条', '插值')]), rules)
        tags = {t['id'] for t in result['technology_tags']}
        self.assertIn('scipy', tags)
        self.assertNotIn('ci', tags)

    def test_ray_and_asyncio_are_normalized_without_array_or_worker_false_hits(self):
        rules = json.loads((Path(__file__).resolve().parents[1] / 'technology_rules.json').read_text(encoding='utf-8'))['rules']
        ray_draft = self.draft(1, 'ray-project/ray', [('Ray actor异步调用', 'Python asyncio事件循环中的worker')])
        ray_tags = {t['id'] for t in technology_profile(ray_draft, rules)['technology_tags']}
        self.assertIn('ray', ray_tags)
        self.assertIn('ray_actors', ray_tags)
        self.assertIn('asyncio', ray_tags)
        self.assertNotIn('fsspec', ray_tags)
        array_draft = self.draft(skills=[('NumPy array', '数组索引')])
        array_tags = {t['id'] for t in technology_profile(array_draft, rules)['technology_tags']}
        self.assertNotIn('ray', array_tags)


if __name__ == '__main__':
    unittest.main()
