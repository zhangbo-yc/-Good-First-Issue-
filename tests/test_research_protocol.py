import json
from pathlib import Path

import unittest
import tempfile

import importlib.util
from types import SimpleNamespace
mod = __import__('gfi_prototype.research.protocol', fromlist=['protocol']) if importlib.util.find_spec('gfi_prototype.research.protocol') else SimpleNamespace()


REPOS = ['ManimCommunity/manim', 'sympy/sympy', 'dask/dask', 'ray-project/ray', 'napari/napari']


def corpus(tmp_path):
    (tmp_path / 'technology_rules.json').write_text('[]')
    for repo in REPOS:
        base = tmp_path / 'data' / repo.replace('/', '__')
        (base / 'processed').mkdir(parents=True)
        (base / 'annotations').mkdir()
        (base / 'issues').mkdir()
        (base / 'source').mkdir()
        (base / 'source' / 'a.py').write_text('pass')
        (base / 'source.zip').write_bytes(b'archive')
        import hashlib
        (base / 'source_snapshot.json').write_text(json.dumps({'repo': repo, 'commit_sha': 'a'*40,
            'archive': 'source.zip', 'archive_sha256': hashlib.sha256(b'archive').hexdigest()}))
        (base / 'analysis_cache.json').write_text(json.dumps({f'{repo}#{n}': {} for n in range(1, 11)}))
        (base / 'annotations' / 'task_analysis_drafts.jsonl').write_text(json.dumps({'repo': repo, 'issue_number': 11})+'\n')
        rows = []
        for n in range(1, 41):
            row = {'repo': repo, 'number': n, 'title': f'Task {n}', 'record_type': 'task',
                   'task_types': ['bugfix' if n % 2 else 'documentation'], 'source_bundle': f'issues/{n}.json'}
            rows.append(json.dumps(row))
            (base / 'issues' / f'{n}.json').write_text(json.dumps({'repo': repo, 'issue': {'number': n, 'title': row['title'], 'body': 'Raw issue'}}))
        (base / 'processed' / 'tasks.jsonl').write_text('\n'.join(rows))
    return tmp_path


def test_freeze_unseen_stratified_and_idempotent(corpus):
    protocol = mod.freeze_protocol(corpus, corpus / 'experiment')
    assert len(protocol['tasks']) == 45
    assert len(protocol['candidate_pool']) == 50
    assert all(t['issue_number'] > 11 for t in protocol['tasks'])
    assert all({'bugfix', 'documentation'} == {t['stratum'] for t in protocol['tasks'] if t['repo'] == repo} for repo in REPOS)
    assert mod.freeze_protocol(corpus, corpus / 'experiment') == protocol
    assert mod.verify_protocol(corpus, protocol) is None
    gold = json.loads((corpus / 'experiment' / 'gold_template.json').read_text())
    assert all(t['expected_technology_ids'] is None and t['requirements'] is None for t in gold)


def test_snapshot_mutation_refuses_verify_and_refreeze(corpus, target):
    protocol = mod.freeze_protocol(corpus, corpus / 'experiment')
    path = corpus / 'data' / 'ManimCommunity__manim' / target
    path.write_bytes(path.read_bytes() + b' ')
    with unittest.TestCase().assertRaises(ValueError):
        mod.verify_protocol(corpus, protocol)
    with unittest.TestCase().assertRaises(ValueError):
        mod.freeze_protocol(corpus, corpus / 'experiment')


def test_protocol_tampering_rejected(corpus):
    protocol = mod.freeze_protocol(corpus, corpus / 'experiment')
    protocol['tasks'][0]['title'] = 'altered'
    with unittest.TestCase().assertRaises(ValueError):
        mod.verify_protocol(corpus, protocol)


def test_historical_concrete_tasks_remain_eligible(corpus):
    for repo in REPOS:
        base = corpus / 'data' / repo.replace('/', '__') / 'processed'
        (base / 'historical_tasks.jsonl').write_bytes((base / 'tasks.jsonl').read_bytes())
        (base / 'tasks.jsonl').write_text('')
    assert len(mod.freeze_protocol(corpus, corpus / 'experiment')['tasks']) == 45


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = corpus(Path(self.temp.name))

    def test_freeze(self):
        test_freeze_unseen_stratified_and_idempotent(self.root)

    def test_mutated_bundle(self):
        test_snapshot_mutation_refuses_verify_and_refreeze(self.root, 'issues/12.json')

    def test_mutated_snapshot(self):
        test_snapshot_mutation_refuses_verify_and_refreeze(self.root, 'source_snapshot.json')

    def test_mutated_archive(self):
        test_snapshot_mutation_refuses_verify_and_refreeze(self.root, 'source.zip')

    def test_tampering(self):
        test_protocol_tampering_rejected(self.root)

    def test_historical(self):
        test_historical_concrete_tasks_remain_eligible(self.root)
