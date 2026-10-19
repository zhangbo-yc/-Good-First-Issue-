import copy
import json
import unittest

from gfi_prototype.research.client import ResearchSession
from gfi_prototype.research.pipeline import run_variant


def sample_packet():
    return {'repo': 'a/b', 'issue_number': 1, 'title': 'Widget error', 'source_commit': 'a' * 40,
        'evidence': [{'id': 'I1', 'kind': 'issue', 'text': 'Fix Widget errors', 'url': 'https://github.com/a/b/issues/1'},
                     {'id': 'E1', 'kind': 'source', 'path': 'pkg/widget.py', 'text': 'class Widget: pass',
                      'start_line': 1, 'end_line': 1, 'url': 'https://github.com/a/b/blob/' + 'a' * 40 + '/pkg/widget.py'}],
        'source_evidence_found': 1}


class ScriptedClient:
    model = 'deepseek-test'
    def __init__(self, always_insufficient=False):
        self.audits = 0
        self.always_insufficient = always_insufficient

    def complete(self, messages):
        if '证据审查器' in messages[0]['content']:
            self.audits += 1
            content = json.loads(messages[1]['content'])
            value = {'reviews': [{'claim_id': c['id'], 'status':
                'insufficient' if c['id'] == 'technology:0' and (self.audits == 1 or self.always_insufficient) else 'sufficient',
                'reason': '需要补充针对Widget异常行为的实现', 'evidence_ids': c['evidence_ids'],
                'missing_information': ['缺少异常行为的具体实现'], 'retrieval_query': 'WidgetError'} for c in content['claims']]}
        else:
            value = {'schema_version': 2, 'task_goal': {'text': '修正部件异常行为', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                'task_scope': '修正局部错误信息', 'technology_stack': [{'technology_id': 'python', 'name': 'Python',
                    'category': 'language', 'role_in_task': '修改Python异常处理', 'evidence_ids': ['I1'], 'basis': 'inferred'}],
                'required_knowledge': ['异常处理'], 'related_files': [], 'related_tests': [],
                'suitable_background': '能阅读Python类和测试', 'newcomer_stage': {'stage': '具备开发基础',
                    'entry_conditions': ['能阅读类定义'], 'rationale': '需要修改并验证异常行为', 'evidence_ids': ['I1']},
                'getting_started': ['阅读目标类和测试'], 'acceptance_criteria': [],
                'unknowns': ['具体边界需要确认'], 'evidence': ['I1'], 'demo_assumption': True}
        return {'content': json.dumps(value), 'model': self.model,
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}, 'elapsed_seconds': .1}


class ResearchPipelineTests(unittest.TestCase):
    def test_issue_only_baseline_does_not_receive_source_material(self):
        result = run_variant(sample_packet(), None, ResearchSession(ScriptedClient()), 'A')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([r['id'] for r in result['packet']['evidence']], ['I1'])
        self.assertIsNone(result['audit'])
        self.assertEqual(result['resources']['model_calls'], 1)

    def test_support_check_is_an_ablation_without_retrieval(self):
        result = run_variant(sample_packet(), None, ResearchSession(ScriptedClient()), 'C')
        self.assertEqual(result['gated']['requirements'], [])
        self.assertEqual(result['resources']['model_calls'], 2)
        self.assertEqual(len(result['rounds']), 1)

    def test_adaptive_retrieval_targets_missing_acceptance_and_exposes_inference(self):
        class Client(ScriptedClient):
            def complete(self, messages):
                if '证据审查器' in messages[0]['content']:
                    self.audits += 1
                    claims = json.loads(messages[1]['content'])['claims']
                    value = {'reviews': []}
                    for c in claims:
                        if c['id'].startswith('acceptance:') and self.audits == 1:
                            value['reviews'].append({'claim_id': c['id'], 'status': 'partial',
                                'reason': 'Issue给出了行为目标，但缺少实现和回归测试证据。',
                                'evidence_ids': c['evidence_ids'], 'missing_information': ['需核对方向对齐的坐标断言'],
                                'retrieval_query': 'align_to RIGHT coordinate test'})
                        else:
                            value['reviews'].append({'claim_id': c['id'], 'status':
                                'reasoned_inference' if c['id'] == 'stage' else 'sufficient',
                                'reason': 'Issue和代码依据支持此判断。', 'evidence_ids': c['evidence_ids'],
                                'missing_information': [], 'retrieval_query': ''})
                    return {'content': json.dumps(value), 'model': self.model,
                            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
                value = {'schema_version': 2, 'task_goal': {'text': '修正部件异常行为', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                    'task_scope': '修正对齐行为并验证', 'technology_stack': [{'technology_id': 'python', 'name': 'Python',
                    'category': 'language', 'role_in_task': '修改Python方法并验证坐标结果', 'evidence_ids': ['I1'], 'basis': 'inferred'}],
                    'required_knowledge': ['Python方法和方向向量'], 'related_files': [], 'related_tests': [],
                    'suitable_background': '能阅读Python类定义和测试', 'newcomer_stage': {'stage': '具备开发基础',
                    'entry_conditions': ['能读写简单类方法'], 'rationale': '任务需要修改方法并验证结果', 'evidence_ids': ['I1']},
                    'getting_started': ['阅读复现代码'], 'acceptance_criteria': [{'text': '建议验证RIGHT方向移动后水平坐标对齐',
                    'evidence_ids': ['I1'], 'basis': 'inferred'}], 'unknowns': ['具体实现仍待核对'],
                    'evidence': ['I1'], 'demo_assumption': True}
                return {'content': json.dumps(value), 'model': self.model,
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}

        class Retriever:
            seen = None
            def supplement(self, packet, queries, limit=4):
                self.seen = queries
                enriched = copy.deepcopy(packet)
                enriched['evidence'].append({'id': 'E2', 'kind': 'test', 'path': 'tests/test_align.py',
                    'text': 'assert moved.x == target.x', 'start_line': 1, 'end_line': 1,
                    'url': 'https://github.com/a/b/blob/' + 'a' * 40 + '/tests/test_align.py'})
                return enriched, ['E2']
        retriever = Retriever()
        result = run_variant(sample_packet(), retriever, ResearchSession(Client()), 'D')
        self.assertTrue(any('align_to RIGHT coordinate test' in q for q in retriever.seen))
        self.assertEqual(result['gated']['stage']['stage'], '具备开发基础')
        acceptance = result['gated']['acceptance']
        self.assertEqual(len(acceptance), 1)
        self.assertEqual(acceptance[0]['basis'], 'inferred')
        self.assertEqual(result['gated']['acceptance_labels'],
                         {'显式要求': [], '建议验证项': ['建议验证RIGHT方向移动后水平坐标对齐']})

    def test_adaptive_method_stops_when_retrieval_makes_no_progress(self):
        class Retriever:
            def supplement(self, packet, queries, limit=4):
                return packet, []
        result = run_variant(sample_packet(), Retriever(), ResearchSession(ScriptedClient()), 'D')
        self.assertEqual(result['stop_reason'], 'no_new_evidence')
        self.assertEqual(result['resources']['model_calls'], 2)

    def test_adaptive_method_adds_evidence_then_reaudits_before_output(self):
        class Retriever:
            def supplement(self, packet, queries, limit=4):
                packet = copy.deepcopy(packet)
                packet['evidence'].append({'id': 'E2', 'kind': 'test', 'path': 'tests/test_widget.py', 'text': 'WidgetError'})
                return packet, ['E2']
        result = run_variant(sample_packet(), Retriever(), ResearchSession(ScriptedClient()), 'D')
        self.assertEqual(result['stop_reason'], 'all_claims_supported')
        self.assertEqual(len(result['rounds']), 2)
        self.assertEqual(result['resources']['model_calls'], 4)
        self.assertEqual(len(result['gated']['requirements']), 1)

    def test_budget_does_not_publish_an_unaudited_checked_condition(self):
        result = run_variant(sample_packet(), None, ResearchSession(ScriptedClient(), max_calls=1), 'C')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['gated'])
        self.assertEqual(result['resources']['model_calls'], 1)

    def test_adaptive_round_limit_is_enforced(self):
        class Retriever:
            counter = 1
            def supplement(self, packet, queries, limit=4):
                self.counter += 1
                enriched = copy.deepcopy(packet)
                enriched['evidence'].append({'id': f'E{self.counter}', 'kind': 'source',
                                            'path': f'pkg/{self.counter}.py', 'text': 'WidgetError'})
                return enriched, [f'E{self.counter}']
        result = run_variant(sample_packet(), Retriever(), ResearchSession(ScriptedClient(True)), 'D', max_rounds=2)
        self.assertEqual(len(result['rounds']), 3)
        self.assertEqual(result['stop_reason'], 'supplement_round_limit')
        self.assertEqual(result['resources']['model_calls'], 6)


if __name__ == '__main__':
    unittest.main()
