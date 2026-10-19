import json

import unittest
import tempfile
from pathlib import Path

import importlib.util
from types import SimpleNamespace
mod = __import__('gfi_prototype.research.evaluation', fromlist=['evaluation']) if importlib.util.find_spec('gfi_prototype.research.evaluation') else SimpleNamespace()


def test_blind_packet_and_pending_missing_annotations(tmp_path):
    protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1, 'title': 'Title', 'source_commit': 'abc'}]}
    results = [{'task_id': 'a/b#1', 'variant': 'D', 'run_id': 'private', 'status': 'complete',
                'analysis': {'goal': 'Goal', 'requirements': [{'name': 'Python', 'evidence_ids': ['I1']}], 'stage': 'early'},
                'gated': {}, 'audit': {'status': 'sufficient'}, 'resources': {'tokens': 123}}]
    mod.build_review_bundle(tmp_path, protocol, results, tmp_path / 'review')
    packet = (tmp_path / 'review' / 'assessor' / 'predictions.json').read_text()
    assert 'Goal' in packet and 'Python' in packet
    assert all(secret not in packet for secret in ['variant', 'private', 'sufficient', 'tokens', 'audit'])
    assert mod.score_reviews(tmp_path / 'review')['status'] == 'pending'
    assert mod.score_reviews(tmp_path / 'review')['requirement_precision'] is None


def test_scoring_independent_gold_and_agreement(tmp_path):
    protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1, 'title': 'Title', 'source_commit': 'abc'}]}
    results = [{'task_id': 'a/b#1', 'variant': 'A', 'run_id': 'x', 'analysis': {'requirements': ['one', 'two']}}]
    mod.build_review_bundle(tmp_path, protocol, results, tmp_path / 'review')
    pred = json.loads((tmp_path / 'review' / 'assessor' / 'predictions.json').read_text())[0]
    reviews = []
    for reviewer in ['reviewer_1', 'reviewer_2']:
        reviews.append({'reviewer_id': reviewer, 'prediction_id': pred['prediction_id'], 'stage_reasonable': True,
            'goal_correct': True, 'goal_citation_supported': True,
            'requirements': [{'requirement_id': 'R1', 'correct': True, 'citation_supported': True, 'gold_ids': ['G1']},
                             {'requirement_id': 'R2', 'correct': False, 'citation_supported': False, 'gold_ids': []}]})
    gold = [{'task_id': 'a/b#1', 'annotator_id': 'independent', 'completed': True,
             'goal': 'Goal', 'stage': '基础入门',
             'requirements': [{'id': 'G1', 'text': 'one'}, {'id': 'G2', 'text': 'missing'}], 'expected_technology_ids': []}]
    score = mod.score_reviews(tmp_path / 'review', reviews=reviews, gold=gold)
    assert score['status'] == 'complete'
    assert score['requirement_precision'] == .5
    assert score['requirement_recall'] == .5
    assert score['citation_support'] == .5
    assert score['goal_correctness'] == 1
    assert score['goal_citation_support'] == 1
    assert score['by_prediction'][0]['reviews'][0]['goal_correctness'] == 1
    assert score['cohen_kappa'] == 1
    reviews[0]['requirements'][0]['gold_ids'] = ['unknown']
    with unittest.TestCase().assertRaises(ValueError):
        mod.score_reviews(tmp_path / 'review', reviews=reviews, gold=gold)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_blind_pending(self):
        test_blind_packet_and_pending_missing_annotations(self.root)

    def test_scores(self):
        test_scoring_independent_gold_and_agreement(self.root)

    def test_recommendations_blind_and_scores_pending_until_complete(self):
        protocol = {'tasks': [{'task_id': 'a/b#1'}], 'candidate_pool': ['a/b#2', 'a/b#3']}
        out = self.root / 'recommendations'
        mod.build_recommendation_review_bundle(self.root, protocol, [
            {'method': 'directional', 'source_task_id': 'a/b#1', 'recommendations': [
                {'task_id': 'a/b#2', 'score': .99}]}], out)
        packet = (out / 'assessor' / 'recommendations.json').read_text()
        self.assertNotIn('directional', packet)
        self.assertNotIn('score', packet)
        sources = json.loads((out / 'assessor' / 'sources.json').read_text())
        self.assertEqual({row['task_id'] for row in sources}, {'a/b#1', 'a/b#2'})
        self.assertIn('issue_url', sources[0])
        self.assertIsNone(mod.score_recommendation_reviews(out)['recommendation_reasonableness'])
        forms = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            rows = json.loads((out / 'assessor' / f'{reviewer}.json').read_text())
            rows[0]['recommendations'][0].update(relevant=True, knowledge_reuse=True,
                                                  new_load_manageable=True, reasonable=True)
            forms.extend(rows)
        score = mod.score_recommendation_reviews(out, forms)
        self.assertEqual(score['recommendation_reasonableness'], 1)
        self.assertEqual(score['relevance'], 1)
        self.assertEqual(score['knowledge_reuse'], 1)
        self.assertEqual(score['new_load_manageable'], 1)
        methods = mod.compare_recommendation_methods(out, score)
        self.assertEqual(methods['by_method']['directional']['knowledge_reuse'], 1)

    def test_unblinded_comparisons_group_only_after_completed_scoring(self):
        protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1,
                              'title': 'Title', 'source_commit': 'abc'}]}
        out = self.root / 'review'
        mod.build_review_bundle(self.root, protocol, [
            {'task_id': 'a/b#1', 'variant': 'A', 'analysis': {'goal': 'A', 'requirements': ['one']}},
            {'task_id': 'a/b#1', 'variant': 'D', 'analysis': {'goal': 'D', 'requirements': ['one']}}], out)
        pending = mod.compare_variant_scores(out, mod.score_reviews(out))
        self.assertEqual(pending['status'], 'pending')
        predictions = json.loads((out / 'assessor/predictions.json').read_text())
        reviews = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            for pred in predictions:
                reviews.append({'reviewer_id': reviewer, 'prediction_id': pred['prediction_id'],
                    'stage_reasonable': True, 'goal_correct': pred['goal']['text'] == 'D',
                    'goal_citation_supported': True, 'requirements': [{'requirement_id': 'R1',
                    'correct': True, 'citation_supported': True, 'gold_ids': ['G1']}]})
        gold = [{'task_id': 'a/b#1', 'completed': True, 'annotator_id': 'human', 'goal': 'Goal',
                 'stage': '基础入门',
                 'requirements': [{'id': 'G1', 'text': 'one'}], 'expected_technology_ids': []}]
        score = mod.score_reviews(out, reviews, gold)
        compared = mod.compare_variant_scores(out, score)
        self.assertEqual(compared['by_variant']['D']['goal_correctness'], 1)
        self.assertEqual(compared['by_variant']['A']['goal_correctness'], 0)
        self.assertEqual(compared['paired_task_count'], 1)

    def test_new_gold_concept_outside_rules_is_valid_and_uncovered(self):
        (self.root / 'technology_rules.json').write_text(json.dumps({'rules': [{'id': 'python'}]}))
        protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1,
                              'title': 'Title', 'source_commit': 'abc'}]}
        out = self.root / 'review'
        mod.build_review_bundle(self.root, protocol, [{'task_id': 'a/b#1', 'variant': 'A',
            'analysis': {'requirements': []}}], out)
        gold = [{'task_id': 'a/b#1', 'completed': True, 'annotator_id': 'human', 'goal': 'Goal',
                 'stage': '基础入门',
                 'requirements': [{'id': 'G-new', 'text': 'A new concept'}],
                 'expected_technology_ids': ['new-concept']}]
        reviews = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            rows = json.loads((out / 'assessor' / f'{reviewer}.json').read_text())
            rows[0]['stage_reasonable'] = True
            rows[0]['goal_correct'] = True
            rows[0]['goal_citation_supported'] = True
            reviews.extend(rows)
        score = mod.score_reviews(out, reviews=reviews, gold=gold)
        self.assertEqual(score['requirement_recall'], 0)
        self.assertIsNone(score['requirement_precision'])

    def test_blind_stage_context_and_packet_text(self):
        protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1,
                              'title': 'Title', 'source_commit': 'abc'}]}
        out = self.root / 'review'
        mod.build_review_bundle(self.root, protocol, [{'task_id': 'a/b#1', 'variant': 'A',
            'analysis': {'newcomer_stage': {'stage': '信息不足', 'rationale': 'insufficient source context',
                         'entry_conditions': ['read source'], 'evidence_ids': ['S1']}},
            'packet': {'evidence': [{'id': 'S1', 'kind': 'source', 'text': 'def run(): pass'}]}}], out)
        packet = json.loads((out / 'assessor' / 'predictions.json').read_text())[0]
        self.assertEqual(packet['evidence'][0]['text'], 'def run(): pass')
        self.assertEqual(packet['stage']['rationale'], 'insufficient source context')
        self.assertEqual(packet['stage']['entry_conditions'], ['read source'])
        self.assertEqual(packet['stage']['eligibility'], 'abstention')

    def test_missing_goal_review_stays_pending_and_labels_validated(self):
        protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1,
                              'title': 'Title', 'source_commit': 'abc'}]}
        out = self.root / 'review'
        mod.build_review_bundle(self.root, protocol, [{'task_id': 'a/b#1', 'variant': 'A',
            'analysis': {'goal': 'Goal', 'requirements': []}}], out)
        reviews = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            rows = json.loads((out / 'assessor' / f'{reviewer}.json').read_text())
            self.assertIsNone(rows[0]['goal_correct'])
            self.assertIsNone(rows[0]['goal_citation_supported'])
            rows[0]['stage_reasonable'] = True
            reviews.extend(rows)
        gold = [{'task_id': 'a/b#1', 'completed': True, 'annotator_id': 'human',
                 'goal': None, 'stage': None,
                 'requirements': [], 'expected_technology_ids': []}]
        score = mod.score_reviews(out, reviews=reviews, gold=gold)
        self.assertEqual(score['status'], 'pending')
        self.assertIsNone(score['goal_correctness'])
        self.assertIsNone(score['goal_citation_support'])
        for i, review in enumerate(reviews):
            review['goal_correct'] = i == 0
            review['goal_citation_supported'] = True
        self.assertEqual(mod.score_reviews(out, reviews=reviews, gold=gold)['status'], 'pending')
        gold[0].update(goal='Goal', stage='基础入门')
        score = mod.score_reviews(out, reviews=reviews, gold=gold)
        self.assertEqual(score['goal_correctness'], .5)
        self.assertEqual(score['goal_citation_support'], 1)
        self.assertEqual(score['by_prediction'][0]['goal_correctness'], .5)
        self.assertEqual(score['by_prediction'][0]['goal_citation_support'], 1)
        self.assertEqual(score['by_prediction'][0]['reviews'][1]['goal_correctness'], 0)
        reviews[0]['goal_correct'] = 'yes'
        with self.assertRaises(ValueError):
            mod.score_reviews(out, reviews=reviews, gold=gold)

    def test_common_blinded_evidence_inventory_does_not_reveal_variant(self):
        protocol = {'tasks': [{'task_id': 'a/b#1', 'repo': 'a/b', 'issue_number': 1,
                              'title': 'Title', 'source_commit': 'abc'}]}
        out = self.root / 'review'
        issue = {'id': 'I1', 'kind': 'issue', 'text': 'Issue text'}
        source = {'id': 'E1', 'kind': 'source', 'path': 'pkg/a.py', 'text': 'source text'}
        mod.build_review_bundle(self.root, protocol, [
            {'task_id': 'a/b#1', 'variant': 'A', 'analysis': {'task_goal': {'text': 'Goal',
                'evidence_ids': ['I1']}}, 'packet': {'evidence': [issue]}},
            {'task_id': 'a/b#1', 'variant': 'D', 'analysis': {'task_goal': {'text': 'Goal',
                'evidence_ids': ['I1']}}, 'packet': {'evidence': [issue, source]}}], out)
        predictions = json.loads((out / 'assessor/predictions.json').read_text())
        self.assertEqual(predictions[0]['evidence'], predictions[1]['evidence'])
        self.assertEqual(len(predictions[0]['evidence']), 2)
        self.assertTrue(all(row['id'].startswith('X') for row in predictions[0]['evidence']))
        self.assertEqual(predictions[0]['goal']['evidence_ids'], predictions[1]['goal']['evidence_ids'])

    def test_empty_recommendation_list_is_an_abstention_not_a_crash(self):
        protocol = {'tasks': [{'task_id': 'a/b#1'}], 'candidate_pool': ['a/b#2']}
        out = self.root / 'empty_recommendations'
        mod.build_recommendation_review_bundle(self.root, protocol,
            [{'method': 'directional', 'source_task_id': 'a/b#1', 'recommendations': []}], out)
        forms = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            forms.extend(json.loads((out / 'assessor' / f'{reviewer}.json').read_text()))
        score = mod.score_recommendation_reviews(out, forms)
        self.assertEqual(score['status'], 'complete')
        compared = mod.compare_recommendation_methods(out, score)
        self.assertIsNone(compared['by_method']['directional']['reasonable'])
        self.assertEqual(compared['by_method']['directional']['recommended_items'], 0)
