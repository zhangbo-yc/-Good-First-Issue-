"""Offline tests only; these never invoke a model."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from judge_experiments import (validate_judgment, make_report, parse_stream,
                               enrich_prediction, judge_payload, DIMENSIONS, review_stages)


class JudgeTests(unittest.TestCase):
    def test_stage_supplement_does_not_rewrite_overall_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessor = root / 'blind' / 'assessor'
            assessor.mkdir(parents=True)
            (root / 'blind' / 'private').mkdir()
            prediction = {'prediction_id': 'P0001', 'task_id': 'repo#1', 'stage': {'text': '信息不足'},
                          'requirements': [], 'available_evidence_ids': ['X001'],
                          'original_guidance': {}, 'evidence': [{'id': 'X001'}]}
            for path, value in [
                (assessor / 'enriched_predictions.json', [prediction]),
                (assessor / 'sources.json', [{'task_id': 'repo#1'}]),
                (root / 'blind' / 'private' / 'mapping.json',
                 [{'prediction_id': 'P0001', 'task_id': 'repo#1', 'variant': 'A'}])]:
                path.write_text(json.dumps(value), encoding='utf8')
            (root / 'model_evaluation.md').write_text('original scores', encoding='utf8')
            def fake(directory, client, prompt, payload, validator):
                self.assertIn('无需用户个人资料', prompt)
                return {'result': validator({'reviews': [{'prediction_id': 'P0001',
                    'assessment': 'abstention_unjustified', 'reason': '需要任务证据',
                    'suggested_entry_level': '具备开发基础', 'evidence_ids': ['X001']}]})}
            with patch('judge_experiments.request_cached', side_effect=fake):
                result = review_stages(root, object())
            self.assertEqual(result['status'], 'complete')
            self.assertTrue((root / 'stage_review.md').exists())
            self.assertTrue((root / 'model_evaluation.md').read_text(encoding='utf8').startswith('original scores'))

    def test_evidence_sent_once_and_input_visibility_preserved(self):
        original = {'packet': {'evidence': [{'id': 'I1', 'kind': 'issue', 'text': 'body'}]},
                    'analysis': {'getting_started': ['Read I1'], 'required_knowledge': ['Python']},
                    'gated': {'stage': {'stage': '信息不足'}}}
        prediction = {'evidence': [{'id': 'X001', 'kind': 'issue', 'text': 'body'},
                                   {'id': 'X002', 'kind': 'source', 'text': 'code'}],
                      'goal': {}, 'stage': {}, 'requirements': [], 'acceptance': []}
        enriched = enrich_prediction(prediction, original)
        self.assertEqual(enriched['available_evidence_ids'], ['X001'])
        self.assertEqual(enriched['original_guidance']['getting_started'], ['Read X001'])
        self.assertEqual(enriched['original_guidance']['scope'], 'ungated_auxiliary_analysis')
        payload = judge_payload({'evidence': prediction['evidence']}, {}, enriched)
        self.assertNotIn('evidence', payload['prediction'])
        self.assertEqual(len(payload['source']['evidence']), 2)
        self.assertNotIn('variant', payload['prediction'])

    def test_stream_keeps_content_and_usage_ignores_keepalive(self):
        result = parse_stream(iter([
            ': keep-alive', '',
            'data: {"model":"deepseek-v4-pro","choices":[{"delta":{"reasoning_content":"thinking"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}',
            'data: {"choices":[],"usage":{"total_tokens":20}}',
            'data: [DONE]']))
        self.assertEqual(result['content'], '{}')
        self.assertEqual(result['usage']['total_tokens'], 20)

    def test_stream_disconnect_cannot_be_cached_as_success(self):
        with self.assertRaisesRegex(ValueError, 'incomplete_stream'):
            parse_stream(iter(['data: {"choices":[{"delta":{"content":"{}"}}]}']))

    def test_unknown_citation_rejected(self):
        prediction = {'goal': {'evidence_ids': ['X001']}, 'requirements': [],
                      'stage': {}, 'acceptance': []}
        response = {'claims': [{'id': 'goal', 'correctness': 'supported',
                    'citation_support': 'supported', 'reason': '依据清楚',
                    'evidence_ids': ['X999']},
                   {'id': 'stage', 'correctness': 'uncertain',
                    'citation_support': 'uncertain', 'reason': '没有判断', 'evidence_ids': []}],
                    'scores': {k: 2 for k in DIMENSIONS},
                    'missing_points': [], 'summary': '部分信息不足'}
        with self.assertRaises(ValueError):
            validate_judgment(response, prediction, {'X001'})

    def test_report_never_calls_model_scores_accuracy(self):
        report = make_report([], {}, 45)
        self.assertIn('模型辅助评审', report)
        self.assertIn('0 / 45', report)


if __name__ == '__main__':
    unittest.main()
