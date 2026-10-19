import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import copy
import hashlib
import io
import json
from contextlib import redirect_stdout

import run_equal_budget_experiment as cli


class EqualBudgetCliTests(unittest.TestCase):
    def test_frozen_design_rejects_changed_model_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'design.json'
            first = cli.freeze_configuration(path, {'model':'deepseek-flash','task_ids':['a/b#1']})
            before = path.read_bytes()
            self.assertEqual(cli.freeze_configuration(path, {'model':'deepseek-flash','task_ids':['a/b#1']}), first)
            with self.assertRaisesRegex(ValueError, 'configuration|配置'):
                cli.freeze_configuration(path, {'model':'deepseek-other','task_ids':['a/b#1']})
            self.assertEqual(path.read_bytes(), before)

    def test_output_guard_never_accepts_historical_experiment_directory(self):
        root = Path('D:/haikesong-------bisai')
        with self.assertRaises(ValueError):
            cli.safe_output(root, root/'experiments/frozen_20261017/final_heldout_v3')
        valid = cli.safe_output(root, root/'experiments/frozen_20261017/equal_calls_v1')
        self.assertTrue(valid.name.startswith('equal_calls_'))

    def test_offline_analysis_resumes_completed_triple_without_provider_calls(self):
        from tests.test_research_pipeline import ScriptedClient,sample_packet
        from tests.test_equal_budget import AddingRetriever
        class Retriever(AddingRetriever):
            def initial(self,number):
                return sample_packet()
            def static_supplement(self,packet,limit=4,seen_keys=None):
                return self.supplement(packet,['fixed issue query'],limit)
        config={'model':'deepseek-test','config_sha256':'frozen','protocol_sha256':'protocol',
                'task_ids':['a/b#1']}
        protocol={'tasks':[{'task_id':'a/b#1','repo':'a/b','issue_number':1}]}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            with patch.object(cli,'DeepSeekClient',return_value=ScriptedClient()), \
                 patch.object(cli,'FixedQueryRetriever',return_value=Retriever()):
                cli.run_analysis(out,protocol,config,'offline-test',['a/b#1'])
            rows=cli.load_complete(out,config)
            self.assertEqual(len(rows),3)
            self.assertEqual({r['resources']['model_calls'] for r in rows},{4})
            before={p:p.read_bytes() for p in out.glob('results/*/*.json')}
            with patch.object(cli,'DeepSeekClient',return_value=object()), \
                 patch.object(cli,'FixedQueryRetriever',return_value=Retriever()):
                cli.run_analysis(out,protocol,config,'offline-test',['a/b#1'])
            self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))

    def test_full_blind_review_and_markdown_run_offline_and_share_identical_judgments(self):
        import judge_experiments as judge
        from gfi_prototype.research.client import ResearchSession
        from gfi_prototype.research.pipeline import run_variant
        from gfi_prototype.common import atomic_json
        from gfi_prototype.research.equal_budget_report import write_report
        from tests.test_research_pipeline import ScriptedClient,sample_packet
        template=run_variant(sample_packet(),None,ResearchSession(ScriptedClient(),attempts=1),'C')
        ids=[f'a/b#{i}' for i in range(1,40)]
        config={'config_sha256':'frozen','protocol_sha256':'protocol','task_ids':ids,
                'model':'deepseek-test','judge_model':'deepseek-pro-test'}
        protocol={'seed':1,'tasks':[{'task_id':task,'title':'Fix widget','repo':'a/b',
                  'issue_number':i,'source_commit':'a'*40} for i,task in enumerate(ids,1)]}
        judgment_keys=[]
        def cached(folder,client,prompt,payload,validator):
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            if prompt==judge.REFERENCE_PROMPT:
                value={'goal':'依据材料解释任务','requirements':[],'acceptance':[],'unknowns':[]}
            else:
                self.assertNotIn('prediction_id',payload['prediction'])
                judgment_keys.append(digest)
                value={'claims':[{'id':key,'correctness':'uncertain','citation_support':'uncertain',
                                  'reason':'离线接口测试，不是真实评审','evidence_ids':[]}
                                 for key in payload['claim_ids']],
                       'scores':{key:3 for key in judge.DIMENSIONS},'missing_points':[],
                       'summary':'离线测试，不调用模型。'}
            return {'result':validator(value),'request_sha256':digest}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'experiment'
            for task in ids:
                for method in ('R','S','D'):
                    value={**copy.deepcopy(template),'task_id':task,'variant':method,
                           'cache_key':cli.identity(config,task,method)}
                    atomic_json(cli.result_path(out,task,method),value)
            with patch.object(cli,'ROOT',Path(folder)),patch.object(judge,'ProClient',return_value=object()), \
                 patch.object(judge,'request_cached',side_effect=cached),redirect_stdout(io.StringIO()):
                cli.run_review(out,protocol,config,'offline-test')
            saved=cli.read(out/'evaluation/scores.json')
            self.assertTrue(saved['complete'])
            self.assertEqual(len(saved['rows']),117)
            self.assertEqual(len(set(judgment_keys)),39)
            report=Path(folder)/'result.md'
            write_report(Path(folder),out,config,cli.load_complete(out,config),report)
            self.assertIn('39/39题',report.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
