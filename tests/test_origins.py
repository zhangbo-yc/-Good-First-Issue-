import unittest
from gfi_prototype.origins import origin_label

class OriginTests(unittest.TestCase):
    def test_known_and_unknown_origins(self):
        self.assertEqual(origin_label('real_model_analysis_v2'), '真实模型分析')
        self.assertEqual(origin_label('existing_assistant_task_draft_with_rule_normalization'), '助手草稿（规则归一化）')
        with self.assertRaises(ValueError):
            origin_label('invented_origin')
