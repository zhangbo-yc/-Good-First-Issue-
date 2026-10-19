import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from gfi_prototype.common import sha256_file
from gfi_prototype.retrieval import EvidenceStore
from gfi_prototype.research.frontend_retrieval import FrontendEvidenceStore, FrontendSnapshotRetriever
from gfi_prototype.research import frontend_retrieval as frontend


class FrontendRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.folder=self.root/'data/a__b'
        self.folder.mkdir(parents=True)
        sha='a'*40
        archive=self.folder/f'source-{sha}.zip'
        files={
            'pkg/core.py':'def LegacyAnchor():\n    return 1\n',
            'src/Refresh.tsx':'export function RefreshWidget() { return <span>lastUpdated</span>; }\n',
            'src/state.ts':'export const lastUpdated = Date.now();\n',
            'src/View.jsx':'export const View = () => <div>lastUpdated</div>;\n',
            'src/refresh.js':'export function refreshTimestamp() { return Date.now(); }\n',
            'src/types.d.ts':'export interface RefreshState { lastUpdated: number; }\n',
            'src/manual.js':'export const notice = "do not edit the current value";\n',
            'src/Refresh.test.tsx':'it("lastUpdated", () => expect(true).toBe(true));\n',
            'src/__tests__/state.spec.js':'test("lastUpdated", () => {});\n',
            'dist/app.js':'export const lastUpdated = 1;',
            'node_modules/pkg/index.ts':'export const lastUpdated = 1;',
            'vendor/third.js':'export const lastUpdated = 1;',
            'src/generated/types.ts':'export const lastUpdated = 1;',
            'public/app.abcdef1234.js':'export const lastUpdated = 1;',
            'src/tool.min.js':'export const lastUpdated = 1;',
            'src/schema.ts':'// AUTO-GENERATED. DO NOT EDIT.\nexport const lastUpdated = 1;',
            'src/long.js':'export const lastUpdated="'+'x'*4000+'";',
        }
        with zipfile.ZipFile(archive,'w') as z:
            for path,text in files.items():z.writestr('project/'+path,text)
        (self.folder/'source_snapshot.json').write_text(json.dumps({'repo':'a/b','commit_sha':sha,
            'archive':archive.name,'archive_sha256':sha256_file(archive)}),encoding='utf-8')
        (self.folder/'issues').mkdir()
        (self.folder/'issues/1.json').write_text(json.dumps({'repo':'a/b','complete':True,
            'issue':{'number':1,'title':'RefreshWidget lastUpdated','body':'refreshTimestamp',
                     'state':'open','html_url':'https://github.com/a/b/issues/1'},'fetched_at':'fixed'}),encoding='utf-8')

    def tearDown(self):self.tmp.cleanup()

    def rows(self,store):
        with closing(sqlite3.connect(store.index)) as db:
            return dict(db.execute('SELECT DISTINCT path,kind FROM chunks'))

    def test_new_profile_adds_frontend_source_and_tests_without_touching_legacy(self):
        old=EvidenceStore(self.folder); old.build_index()
        before=sha256_file(old.index)
        new=FrontendEvidenceStore(self.folder); meta=new.build_index()
        rows=self.rows(new)
        for path in ['src/Refresh.tsx','src/state.ts','src/View.jsx','src/refresh.js']:
            self.assertEqual(rows[path],'source')
        for path in ['src/Refresh.test.tsx','src/__tests__/state.spec.js']:
            self.assertEqual(rows[path],'test')
        self.assertNotEqual(new.index,old.index)
        self.assertEqual(sha256_file(old.index),before)
        self.assertEqual(set(self.rows(old)),{'pkg/core.py'})
        self.assertEqual(meta['profile'],'frontend_source_v1')

    def test_excludes_generated_bundled_minified_and_vendor_frontend_files(self):
        store=FrontendEvidenceStore(self.folder); store.build_index()
        rows=self.rows(store)
        for path in ['dist/app.js','node_modules/pkg/index.ts','vendor/third.js','src/generated/types.ts',
                     'public/app.abcdef1234.js','src/tool.min.js','src/schema.ts','src/long.js']:
            self.assertNotIn(path,rows,path)

    def test_new_index_reuses_itself_and_still_verifies_source_identity(self):
        store=FrontendEvidenceStore(self.folder)
        self.assertFalse(store.build_index()['reused'])
        self.assertTrue(store.build_index()['reused'])
        packet=store.evidence_bundle(1)
        frontend=[r for r in packet['evidence'] if r.get('path','').endswith('.tsx')]
        self.assertTrue(frontend)
        packet['evidence'][-1]['text']='tampered'
        with self.assertRaises(ValueError):store.verify_bundle(packet)

    def test_snapshot_adapter_is_explicit_and_excludes_comments(self):
        retriever=FrontendSnapshotRetriever(self.root,'a/b')
        packet=retriever.initial(1)
        self.assertEqual(packet['retrieval_profile'],'frontend_source_v1')
        self.assertFalse(any(r['kind']=='comment' for r in packet['evidence']))
        self.assertFalse((self.folder/'retrieval/source_index.sqlite3').exists())

    def test_handwritten_declarations_and_strings_are_not_treated_as_generated_headers(self):
        store=FrontendEvidenceStore(self.folder); store.build_index()
        rows=self.rows(store)
        self.assertIn('src/types.d.ts',rows)
        self.assertIn('src/manual.js',rows)

    def test_failed_rebuild_keeps_valid_new_and_legacy_indices_and_removes_temporary_file(self):
        old=EvidenceStore(self.folder); old.build_index()
        store=FrontendEvidenceStore(self.folder); store.build_index()
        hashes={p:sha256_file(p) for p in [old.index,store.index]}
        with patch.object(frontend,'MAX_LINE_CHARS',1999), \
                patch.object(frontend,'materialized_archive_members',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):store.build_index()
        for p,digest in hashes.items():self.assertEqual(sha256_file(p),digest)
        self.assertFalse(list(store.index.parent.glob('*.building')))

    def test_changed_archive_is_rejected_without_creating_new_index(self):
        store=FrontendEvidenceStore(self.folder)
        store.archive.write_bytes(b'changed archive')
        with self.assertRaises(ValueError):store.build_index()
        self.assertFalse(store.index.exists())

    def test_non_object_cache_metadata_is_rebuilt_instead_of_crashing(self):
        store=FrontendEvidenceStore(self.folder); store.build_index()
        for malformed in ['null','[]','42']:
            with self.subTest(metadata=malformed):
                with closing(sqlite3.connect(store.index)) as db:
                    db.execute('UPDATE metadata SET value=? WHERE key="index"',(malformed,))
                    db.commit()
                result=store.build_index()
                self.assertFalse(result['reused'])
                self.assertEqual(result['profile'],'frontend_source_v1')

if __name__=='__main__':unittest.main()
