import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_final_experiment as final


class FinalExperimentOutputTests(unittest.TestCase):
    def test_explicit_output_is_used_for_analysis_and_review(self):
        protocol = {'protocol_sha256': 'frozen'}
        selected = [{'task_id': 'a/b#1', 'repo': 'a/b'}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            protocol_path = root / 'protocol.json'
            protocol_path.write_text(json.dumps(protocol))
            with patch.object(final, 'ROOT', root), patch.object(final, 'PROTOCOL_PATH', protocol_path), \
                    patch.object(final, 'select_holdout', return_value=selected), \
                    patch.object(final, 'freeze_holdout_manifest') as freeze, \
                    patch.object(final, 'ensure_all_results') as ensure, \
                    patch.object(final, 'run_analysis') as analyze, \
                    patch.object(final, 'run_review', return_value=True) as review, \
                    patch.object(final, 'load_project_env'), patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'offline-test'}), \
                    patch.object(final.os, 'chdir'):
                self.assertEqual(final.main(['--output', 'experiments/graph-test']), 0)
            output = root / 'experiments/graph-test'
            self.assertEqual(freeze.call_args.args[-1], output)
            self.assertEqual(analyze.call_args.args[-1], output)
            self.assertEqual(ensure.call_args.args[-1], output)
            self.assertEqual(review.call_args.args[-1], output)


if __name__ == '__main__':
    unittest.main()
