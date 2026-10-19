"""Check paired statistics, missing scores and case identity without model calls."""
import copy
import unittest

import analyze_existing_experiments as report


def case(task_id, b, d, repo='a/b', stratum='bug'):
    scores = {variant: {key: 3 for key in report.DIMENSIONS} for variant in 'ABCD'}
    scores['B']['actionability'] = b
    scores['D']['actionability'] = d
    return {'task_id': task_id, 'repo': repo, 'stratum': stratum, 'title': task_id,
            'scores': scores, 'results': {}, 'judgments': {}, 'prediction_ids': {}}


class ExistingExperimentAnalysisTests(unittest.TestCase):
    def test_comparison_pairs_only_tasks_with_both_scores(self):
        rows = [case('a/b#1', 2, 4), case('a/b#2', 3, 3),
                case('a/b#3', 4, 3), case('a/b#4', None, 4)]
        result = report.compare_conditions(rows, 'D', 'B', 'actionability')
        self.assertEqual(result['n'], 3)
        self.assertAlmostEqual(result['mean_delta'], 1 / 3)
        self.assertEqual((result['wins'], result['ties'], result['losses']), (1, 1, 1))

    def test_overall_means_weight_tasks_instead_of_projects(self):
        rows = [case('a/b#1', 1, 4), case('a/b#2', 1, 4), case('c/d#1', 4, 1, repo='c/d')]
        result = report.group_means(rows)
        self.assertEqual(result['D']['actionability']['n'], 3)
        self.assertEqual(result['D']['actionability']['mean'], 3)

    def test_declines_are_selected_against_b_and_grouped_by_frozen_type(self):
        rows = [case('a/b#1', 4, 3, stratum='bug'), case('a/b#2', 2, 3, stratum='test')]
        result = report.analyze_records(rows)
        self.assertEqual(result['decline_task_ids'], ['a/b#1'])
        self.assertEqual(result['by_type']['bug']['n'], 1)
        self.assertEqual(result['by_project']['a/b']['n'], 2)
        self.assertEqual(rows[0]['scores']['B']['actionability'], 4)

    def test_duplicate_tasks_and_missing_conditions_are_rejected(self):
        row = case('a/b#1', 3, 4)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            report.validate_records([row, copy.deepcopy(row)])
        del row['scores']['C']
        with self.assertRaisesRegex(ValueError, 'A/B/C/D'):
            report.validate_records([row])

    def test_display_rounding_is_explicit_half_up_not_binary_tie_rounding(self):
        self.assertEqual(report.fmt(0.125, signed=True), '+0.13')
        self.assertEqual(report.fmt(0.625), '0.63')


if __name__ == '__main__':
    unittest.main()
