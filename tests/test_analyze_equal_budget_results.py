import unittest

from analyze_equal_budget_results import paired, select_cases, aggregate_tokens


class FinalAnalysisTests(unittest.TestCase):
    def scores(self,r,s,d):
        return {m:{'actionability':v} for m,v in [('R',r),('S',s),('D',d)]}

    def test_paired_difference_uses_raw_scores_and_reports_missing(self):
        scores={'a':self.scores(2,2,3),'b':self.scores(3,2,2),'c':self.scores(None,2,4)}
        result=paired(scores,'R',list(scores))
        self.assertEqual(result['n'],2)
        self.assertEqual(result['mean_difference'],0)
        self.assertEqual((result['wins'],result['ties'],result['losses']),(1,0,1))
        self.assertEqual(result['missing_pairs'],1)

    def test_case_rule_retains_strongest_loss_instead_of_only_winners(self):
        scores={'win':self.scores(2,2,4),'tie':self.scores(2,2,2),
                'loss_b':self.scores(2,3,2),'loss_a':self.scores(2,2,1)}
        result=select_cases(scores)
        self.assertEqual(result,{'win':'win','loss':'loss_a'})

    def test_case_tie_is_broken_by_task_id(self):
        scores={'z':self.scores(2,2,3),'a':self.scores(2,2,3)}
        self.assertEqual(select_cases(scores)['win'],'a')
        self.assertIsNone(select_cases(scores)['loss'])

    def test_token_ratio_uses_totals_not_average_of_ratios(self):
        records={'a':{m:{'resources':{'total_tokens':v}} for m,v in [('R',1),('S',1),('D',2)]},
                 'b':{m:{'resources':{'total_tokens':v}} for m,v in [('R',9),('S',9),('D',9)]}}
        result=aggregate_tokens(records)
        self.assertEqual(result['totals']['D'],11)
        self.assertAlmostEqual(result['D_over_R'],1.1)

if __name__=='__main__':
    unittest.main()
