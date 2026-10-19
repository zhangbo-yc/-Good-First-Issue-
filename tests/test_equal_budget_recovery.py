import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_equal_budget_resilient as recovery


class RecoveryTests(unittest.TestCase):
    def test_schema_failure_records_specific_reason_and_redacts_key(self):
        class Client:
            model = 'deepseek-test'
            secret = 'private-key'
            def complete(self, messages):
                return {'content':'{"bad":"private-key"}', 'usage':{'total_tokens':4,
                    'prompt_tokens':2,'completion_tokens':2}, 'elapsed_seconds':0.1}
        def validator(value):
            raise ValueError('Unknown evidence ID private-key')
        with tempfile.TemporaryDirectory() as folder:
            session = recovery.DiagnosticSession(Client(), Path(folder))
            with self.assertRaises(ValueError):
                session.request([{'role':'user','content':'q'}], validator, 'analysis')
            files = list(Path(folder).glob('diagnostics/*.json'))
            self.assertEqual(len(files), 1)
            saved = files[0].read_text(encoding='utf-8')
            self.assertIn('Unknown evidence ID', saved)
            self.assertNotIn('private-key', saved)
            self.assertEqual(len(list(Path(folder).glob('attempts/*.json'))), 1)

    def test_failed_task_does_not_block_later_tasks_and_retries_are_bounded(self):
        seen = []
        def analyze(output, protocol, config, key, task_ids):
            seen.extend(task_ids)
            if task_ids == ['a/b#1']:
                raise RuntimeError('Condition incomplete; resume from validated stage receipts')
        with tempfile.TemporaryDirectory() as folder, patch.object(recovery.base, 'run_analysis', side_effect=analyze):
            failed = recovery.analyze_resilient(Path(folder), {}, {}, 'unused', ['a/b#1','a/b#2'], 2)
            self.assertEqual(failed, ['a/b#1'])
            self.assertEqual(seen, ['a/b#1','a/b#1','a/b#2'])
            self.assertEqual(json.loads((Path(folder)/'recovery_progress.json').read_text())['failed_task_ids'], failed)

    def test_invalid_json_gets_parser_diagnostic(self):
        class Client:
            model='deepseek-test'
            def complete(self,messages):
                return {'content':'{"unfinished":', 'usage':{'total_tokens':2,
                    'prompt_tokens':1,'completion_tokens':1}}
        with tempfile.TemporaryDirectory() as folder:
            session = recovery.DiagnosticSession(Client(),Path(folder))
            with self.assertRaises(ValueError):
                session.request([{'role':'user','content':'q'}],lambda x:x,'analysis')
            row=json.loads(next(Path(folder).glob('diagnostics/*.json')).read_text())
            self.assertEqual(row['detail'][0]['type'], 'JSONDecodeError')

    def test_integrity_errors_are_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(recovery.base, 'run_analysis',
                side_effect=ValueError('Incompatible result cache')):
            with self.assertRaisesRegex(ValueError, 'Incompatible'):
                recovery.analyze_resilient(Path(folder), {}, {}, 'unused', ['a/b#1'], 3)

    def test_successful_stage_is_still_reused_without_calling_provider(self):
        class Client:
            model = 'deepseek-test'
            calls = 0
            def complete(self, messages):
                self.calls += 1
                return {'content':'{"answer":1}', 'usage':{'total_tokens':2,
                    'prompt_tokens':1,'completion_tokens':1}}
        client = Client()
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                session = recovery.DiagnosticSession(client, Path(folder))
                result = session.request([{'role':'user','content':'q'}],lambda x:x,'analysis')
                self.assertEqual(result, {'answer':1})
                self.assertEqual(session.resources()['model_calls'], 1)
            self.assertEqual(client.calls, 1)
            self.assertEqual(len(list(Path(folder).glob('diagnostics/*.json'))), 0)

if __name__ == '__main__':
    unittest.main()
