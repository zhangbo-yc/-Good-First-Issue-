import copy
import unittest

from gfi_prototype.research.evidence import audit_analysis, extract_claims, gate_analysis, quality_score


def analysis_fixture():
    return {'task_goal': {'text': '修正部件错误信息', 'evidence_ids': ['I1'], 'basis': 'explicit'},
        'technology_stack': [
            {'name': 'Python', 'role_in_task': '调整类中的异常文本', 'evidence_ids': ['E1'], 'basis': 'inferred'},
            {'name': 'Qt', 'role_in_task': '调整界面事件', 'evidence_ids': ['E2'], 'basis': 'inferred'}],
        'newcomer_stage': {'stage': '基础入门', 'rationale': '局部修改', 'entry_conditions': ['能读类'],
                          'evidence_ids': ['E1']},
        'acceptance_criteria': [{'text': '子类名称正确', 'evidence_ids': ['I1'], 'basis': 'inferred'}],
        'unknowns': ['边界条件待确认']}


def packet_fixture():
    return {'repo': 'a/b', 'issue_number': 1, 'source_commit': 'a' * 40,
            'evidence': [{'id': 'I1', 'kind': 'issue', 'text': 'Fix wrong class name'},
                         {'id': 'E1', 'kind': 'source', 'path': 'pkg/widget.py', 'text': 'class Widget: pass'},
                         {'id': 'E2', 'kind': 'document', 'path': 'deps.txt', 'text': 'Qt is optional'}]}


class EvidenceAuditTests(unittest.TestCase):
    def test_grounded_inference_can_survive_gate_with_distinct_status(self):
        raw = analysis_fixture()
        rows = self.reviews(raw)
        rows = [{**r, 'status': 'reasoned_inference'} if r['claim_id'] in {'stage', 'acceptance:0'} else r
                for r in rows]
        audit = audit_analysis(raw, packet_fixture(), self.session(rows))
        gated = gate_analysis(raw, audit)
        self.assertEqual(gated['stage']['stage'], '基础入门')
        self.assertEqual(len(gated['acceptance']), 1)
        self.assertEqual(gated['acceptance'][0]['basis'], 'inferred')
        self.assertNotIn('stage', gated['withheld_claim_ids'])

    def test_unbased_inference_still_withheld_and_requests_evidence(self):
        raw = analysis_fixture()
        rows = self.reviews(raw)
        rows = [{**r, 'status': 'partial', 'missing_information': ['需读取实际实现'],
                 'retrieval_query': 'align_to implementation'} if r['claim_id'] == 'acceptance:0' else r
                for r in rows]
        audit = audit_analysis(raw, packet_fixture(), self.session(rows))
        gated = gate_analysis(raw, audit)
        self.assertEqual(gated['acceptance'], [])
        self.assertIn('acceptance:0', gated['withheld_claim_ids'])

    def reviews(self, analysis=None):
        return [{'claim_id': c['id'], 'status': 'insufficient' if c['id'] in {'technology:1', 'stage'} else 'sufficient',
                 'reason': '现有证据可支持此结论' if c['id'] not in {'technology:1', 'stage'} else '项目依赖不能证明本任务需要该技术',
                 'evidence_ids': c['evidence_ids'], 'missing_information': ['需定位任务涉及的界面实现']
                 if c['id'] == 'technology:1' else [], 'retrieval_query': 'Widget Qt events'
                 if c['id'] == 'technology:1' else ''} for c in extract_claims(analysis or analysis_fixture())]

    def session(self, reviews):
        class Session:
            def request(self, messages, validator, purpose):
                return validator({'reviews': reviews})
        return Session()

    def test_gate_withholds_unsupported_technology_and_stage_without_mutating_raw(self):
        raw = analysis_fixture()
        before = copy.deepcopy(raw)
        audit = audit_analysis(raw, packet_fixture(), self.session(self.reviews()))
        gated = gate_analysis(raw, audit)
        self.assertEqual(gated['goal']['text'], '修正部件错误信息')
        self.assertEqual(gated['goal']['evidence_ids'], ['I1'])
        self.assertEqual([t['name'] for t in gated['requirements']], ['Python'])
        self.assertEqual(gated['stage']['stage'], '信息不足')
        self.assertIn('technology:1', gated['withheld_claim_ids'])
        self.assertTrue(any('界面实现' in text for text in gated['unknowns']))
        self.assertEqual(raw, before)
        self.assertAlmostEqual(quality_score(audit), 0.5)

    def test_auditor_cannot_borrow_unrelated_evidence_to_mark_claim_supported(self):
        reviews = self.reviews()
        reviews[0]['evidence_ids'] = ['E2']
        with self.assertRaises(ValueError):
            audit_analysis(analysis_fixture(), packet_fixture(), self.session(reviews))

    def test_missing_and_duplicate_claim_reviews_are_rejected(self):
        rows = self.reviews()
        for altered in (rows[:-1], rows + [rows[0]]):
            with self.assertRaises(ValueError):
                audit_analysis(analysis_fixture(), packet_fixture(), self.session(altered))

    def test_comment_only_support_is_not_sufficient(self):
        raw, packet = analysis_fixture(), packet_fixture()
        packet['evidence'][0]['kind'] = 'comment'
        with self.assertRaises(ValueError):
            audit_analysis(raw, packet, self.session(self.reviews(raw)))

    def test_unknown_quality_is_not_fabricated_as_a_perfect_score(self):
        self.assertIsNone(quality_score({'reviews': []}))


if __name__ == '__main__':
    unittest.main()
