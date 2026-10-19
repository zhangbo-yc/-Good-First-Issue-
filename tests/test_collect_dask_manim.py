import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'collect_dask_manim.py'


def issue(number, label='good first issue', state='open'):
    return {'number': number, 'title': 'Example', 'state': state,
            'html_url': f'https://github.com/dask/dask/issues/{number}', 'labels': [{'name': label}]}


class DaskManimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('dask_manim', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertIsNotNone(self.module, 'Dask/Manim collector not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def save(self, issues):
        (self.folder / 'issues').mkdir(exist_ok=True)
        records = [{'repo': 'dask/dask', 'complete': True, 'issue': data} for data in issues]
        (self.folder / 'issues.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
        for record in records:
            (self.folder / 'issues' / f"{record['issue']['number']}.json").write_text(json.dumps(record), encoding='utf-8')

    def test_only_exact_gfi_label_variants_match(self):
        for label in ['good first issue', 'good-first-issue', 'Good First Issue']:
            self.assertTrue(self.module.is_gfi_label(label))
        for label in ['help wanted', 'not good first issue', 'good first issue candidate', 'easy']:
            self.assertFalse(self.module.is_gfi_label(label))

    def test_resolve_actual_labels_and_fail_if_absent(self):
        class Client:
            def pages(self, path, params):
                return [{'name': 'good first issue'}, {'name': 'help wanted'}]
        self.assertEqual(self.module.resolve_gfi_labels(Client(), 'dask/dask'), ['good first issue'])
        class EmptyClient:
            def pages(self, path, params):
                return [{'name': 'help wanted'}]
        with self.assertRaises(ValueError):
            self.module.resolve_gfi_labels(EmptyClient(), 'dask/dask')

    def test_audit_all_states_and_live_membership(self):
        records = [issue(1), issue(2, state='closed')]
        self.save(records)
        report = self.module.audit_project(self.folder, 'dask/dask', records)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['state_counts'], {'open': 1, 'closed': 1})
        self.assertEqual(report['snapshot_non_gfi_numbers'], [])
        self.assertTrue((self.folder/'label_audit.csv').exists())

    def test_audit_rejects_wrong_labels_and_prs(self):
        wrong, pr = issue(1, 'help wanted'), issue(2)
        pr['pull_request'] = {}
        self.save([wrong, pr])
        report = self.module.audit_project(self.folder, 'dask/dask', [])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['snapshot_non_gfi_numbers'], [1])
        self.assertEqual(report['pull_request_numbers'], [2])

    def test_audit_detects_omission_and_removed_live_label(self):
        self.save([issue(1), issue(3)])
        report = self.module.audit_project(self.folder, 'dask/dask', [issue(1), issue(2)])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['missing_from_export'], [2])
        self.assertEqual(report['not_in_live_label_query'], [3])

    def test_audit_checks_individual_files_not_only_jsonl(self):
        self.save([issue(1)])
        path = self.folder/'issues/1.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['issue']['labels'] = [{'name': 'help wanted'}]
        path.write_text(json.dumps(record), encoding='utf-8')
        report = self.module.audit_project(self.folder, 'dask/dask', [issue(1)])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['individual_file_mismatches'], [1])
        self.assertEqual(report['individual_non_gfi_numbers'], [1])

    def test_configured_runner_preserves_previous_summary_and_uses_all_states(self):
        original = self.folder/'collection_summary.json'
        original.write_text('{"previous":"sympy-napari"}', encoding='utf-8')
        def runner(project, client, output, state, source, refresh, limit):
            return {'repo': project['repo'], 'status': 'complete', 'requested_state': state}
        result = self.module.core.main(['--anonymous', '--no-source', '--output', str(self.folder)],
                                      projects=[{'repo': 'dask/dask'}], project_runner=runner,
                                      default_state='all', summary_name='collection_summary_dask_manim.json')
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(original.read_text())['previous'], 'sympy-napari')
        summary = json.loads((self.folder/'collection_summary_dask_manim.json').read_text())
        self.assertEqual(summary['projects'], [{'repo': 'dask/dask', 'status': 'complete', 'requested_state': 'all'}])


if __name__ == '__main__':
    unittest.main()
