"""Behavioral tests for the independent research ranking methods."""
import copy
import math
import unittest

from gfi_prototype.research.directional import rank_directional, rank_random, explain_exclusions


def profile(number, tags=(('python', 1),), paths=()):
    return {'task_id': f'a/b#{number}', 'repo': 'a/b', 'issue_number': number,
            'title': 'Fix a concrete indexing error', 'goal': 'Correct indexing behavior',
            'url': f'https://github.com/a/b/issues/{number}', 'source_commit': 'a' * 40,
            'technology_tags': [{'id': key, 'name': key, 'weight': weight, 'evidence_ids': ['I1']}
                                for key, weight in tags],
            'evidence': [{'id': 'I1', 'url': f'https://github.com/a/b/issues/{number}'}],
            'source_paths': list(paths)}


class DirectionalTests(unittest.TestCase):
    def rank(self, source, candidates, **kwargs):
        return rank_directional(source, candidates,
                                {p['task_id']: .8 for p in [source] + candidates}, **kwargs)

    def test_target_coverage_is_asymmetric(self):
        a, b = profile(1), profile(2, (('python', 1), ('numpy', 3)))
        ab, ba = self.rank(a, [b])[0], self.rank(b, [a])[0]
        self.assertEqual(ab['components']['reuse'], .25)
        self.assertEqual(ba['components']['reuse'], 1)
        self.assertLess(ab['score'], ba['score'])

    def test_equal_coverage_has_distinct_absolute_load(self):
        a = profile(1, (('python', 1), ('numpy', 3)))
        small = profile(2, (('python', 1), ('pytest', 1)))
        large = profile(3, (('numpy', 3), ('pandas', 3)))
        rows = {row['task_id']: row for row in self.rank(a, [small, large])}
        self.assertEqual(rows['a/b#2']['components']['reuse'], .5)
        self.assertEqual(rows['a/b#3']['components']['reuse'], .5)
        self.assertLess(rows['a/b#2']['components']['novelty_burden'],
                        rows['a/b#3']['components']['novelty_burden'])

    def test_new_source_modules_add_burden_but_docs_and_tests_do_not(self):
        a = profile(1, paths=['src/pkg/array/core.py'])
        b = profile(2, paths=['src/pkg/image/core.py', 'tests/widget/test_it.py', 'docs/topic/a.md'])
        row = self.rank(a, [b])[0]
        self.assertEqual(row['new_modules'], ['pkg/image'])
        self.assertGreater(row['components']['novelty_burden'], 0)

    def test_only_blob_commit_urls_supply_modules(self):
        a, b = profile(1, paths=['pkg/base/core.py']), profile(2)
        b['evidence'].extend([
            {'id': 'S1', 'url': 'https://github.com/a/b/blob/' + 'a'*40 + '/pkg/array/core.py#L2'},
            {'id': 'S2', 'url': 'https://github.com/a/b/issues/2?path=pkg/fake.py'},
            {'id': 'S3', 'url': 'https://github.com/a/b/blob/main/pkg/branch/core.py'}])
        self.assertEqual(self.rank(a, [b])[0]['new_modules'], ['pkg/array'])

    def test_absent_source_paths_leave_module_comparison_unknown(self):
        a, b = profile(1), profile(2, paths=['pkg/new/core.py'])
        row = self.rank(a, [b])[0]
        self.assertFalse(row['module_information_available'])
        self.assertIsNone(row['module_burden'])
        self.assertEqual(row['new_modules'], [])
        self.assertEqual(row['module_missing_policy'], 'neutral_prior_0.5')

    def test_unknown_module_information_does_not_gain_zero_burden_advantage(self):
        a = profile(1, paths=['pkg/base/core.py'])
        unknown = profile(2)
        known_new = profile(3, paths=['pkg/new/core.py'])
        rows = {row['task_id']: row for row in self.rank(a, [unknown, known_new])}
        self.assertLessEqual(rows['a/b#2']['score'], rows['a/b#3']['score'])

    def test_missing_audits_are_excluded_with_reason(self):
        a, b = profile(1), profile(2)
        self.assertEqual(rank_directional(a, [b], {a['task_id']: .8}), [])
        self.assertEqual(explain_exclusions(a, [b], {a['task_id']: .8})[0]['reason'],
                         'missing_candidate_evidence_quality')
        self.assertEqual(rank_directional(a, [b], {b['task_id']: .8}), [])

    def test_quality_uses_weaker_of_both_audits_and_explains_generic_relation(self):
        a, b = profile(1), profile(2)
        row = rank_directional(a, [b], {'a/b#1': .3, 'a/b#2': .9})[0]
        self.assertEqual(row['components']['evidence_quality'], .3)
        self.assertAlmostEqual(row['score'], .5225)
        self.assertEqual(row['match_strength'], 'generic_only')
        self.assertEqual(row['source_evidence_quality'], .3)
        self.assertEqual(row['candidate_evidence_quality'], .9)
        self.assertEqual(row['shared_technologies'][0]['id'], 'python')
        self.assertIn('not_verified', row['relation_claim'])

    def test_filters_nonself_other_repo_and_guidance(self):
        a, b, c, d = [profile(i) for i in range(1, 5)]
        c['repo'] = 'x/y'
        d['record_type'] = 'guidance'
        self.assertEqual([r['task_id'] for r in self.rank(a, [a, b, c, d])], ['a/b#2'])

    def test_guidance_source_cannot_anchor_recommendations(self):
        a, b = profile(1), profile(2)
        a['record_type'] = 'guidance'
        self.assertEqual(self.rank(a, [b]), [])
        self.assertEqual(rank_random(a, [b]), [])

    def test_unmapped_requirements_are_explicit_and_exclude(self):
        a, b = profile(1), profile(2)
        b['unmapped_requirement_indexes'] = [2]
        self.assertEqual(self.rank(a, [b]), [])
        reasons = explain_exclusions(a, [b], {'a/b#1': .8, 'a/b#2': .8})
        self.assertEqual(reasons[0]['reason'], 'unmapped_candidate_technologies')

    def test_duplicate_task_or_technology_ids_are_rejected(self):
        a, b = profile(1), profile(2)
        with self.assertRaises(ValueError):
            self.rank(a, [b, b])
        b['technology_tags'].append(copy.deepcopy(b['technology_tags'][0]))
        with self.assertRaises(ValueError):
            self.rank(a, [b])

    def test_invalid_tag_weights_and_mismatches_raise(self):
        a = profile(1)
        for value in (0, -1, math.nan, math.inf, True, None, 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.rank(a, [profile(2, (('python', value),))])

    def test_candidate_only_rule_weights_must_also_agree(self):
        a = profile(1)
        b = profile(2, (('numpy', 3),))
        c = profile(3, (('numpy', 2),))
        with self.assertRaises(ValueError):
            self.rank(a, [b, c])

    def test_nonfinite_or_out_of_range_quality_raises(self):
        a, b = profile(1), profile(2)
        for value in (math.nan, math.inf, -.1, 1.1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rank_directional(a, [b], {'a/b#1': .8, 'a/b#2': value})

    def test_invalid_scoring_weights_raise(self):
        for weights in ({'reuse': math.nan, 'burden': .3, 'evidence': .2},
                        {'reuse': -1, 'burden': .3, 'evidence': .2}, {'reuse': 1}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                self.rank(profile(1), [profile(2)], weights=weights)

    def test_top_k_validated_for_both_methods(self):
        for k in (0, -1, 11, True, 1.5):
            with self.subTest(k=k), self.assertRaises(ValueError):
                self.rank(profile(1), [profile(2)], top_k=k)
            with self.subTest(k=k), self.assertRaises(ValueError):
                rank_random(profile(1), [profile(2)], top_k=k)

    def test_ties_and_random_are_order_independent_and_reproducible(self):
        a = profile(1)
        candidates = [profile(i) for i in range(2, 12)]
        self.assertEqual([r['task_id'] for r in self.rank(a, candidates)], ['a/b#10', 'a/b#11', 'a/b#2'])
        self.assertEqual(rank_random(a, candidates, seed=42), rank_random(a, list(reversed(candidates)), seed=42))
        self.assertNotEqual(rank_random(a, candidates, seed=42), rank_random(a, candidates, seed=43))
        self.assertEqual(len(rank_random(a, candidates)), 3)

    def test_random_includes_no_overlap_and_uses_same_structural_eligibility(self):
        a, b = profile(1), profile(2, (('numpy', 3),))
        self.assertEqual(rank_random(a, [a, b])[0]['task_id'], b['task_id'])
        self.assertEqual(self.rank(a, [b])[0]['components']['reuse'], 0)
        with self.assertRaises(ValueError):
            rank_random(a, [b, b])


if __name__ == '__main__':
    unittest.main()

