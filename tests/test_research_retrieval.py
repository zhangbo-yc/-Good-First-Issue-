import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from gfi_prototype.common import sha256_file
from gfi_prototype.research.retrieval import SnapshotRetriever


class ResearchRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / 'data/a__b'
        folder.mkdir(parents=True)
        (folder / 'issues').mkdir()
        archive = folder / ('source-' + 'a' * 40 + '.zip')
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('repo/pkg/widget.py', 'class Widget:\n    pass\n')
            stream.writestr('repo/tests/test_widget.py', 'def test_Widget():\n    assert True\n')
            stream.writestr('repo/pkg/decoder.py', 'def decode_packet(value):\n    return value\n')
        (folder / 'source_snapshot.json').write_text(json.dumps({'repo': 'a/b', 'commit_sha': 'a' * 40,
            'archive': archive.name, 'archive_sha256': sha256_file(archive)}))
        (folder / 'issues/1.json').write_text(json.dumps({'repo': 'a/b', 'complete': True,
            'fetched_at': '2026-10-16', 'issue': {'number': 1, 'title': 'Fix Widget', 'body': 'Widget fails',
            'html_url': 'https://github.com/a/b/issues/1', 'state': 'closed'},
            'comments': [{'body': 'Resolved using the final patch', 'html_url': 'https://github.com/a/b/issues/1#comment'}]}))
        self.retriever = SnapshotRetriever(self.root, 'a/b')

    def test_issue_only_contains_no_repository_or_resolution_comment_evidence(self):
        packet = self.retriever.initial(1, issue_only=True)
        self.assertEqual([e['id'] for e in packet['evidence']], ['I1'])
        self.assertEqual(packet['source_evidence_found'], 0)
        self.assertNotIn('state_at_snapshot', packet)
        self.assertNotIn('Resolved', json.dumps(packet))

    def test_supplement_adds_verified_distinct_source_and_no_duplicate_ids(self):
        packet = self.retriever.initial(1)
        self.assertTrue(any(ref.get('path') == 'pkg/widget.py' for ref in packet['evidence']))
        enriched, added = self.retriever.supplement(packet, ['decode_packet'])
        self.assertTrue(any(ref.get('path') == 'pkg/decoder.py' for ref in enriched['evidence']))
        self.assertTrue(added)
        self.assertEqual(len({e['id'] for e in enriched['evidence']}), len(enriched['evidence']))
        again, more = self.retriever.supplement(enriched, ['decode_packet'])
        self.assertEqual(more, [])
        self.assertEqual(again, enriched)

    def test_archive_mutation_is_rejected_before_evidence_is_used(self):
        self.retriever.initial(1)
        self.retriever.store.archive.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.retriever.supplement(self.retriever.initial(1, issue_only=True), ['decode_packet'])


if __name__ == '__main__':
    unittest.main()
