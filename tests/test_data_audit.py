import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from audit_gfi_data import audit_project, run_audit


class DataAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'example__project'
        self.folder.mkdir()
        self.sha = 'a' * 40
        archive = self.folder / f'source-{self.sha}.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for path in ['README.md', 'CONTRIBUTING.md', 'pyproject.toml', 'tests/test_x.py', 'pkg/x.py']:
                z.writestr('project/' + path, 'content')
        (self.folder / 'source_snapshot.json').write_text(json.dumps({
            'repo': 'example/project', 'commit_sha': self.sha, 'archive': archive.name,
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}), encoding='utf-8')
        self.rows = [{'repo': 'example/project', 'complete': True, 'fetched_at': '2026-10-16T00:00:00Z',
                      'issue': {'number': n, 'state': state}, 'comments': [], 'timeline': [],
                      'related_pull_requests': []} for n, state in [(1, 'open'), (2, 'closed')]]
        for name, content in [('manifest.json', {'status': 'complete'})]:
            (self.folder / name).write_text(json.dumps(content), encoding='utf-8')
        self.write_rows()

    def write_rows(self):
        (self.folder / 'issues.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in self.rows), encoding='utf-8')

    def test_counts_closed_as_history_not_success(self):
        result = audit_project(self.folder)
        self.assertEqual(result['raw_issue_count'], 2)
        self.assertEqual(result['states'], {'open': 1, 'closed': 1})
        self.assertEqual(result['related_pr_summary_count'], 0)
        self.assertFalse(result['closed_implies_success'])
        self.assertEqual(result['model_analysis_run_count'], 0)

    def test_source_materials_present_are_not_reported_missing(self):
        result = audit_project(self.folder)
        self.assertTrue(result['source_archive_verified'])
        self.assertIn('CONTRIBUTING.md', result['project_materials']['contribution_guides'])
        self.assertIn('pyproject.toml', result['project_materials']['dependency_manifests'])
        self.assertIn('tests/test_x.py', result['project_materials']['tests'])

    def test_changed_archive_fails_closed(self):
        archive = self.folder / f'source-{self.sha}.zip'
        archive.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            audit_project(self.folder)

    def test_duplicate_or_incomplete_raw_records_rejected(self):
        self.rows.append(self.rows[0])
        self.write_rows()
        with self.assertRaises(ValueError):
            audit_project(self.folder)

    def test_partial_model_run_is_not_counted_as_success(self):
        path = self.folder / 'prototype_runs/failed'
        path.mkdir(parents=True)
        (path / 'analysis.json').write_text('{}', encoding='utf-8')
        (path / 'run_manifest.json').write_text('{"status": "failed"}', encoding='utf-8')
        self.assertEqual(audit_project(self.folder)['model_analysis_run_count'], 0)

    def move_into_dataset_root(self):
        root = self.folder.parent
        (root / 'data').mkdir()
        self.folder = self.folder.rename(root / 'data' / self.folder.name)
        return root

    def test_missing_snapshot_cannot_silently_hide_a_project(self):
        root = self.move_into_dataset_root()
        broken = root / 'data/broken__project'
        broken.mkdir()
        (broken / 'issues.jsonl').write_text('{}\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            run_audit(root)

    def test_gap_text_updates_when_data_is_supplemented(self):
        self.rows[0]['related_pull_requests'] = [{'number': 3}]
        self.write_rows()
        for name in ['first', 'second']:
            path = self.folder / 'prototype_runs' / name
            path.mkdir(parents=True)
            (path / 'analysis.json').write_text('{}', encoding='utf-8')
            (path / 'run_manifest.json').write_text(json.dumps({'status': 'analysis_ready_pending_human_review', 'issue_number': 1}), encoding='utf-8')
        result = run_audit(self.move_into_dataset_root())
        gaps = {g['item']: g['action'] for g in result['gaps']}
        self.assertIn('1 extracted PR summaries', gaps['Related PR evidence'])
        self.assertIn('2 real analysis runs', gaps['Task-level model outputs'])


if __name__ == '__main__':
    unittest.main()
