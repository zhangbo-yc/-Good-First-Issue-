import tempfile
import unittest
from pathlib import Path

import prepare_annotation_drafts as exporter


class DraftSelectionTests(unittest.TestCase):
    def test_five_repositories_have_ten_distinct_samples_each(self):
        self.assertTrue(hasattr(exporter, 'validate_draft_selection'), 'Five-repository validation missing')
        drafts = [{'repo': repo, 'number': number} for repo in
                  ['sympy/sympy', 'napari/napari', 'dask/dask', 'ManimCommunity/manim', 'ray-project/ray'] for number in range(10)]
        counts = exporter.validate_draft_selection(drafts)
        self.assertEqual(dict(counts), {'sympy/sympy': 10, 'napari/napari': 10, 'dask/dask': 10, 'ManimCommunity/manim': 10, 'ray-project/ray': 10})
        with self.assertRaises(ValueError):
            exporter.validate_draft_selection(drafts[:-1])
        with self.assertRaises(ValueError):
            exporter.validate_draft_selection(drafts+[drafts[0]])

    def test_existing_human_review_form_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'review_form.csv'
            original = 'reviewer_id,review_notes\nreviewer-a,retain this feedback\n'
            path.write_text(original, encoding='utf-8')
            exporter.write_review_form(path, [])
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_evidence_rejects_missing_anchor_and_source_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'source'
            source.mkdir()
            (source/'module.py').write_text('def example():\n    return 1\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                exporter.code_evidence(source, 'example/repo', 'a'*40, ['S1', 'module.py', 'not_present'])
            with self.assertRaises(ValueError):
                exporter.code_evidence(source, 'example/repo', 'a'*40, ['S1', '../outside.py', 'anything'])


if __name__ == '__main__':
    unittest.main()
