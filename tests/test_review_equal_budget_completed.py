import copy
import hashlib
import json
import io
from contextlib import redirect_stdout
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_equal_budget_completed as review
import run_equal_budget_experiment as base
from gfi_prototype.common import atomic_json


class CompletedReviewTests(unittest.TestCase):
    def fixture(self, folder):
        config={'config_sha256':'config','task_ids':['a/b#1','a/b#2'], 'judge_model':'deepseek-test'}
        for method in ('R','S','D'):
            value={'task_id':'a/b#1','variant':method,'cache_key':base.identity(config,'a/b#1',method),
                   'status':'complete','errors':[], 'resources':{'model_calls':2,'total_tokens':10,'usage_complete':True}}
            atomic_json(base.result_path(folder,'a/b#1',method),value)
        atomic_json(base.result_path(folder,'a/b#2','D'), {'task_id':'a/b#2','variant':'D',
            'cache_key':base.identity(config,'a/b#2','D'),'status':'failed',
            'errors':[{'error_type':'ValueError'}]})
        return config

    def test_selects_complete_triples_and_retains_failed_task(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            config=self.fixture(output)
            results, cohort=review.prepare_cohort(output,config)
            self.assertEqual(len(results),3)
            self.assertEqual(cohort['included_task_ids'],['a/b#1'])
            self.assertEqual(cohort['excluded_tasks'][0]['task_id'],'a/b#2')
            self.assertEqual(cohort['excluded_tasks'][0]['methods']['D']['status'],'failed')
            self.assertEqual(cohort['excluded_tasks'][0]['methods']['R']['status'],'not_generated')
            self.assertEqual(cohort['planned_tasks'],2)
            self.assertEqual(cohort['completion_rate'],0.5)

    def test_frozen_cohort_rejects_changed_completed_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            config=self.fixture(output)
            review.prepare_cohort(output,config)
            path=base.result_path(output,'a/b#1','R')
            value=base.read(path)
            value['changed']='yes'
            atomic_json(path,value)
            with self.assertRaises(ValueError):
                review.prepare_cohort(output,config)

    def test_pending_report_does_not_claim_effectiveness(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            config=self.fixture(output)
            results,cohort=review.prepare_cohort(output,config)
            path=output/'report.md'
            review.write_report(output,results,cohort,path)
            text=path.read_text(encoding='utf-8')
            self.assertIn('1/2',text)
            self.assertIn('a/b#2',text)
            self.assertIn('选择偏差',text)
            self.assertIn('尚未完成评分',text)

    def test_34_complete_tasks_review_102_answers_and_keep_five_failures(self):
        import judge_experiments as judge
        from tests.test_research_pipeline import ScriptedClient, sample_packet
        from gfi_prototype.research.client import ResearchSession
        from gfi_prototype.research.pipeline import run_variant
        template=run_variant(sample_packet(),None,ResearchSession(ScriptedClient(),attempts=1),'C')
        ids=[f'a/b#{i}' for i in range(1,40)]
        config={'config_sha256':'frozen','protocol_sha256':'protocol','task_ids':ids,
                'model':'deepseek-test','judge_model':'deepseek-pro-test'}
        protocol={'seed':1,'tasks':[{'task_id':task,'title':'Fix widget','repo':'a/b',
                  'issue_number':i,'source_commit':'a'*40} for i,task in enumerate(ids,1)]}
        def cached(folder,client,prompt,payload,validator):
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            if prompt==judge.REFERENCE_PROMPT:
                value={'goal':'依据材料解释任务','requirements':[],'acceptance':[],'unknowns':[]}
            else:
                value={'claims':[{'id':key,'correctness':'uncertain','citation_support':'uncertain',
                                  'reason':'离线测试','evidence_ids':[]} for key in payload['claim_ids']],
                       'scores':{key:3 for key in judge.DIMENSIONS},'missing_points':[], 'summary':'离线测试'}
            return {'result':validator(value),'request_sha256':digest}
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            for task in ids[:34]:
                for method in ('R','S','D'):
                    value={**copy.deepcopy(template),'task_id':task,'variant':method,
                           'cache_key':base.identity(config,task,method)}
                    atomic_json(base.result_path(output,task,method),value)
            results,cohort=review.prepare_cohort(output,config)
            with patch.object(judge,'ProClient',return_value=object()), \
                    patch.object(judge,'request_cached',side_effect=cached), redirect_stdout(io.StringIO()):
                review.run_review(output,protocol,config,results,cohort,'offline')
            scores=base.read(output/review.REVIEW_FOLDER/'scores.json')
            self.assertTrue(scores['complete'])
            self.assertEqual(len(scores['rows']),102)
            self.assertEqual(len(cohort['excluded_tasks']),5)
            report=output/'report.md'
            review.write_report(output,results,cohort,report)
            text=report.read_text(encoding='utf-8')
            self.assertIn('34/39',text)
            self.assertIn('34/34',text)
            self.assertIn('完整样本评分已结束',text)

if __name__=='__main__':
    unittest.main()
