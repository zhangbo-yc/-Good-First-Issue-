import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('langchain_core'), 'Install prototype dependencies in .venv to run these tests')
class AnalysisV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retrieval = cls.analysis_v2 = None
        if (ROOT/'gfi_prototype/retrieval.py').exists():
            from gfi_prototype import retrieval, analysis_v2
            cls.retrieval, cls.analysis_v2 = retrieval, analysis_v2

    def setUp(self):
        self.assertIsNotNone(self.retrieval, 'Single-task evidence prototype is missing')
        self.assertIsNotNone(self.analysis_v2, 'Versioned schema v2 module is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'example__project'
        self.folder.mkdir()
        sha='a'*40
        archive=self.folder/f'source-{sha}.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('repo/pkg/curves.py','class CurvesAsSubmobjects:\n    def throw_error_if_no_points(self):\n        raise ValueError(type(self).__name__)\n')
            z.writestr('repo/tests/test_curves.py','def test_CurvesAsSubmobjects():\n    assert "CurvesAsSubmobjects"\n')
            z.writestr('repo/.env','API_KEY=PRIVATE_DO_NOT_INDEX')
        from collect_gfi import sha256_file
        (self.folder/'source_snapshot.json').write_text(json.dumps({'repo':'example/project','commit_sha':sha,
            'archive':archive.name,'archive_sha256':sha256_file(archive)}),encoding='utf-8')
        (self.folder/'issues').mkdir()
        (self.folder/'issues/1.json').write_text(json.dumps({'repo':'example/project','complete':True,'fetched_at':'2026-10-16T00:00:00Z',
            'issue':{'number':1,'title':'Fix CurvesAsSubmobjects error','body':'Check `CurvesAsSubmobjects` and `throw_error_if_no_points` in pkg/curves.py.',
                     'state':'open','html_url':'https://github.com/example/project/issues/1','labels':[{'name':'good first issue'}]},'comments':[]}),encoding='utf-8')

    def packet(self):
        store=self.retrieval.EvidenceStore(self.folder)
        store.build_index()
        return store.evidence_bundle(1)

    def response(self,packet):
        code=next(e for e in packet['evidence'] if e.get('path')=='pkg/curves.py')
        test=next(e for e in packet['evidence'] if e.get('kind')=='test')
        return {'schema_version':2,
                'task_goal':{'text':'核对空对象错误中的动态类名','evidence_ids':['I1'],'basis':'explicit'},
                'task_scope':'只修正错误消息里的类名来源，不改变公开接口',
                'technology_stack':[{'technology_id':'python-3','name':'Python 3','category':'编程语言',
                                     'role_in_task':'用于读取类定义与异常处理写法','evidence_ids':[code['id']],'basis':'explicit'}],
                'required_knowledge':['Python 类与继承','异常消息构造'],
                'related_files':[{'path':'pkg/curves.py','evidence_ids':[code['id']]}],
                'related_tests':[{'path':'tests/test_curves.py','evidence_ids':[test['id']]}],
                'suitable_background':'有 Python 基础并读过面向对象章节',
                'newcomer_stage':{'stage':'基础入门','entry_conditions':['能读懂类定义与异常'],
                                  'rationale':'改动范围限于单个错误消息','evidence_ids':['I1']},
                'getting_started':['先读 pkg/curves.py 中的类定义','再运行 tests/test_curves.py 验证改动'],
                'acceptance_criteria':[{'text':'建议错误消息包含真实子类名','evidence_ids':['I1',code['id']],'basis':'inferred'}],
                'unknowns':['最新PR与认领状态未核验'],
                'evidence':['I1',code['id'],test['id']],
                'demo_assumption':True}

    def test_v2_rejects_unknown_fields(self):
        packet=self.packet(); result=self.response(packet)
        result['success_probability']=0.99
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)

    def test_v2_citations_and_paths_must_match(self):
        packet=self.packet()
        result=self.response(packet)
        result['technology_stack'][0]['evidence_ids']=['INVENTED']
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)
        result=self.response(packet)
        result['evidence']=['INVENTED']
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)
        result=self.response(packet)
        result['related_tests'][0]['path']='tests/other_test.py'
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)

    def test_v2_stage_and_demo_assumption_pinned(self):
        packet=self.packet()
        for stage in ('基础入门','具备开发基础','已有一定项目经验','信息不足'):
            result=self.response(packet)
            result['newcomer_stage']['stage']=stage
            self.assertEqual(self.analysis_v2.validate_analysis_v2(result,packet).newcomer_stage.stage,stage)
        result=self.response(packet)
        result['newcomer_stage']['stage']='expert'
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)
        result=self.response(packet)
        result['demo_assumption']=False
        with self.assertRaises(ValueError):
            self.analysis_v2.validate_analysis_v2(result,packet)

    def test_v2_pipeline_retries_then_raises(self):
        from langchain_core.runnables import RunnableLambda
        packet=self.packet(); valid=self.response(packet); calls=[]
        def model(prompt):
            calls.append(prompt)
            return json.dumps({'bad':'shape'} if len(calls)==1 else valid)
        result=self.analysis_v2.analyze_packet_v2(packet,RunnableLambda(model),max_attempts=2)
        self.assertEqual(result['attempts'],2)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['validation_failures'],['ValidationError'])
        calls.clear()
        def invalid(prompt):
            calls.append(prompt)
            return '{"not":"valid"}'
        with self.assertRaises(ValueError):
            self.analysis_v2.analyze_packet_v2(packet,RunnableLambda(invalid),max_attempts=2)
        self.assertEqual(len(calls),2)

    def test_v2_wrapper_records_schema_and_prompt_version(self):
        from langchain_core.runnables import RunnableLambda
        packet=self.packet(); valid=self.response(packet)
        result=self.analysis_v2.analyze_packet_v2(packet,RunnableLambda(lambda prompt: json.dumps(valid)))
        self.assertEqual(result['schema_version'],2)
        self.assertEqual(result['prompt_version'],'analysis_v2_2026-10-16')
        self.assertEqual(result['validation_scope'],'schema_and_citation_references_only')
        self.assertEqual(result['semantic_review_status'],'pending_human_review')
        self.assertFalse(result['is_ground_truth'])
        self.assertFalse(result['current_availability_verified'])
        self.assertEqual(result['analysis']['schema_version'],2)

    def test_file_reference_repair_explains_source_only_path_rule(self):
        from langchain_core.runnables import RunnableLambda
        packet = self.packet()
        valid = self.response(packet)
        invalid = json.loads(json.dumps(valid))
        invalid['related_files'][0]['evidence_ids'] = ['I1']
        prompts = []
        def model(prompt):
            prompts.append(str(prompt))
            if 'ONLY source evidence' in str(prompt):
                return json.dumps(valid)
            return json.dumps(invalid)
        result = self.analysis_v2.analyze_packet_v2(packet, RunnableLambda(model), max_attempts=2)
        self.assertEqual(result['attempts'], 2)
        self.assertIn('ONLY source evidence', prompts[-1])

    def test_v2_report_keeps_manual_review_notice_and_sections(self):
        from langchain_core.runnables import RunnableLambda
        packet=self.packet(); valid=self.response(packet)
        result=self.analysis_v2.analyze_packet_v2(packet,RunnableLambda(lambda prompt: json.dumps(valid)))
        self.analysis_v2.write_report_v2(self.folder,packet,result)
        report=(self.folder/'ANALYSIS_REPORT.md').read_text(encoding='utf-8')
        self.assertIn('自动核验仅覆盖结构/引用，语义待人工复核',report)
        for section in ('## 任务目标','## 任务范围','## 技术栈','## 所需知识','## 适合背景','## 新手阶段','## 开始与验证',
                        '## 相关文件','## 相关测试','## 验收条件','## 未知项','## 证据位置'):
            self.assertIn(section,report)
        self.assertIn('用于读取类定义与异常处理写法',report)
        self.assertIn('进入条件',report)
        for excluded in ('analysis_origin','issue_content_sha256','prompt_version'):
            self.assertNotIn(excluded,report)


if __name__=='__main__':
    unittest.main()
