import json
import tempfile
import unittest
from pathlib import Path

from gfi_prototype import artifacts


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'data/a__b'
        self.profile = {'repo': 'a/b', 'task_id': 'a/b#1', 'issue_number': 1,
                        'source_commit': 'a' * 40, 'title': 'Fix widget', 'goal': 'Fix',
                        'technology_tags': [{'id': 'python', 'name': 'Python', 'weight': 1}],
                        'original_requirements': [], 'evidence': []}

    def write_run(self, name='20260101', number=1, commit=None):
        folder = self.project / 'prototype_runs' / name
        folder.mkdir(parents=True)
        values = {
            'run_manifest.json': {'repo': 'a/b', 'issue_number': number,
                                  'source_commit': commit or 'a' * 40,
                                  'status': 'analysis_ready_pending_human_review',
                                  'model': 'deepseek-flash'},
            'evidence_bundle.json': {'repo': 'a/b', 'issue_number': number,
                                     'source_commit': commit or 'a' * 40,
                                     'evidence': [{'id': 'I1', 'text': 'Issue evidence'}]},
            'analysis.json': {'analysis': {'task_goal': {'text': 'Recorded result'}}}}
        for name, value in values.items():
            (folder / name).write_text(json.dumps(value), encoding='utf-8')
        return folder

    def test_recorded_analysis_exposes_actual_packet_and_output(self):
        self.write_run()
        result = artifacts.analysis_artifact(self.root, self.profile)
        self.assertEqual(result['kind'], 'model')
        self.assertEqual(result['packet']['evidence'][0]['text'], 'Issue evidence')
        self.assertEqual(result['analysis']['task_goal']['text'], 'Recorded result')

    def test_wrong_issue_or_commit_is_not_used_as_model_output(self):
        self.write_run(number=2)
        self.write_run(name='20260102', commit='b' * 40)
        self.assertEqual(artifacts.analysis_artifact(self.root, self.profile)['kind'], 'draft')

    def test_packet_identity_must_match_manifest(self):
        folder = self.write_run()
        path = folder / 'evidence_bundle.json'
        packet = json.loads(path.read_text())
        packet['issue_number'] = 2
        path.write_text(json.dumps(packet))
        self.assertEqual(artifacts.analysis_artifact(self.root, self.profile)['kind'], 'draft')

    def test_broken_recent_run_does_not_hide_valid_older_one(self):
        self.write_run()
        folder = self.write_run(name='20260102')
        (folder / 'analysis.json').write_text('{')
        self.assertEqual(artifacts.analysis_artifact(self.root, self.profile)['kind'], 'model')

    def test_missing_run_uses_draft_with_excerpts(self):
        folder = self.project / 'annotations'
        folder.mkdir(parents=True)
        draft = {**self.profile, 'evidence': [{'id': 'S1', 'excerpt': 'def widget()'}]}
        (folder / 'task_analysis_drafts.jsonl').write_text(json.dumps(draft), encoding='utf-8')
        result = artifacts.analysis_artifact(self.root, self.profile)
        self.assertEqual(result['kind'], 'draft')
        self.assertEqual(result['packet']['evidence'][0]['excerpt'], 'def widget()')


    def test_latest_demo_skips_incomplete_newer_run(self):
        base = self.root / 'data/demo_runs'
        for name, status in [('20260101', 'complete_demo'), ('20260102', 'pending')]:
            folder = base / name
            folder.mkdir(parents=True)
            (folder / 'run_manifest.json').write_text(json.dumps({'status': status}), encoding='utf-8')
        folder = base / '20260101'
        for filename in ('demo_catalog.jsonl', 'task_technology_profiles.jsonl', 'all_task_matches.jsonl'):
            (folder / filename).write_text('{"id": 1}\n', encoding='utf-8')
        catalog, profiles, matches, manifest = artifacts.latest_demo_data(self.root)
        self.assertEqual((catalog, profiles, matches), ([{'id': 1}],) * 3)
        self.assertEqual(manifest['status'], 'complete_demo')

    def test_missing_demo_reports_preparation_entry(self):
        with self.assertRaisesRegex(ValueError, 'recommend_gfi.py'):
            artifacts.latest_demo_data(self.root)


if __name__ == '__main__':
    unittest.main()
