import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'clean_gfi.py'


def bundle(number=1, body='Please add a documented public API for this specific operation.', comments=None, assignees=None):
    return {'schema_version': 1, 'repo': 'example/project', 'fetched_at': '2026-10-16T00:00:00+00:00',
            'complete': True, 'issue': {'number': number, 'title': 'Add public API', 'body': body,
            'state': 'open', 'html_url': f'https://github.com/example/project/issues/{number}',
            'updated_at': '2026-10-15T00:00:00Z', 'labels': [{'name': 'good first issue'}],
            'assignees': assignees or []}, 'comments': comments or [], 'timeline': [], 'related_pull_requests': []}


def comment(text, date='2026-10-15T00:00:00Z', role='NONE'):
    return {'body': text, 'created_at': date, 'updated_at': date, 'author_association': role,
            'html_url': 'https://github.com/example/project/issues/1#issuecomment-7', 'user': {'login': 'alice'}}


class CleaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('cleaner', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertIsNotNone(self.module, 'Step-one cleaning implementation is missing')

    def test_old_interest_does_not_become_current_assignment(self):
        record = self.module.triage(bundle(comments=[comment('I would like to work on this.', '2020-01-01T00:00:00Z')]))
        self.assertEqual(record['availability'], 'needs_confirmation')
        self.assertIn('historical_interest', record['quality_flags'])
        self.assertFalse(record['recommendation_ready'])

    def test_recent_explicit_work_is_possible_claim_not_confirmed_assignment(self):
        record = self.module.triage(bundle(comments=[comment('I am working on this issue.')]))
        self.assertEqual(record['availability'], 'possibly_claimed')
        self.assertEqual(record['availability_signals'][0]['url'], 'https://github.com/example/project/issues/1#issuecomment-7')

    def test_quotes_questions_and_negations_do_not_claim_the_task(self):
        for text in ['> I am working on this issue.\n\nThanks.', 'Is anyone working on this issue?',
                     'I am not working on this issue anymore.', '```\nI am working on this issue.\n```']:
            with self.subTest(text=text):
                self.assertEqual(self.module.triage(bundle(comments=[comment(text)]))['availability'], 'needs_confirmation')

    def test_pr_reference_is_not_proof_of_solution_or_open_pr(self):
        record = self.module.triage(bundle(comments=[comment('Related example https://github.com/example/project/pull/42')]))
        self.assertEqual(record['availability'], 'needs_confirmation')
        self.assertFalse(record['pr_references'][0]['verified'])
        self.assertEqual(record['pr_references'][0]['relation'], 'unverified_reference')

    def test_possible_solution_evidence_stays_provisional(self):
        record = self.module.triage(bundle(comments=[comment('I think this can be closed now because the API was removed.', role='CONTRIBUTOR')]))
        self.assertEqual(record['availability'], 'possibly_resolved')
        self.assertIn('possible_resolution', record['quality_flags'])

    def test_partial_fix_questions_and_conditionals_do_not_resolve_whole_task(self):
        statements = [
            'I have already fixed matrices.py in my branch.',
            'This can be closed if a test is added for this ODE',
            'Please have a look if this can be closed.',
            'Can this issue be closed?',
            'This issue is not resolved.',
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                record = self.module.triage(bundle(comments=[comment(statement)]))
                self.assertEqual(record['availability'], 'needs_confirmation')
                self.assertFalse(any(s['type'] == 'possible_resolution' for s in record['availability_signals']))

    def test_old_solution_claim_does_not_describe_current_availability(self):
        record = self.module.triage(bundle(comments=[comment('This issue can be closed.', '2020-01-01T00:00:00Z')]))
        self.assertEqual(record['availability'], 'needs_confirmation')
        self.assertIn('historical_resolution', record['quality_flags'])

    def test_newer_remaining_work_overrides_earlier_resolution_claim(self):
        earlier = comment('This issue is resolved.', '2026-10-01T00:00:00Z')
        earlier['html_url'] += '-earlier'
        later = comment('There are two points that remain from this issue.', '2026-10-15T00:00:00Z')
        later['html_url'] += '-later'
        record = self.module.triage(bundle(comments=[later, earlier]))
        self.assertEqual(record['availability'], 'needs_confirmation')
        self.assertIn('resolution_conflict', record['quality_flags'])
        positive = next(s for s in record['availability_signals'] if s['type'] == 'possible_resolution')
        self.assertEqual(positive['superseded_by'], [later['html_url']])

    def test_newer_resolution_can_follow_older_remaining_work(self):
        earlier = comment('There is remaining work.', '2026-10-01T00:00:00Z')
        later = comment('This issue is resolved.', '2026-10-15T00:00:00Z')
        record = self.module.triage(bundle(comments=[later, earlier]))
        self.assertEqual(record['availability'], 'possibly_resolved')

    def test_old_issue_is_flagged_without_being_deleted(self):
        data = bundle()
        data['issue']['updated_at'] = '2015-01-01T00:00:00Z'
        record = self.module.triage(data)
        self.assertEqual(record['record_type'], 'task')
        self.assertIn('long_inactive', record['quality_flags'])

    def test_closed_task_is_historical_not_an_open_candidate(self):
        data = bundle()
        data['issue']['state'] = 'closed'
        record = self.module.triage(data)
        self.assertEqual(record['availability'], 'closed_in_snapshot')
        self.assertFalse(record['candidate_record'])
        self.assertTrue(record['historical_record'])

    def test_spatial_translation_does_not_imply_internationalization(self):
        data = bundle()
        data['issue']['title'] = 'Create a layer with scale and translation of the selected layer'
        record = self.module.triage(data)
        self.assertNotIn('internationalization', record['task_types'])

    def test_curated_type_override_needs_matching_evidence(self):
        rule = {'record_type': 'guide', 'match_text': 'See other issues', 'reason': 'Redirect only'}
        record = self.module.triage(bundle(body='See other issues for actual work.'), rule)
        self.assertEqual(record['record_type'], 'guide')
        self.assertFalse(record['candidate_record'])
        with self.assertRaises(ValueError):
            self.module.triage(bundle(), rule)

    def test_tracker_override_can_preserve_comment_checklist_evidence(self):
        rule = {'record_type': 'umbrella', 'match_text': 'public API',
                'comment_match_text': 'Tracking PRs:', 'reason': 'Multiple independent work items'}
        record = self.module.triage(bundle(comments=[comment('Tracking PRs: several independent rules')]), rule)
        self.assertEqual(record['classification_evidence'][1]['url'], 'https://github.com/example/project/issues/1#issuecomment-7')
        with self.assertRaises(ValueError):
            self.module.triage(bundle(), rule)

    def test_cleaning_preserves_code_while_removing_template_comments(self):
        text = '<!-- template -->\r\nGoal\r\n```html\r\n<!-- meaningful sample -->\r\n```'
        cleaned = self.module.clean_markdown(text)
        self.assertNotIn('template', cleaned)
        self.assertIn('<!-- meaningful sample -->', cleaned)

    def test_pipeline_preserves_raw_input_and_accounts_for_all_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'example__project'
            folder.mkdir()
            path = folder / 'issues.jsonl'
            raw = json.dumps(bundle()) + '\n' + json.dumps(bundle(2, 'See other issues')) + '\n'
            path.write_text(raw, encoding='utf-8')
            result = self.module.run(root, {'example/project#2': {'record_type': 'guide', 'match_text': 'See other issues', 'reason': 'Redirect'}})
            self.assertEqual(path.read_text(encoding='utf-8'), raw)
            self.assertEqual(result['total_records'], 2)
            self.assertEqual(result['record_types'], {'task': 1, 'guide': 1})
            records = [json.loads(line) for line in (folder / 'processed/triage.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(len((folder / 'processed/tasks.jsonl').read_text(encoding='utf-8').splitlines()), 1)
            self.assertTrue((root / 'CLEANING_REPORT.md').exists())

    def test_duplicate_identity_is_reported_instead_of_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'example__project'
            folder.mkdir()
            raw = (json.dumps(bundle()) + '\n') * 2
            (folder / 'issues.jsonl').write_text(raw, encoding='utf-8')
            result = self.module.run(root, {})
            self.assertEqual(result['total_records'], 1)
            self.assertEqual(result['duplicate_records'], 1)

    def test_closed_tasks_have_separate_output_and_complete_accounting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root/'example__project'
            folder.mkdir()
            closed = bundle(2)
            closed['issue']['state'] = 'closed'
            (folder/'issues.jsonl').write_text(json.dumps(bundle())+'\n'+json.dumps(closed)+'\n', encoding='utf-8')
            result = self.module.run(root, {})
            self.assertEqual(result['total_records'], 2)
            self.assertEqual(result['candidate_records'], 1)
            self.assertEqual(result['historical_records'], 1)
            self.assertEqual(len((folder/'processed/tasks.jsonl').read_text(encoding='utf-8').splitlines()), 1)
            self.assertEqual(len((folder/'processed/historical_tasks.jsonl').read_text(encoding='utf-8').splitlines()), 1)
            self.assertEqual((folder/'processed/non_task_records.jsonl').read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()
