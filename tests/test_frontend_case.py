import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_frontend_case as cli
from tests.test_research_pipeline import ScriptedClient, sample_packet
from tests.test_equal_budget import AddingRetriever


class FrontendCaseTests(unittest.TestCase):
    def test_output_guard_rejects_frozen_experiment(self):
        with self.assertRaises(ValueError):cli.safe_output(cli.ROOT/'experiments/frozen_20261017/equal_calls_v1')

    def test_mock_case_resume_reuses_both_completed_answers(self):
        config={'config_sha256':'test','model':'deepseek-test'}
        packets={m:sample_packet() for m in cli.PROFILES}
        retrievers={m:AddingRetriever() for m in cli.PROFILES}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            with patch.object(cli,'DeepSeekClient',return_value=ScriptedClient()):
                self.assertEqual(cli.run_case(out,config,packets,retrievers,'unused'),[])
            with patch.object(cli,'ROOT',out):
                report=cli.write_report(out,config).read_text(encoding='utf-8')
                self.assertIn('不是新未见测试',report)
                self.assertIn('不自动打质量分',report)
                self.assertIn('实际自适应轮数和Token可能不同',report)
            before={p:p.read_bytes() for p in out.glob('results/*.json')}
            with patch.object(cli,'DeepSeekClient',side_effect=AssertionError('No API client on complete resume')):
                self.assertEqual(cli.run_case(out,config,packets,retrievers,'unused'),[])
            self.assertTrue(all(p.read_bytes()==value for p,value in before.items()))

    def test_failed_condition_does_not_prevent_other_condition(self):
        class BadClient:
            model='deepseek-test'
            def complete(self,messages):raise RuntimeError('provider detail must not leak')
        config={'config_sha256':'test','model':'deepseek-test'}
        packets={m:sample_packet() for m in cli.PROFILES}
        retrievers={m:AddingRetriever() for m in cli.PROFILES}
        with tempfile.TemporaryDirectory() as folder, patch.object(cli,'DeepSeekClient',return_value=BadClient()):
            out=Path(folder)
            failed=cli.run_case(out,config,packets,retrievers,'unused',attempts=1)
            self.assertEqual(set(failed),set(cli.PROFILES))
            self.assertEqual(len(list(out.glob('results/*.json'))),2)
            self.assertEqual(len(list(out.glob('requests/*/attempts/*.json'))),2)

if __name__=='__main__':unittest.main()
