import unittest

from gfi_prototype.research.recommendations import profile_from_result, run_recommendation_methods, sum_attempt_resources


RULES = [{'id': 'python', 'name': 'Python', 'category': 'language', 'weight': 1, 'pattern': 'Python'},
         {'id': 'qt', 'name': 'Qt', 'category': 'framework', 'weight': 3, 'pattern': 'Qt'}]


def profile(task_id, tags):
    repo, number = task_id.rsplit('#', 1)
    evidence = [{'id': f'E{i}', 'url': f'https://github.com/{repo}/blob/' + 'a' * 40 + f'/pkg/m{i}.py'}
                for i in range(1, len(tags) + 1)]
    return {'task_id': task_id, 'repo': repo, 'issue_number': int(number), 'title': task_id,
            'url': f'https://github.com/{repo}/issues/{number}', 'goal': '完成任务', 'record_type': 'task',
            'suitable_background': 'Python基础',
            'technology_tags': [{'id': tag, 'name': tag, 'weight': 1 if tag == 'python' else 3,
                                 'evidence_ids': [f'E{i}']} for i, tag in enumerate(tags, 1)],
            'evidence': evidence, 'unmapped_requirement_indexes': []}


class RecommendationExperimentTests(unittest.TestCase):
    def result(self):
        return {'task_id': 'a/b#1', 'status': 'complete', 'variant': 'D',
            'analysis': {'task_goal': {'text': '修正部件', 'evidence_ids': ['I1'], 'basis': 'explicit'},
                'task_scope': '局部改动', 'technology_stack': [{'technology_id': 'python', 'name': 'Python',
                    'category': 'language', 'role_in_task': '修改Python实现', 'evidence_ids': ['E1'], 'basis': 'explicit'},
                    {'technology_id': 'qt', 'name': 'Qt', 'category': 'framework', 'role_in_task': '修改Qt界面',
                     'evidence_ids': ['E2'], 'basis': 'inferred'}], 'suitable_background': 'Python基础',
                'newcomer_stage': {'stage': '具备开发基础', 'entry_conditions': ['Python'], 'rationale': '局部',
                                     'evidence_ids': ['E1']}, 'getting_started': ['阅读实现'],
                'acceptance_criteria': [], 'unknowns': [], 'demo_assumption': True},
            'gated': {'goal': '修正部件', 'requirements': [{'technology_id': 'python', 'name': 'Python',
                'category': 'language', 'role_in_task': '修改Python实现', 'evidence_ids': ['E1'], 'basis': 'explicit'}],
                'stage': {'stage': '信息不足'}, 'acceptance': [], 'unknowns': [],
                'withheld_claim_ids': ['technology:1']},
            'audit': {'reviews': [{'claim_id': 'technology:0', 'status': 'sufficient'},
                                  {'claim_id': 'technology:1', 'status': 'insufficient'}]},
            'packet': {'repo': 'a/b', 'issue_number': 1, 'source_commit': 'a' * 40,
                'evidence': [{'id': 'I1', 'kind': 'issue', 'url': 'https://github.com/a/b/issues/1'},
                    {'id': 'E1', 'kind': 'source', 'path': 'pkg/a.py', 'url': 'https://github.com/a/b/blob/' + 'a' * 40 + '/pkg/a.py'},
                    {'id': 'E2', 'kind': 'source', 'path': 'gui/q.py', 'url': 'https://github.com/a/b/blob/' + 'a' * 40 + '/gui/q.py'}]}}

    def test_source_profile_uses_only_supported_requirements(self):
        source, quality = profile_from_result(self.result(), RULES)
        self.assertEqual([tag['id'] for tag in source['technology_tags']], ['python'])
        self.assertEqual(quality, 0.5)

    def test_three_methods_share_frozen_candidate_pool_and_directional_uses_quality(self):
        source, quality = profile_from_result(self.result(), RULES)
        candidates = [profile('a/b#2', ['python']), profile('a/b#3', ['python', 'qt'])]
        output = run_recommendation_methods(source, candidates,
            {source['task_id']: quality, 'a/b#2': 1.0, 'a/b#3': .5}, top_k=2, seed=3)
        self.assertEqual(set(output), {'random', 'jaccard', 'directional'})
        self.assertEqual({row['task_id'] for row in output['random']}, {'a/b#2', 'a/b#3'})
        self.assertEqual(output['directional'][0]['task_id'], 'a/b#2')
        self.assertEqual(output['directional'][0]['score_meaning'], 'transparent_utility_not_probability')

    def test_result_without_supported_requirements_is_not_ranked(self):
        value = self.result()
        value['gated']['requirements'] = []
        with self.assertRaises(ValueError):
            profile_from_result(value, RULES)

    def test_all_candidate_audit_attempt_costs_are_preserved(self):
        value = sum_attempt_resources([{'resources': {'model_calls': 2, 'total_tokens': 10,
            'usage_complete': True}}, {'resources': {'model_calls': 1, 'total_tokens': 7,
            'usage_complete': False}}])
        self.assertEqual(value['model_calls'], 3)
        self.assertEqual(value['total_tokens'], 17)
        self.assertFalse(value['usage_complete'])


if __name__ == '__main__':
    unittest.main()
