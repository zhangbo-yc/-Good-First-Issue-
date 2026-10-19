"""Pinned evidence retrieval for controlled experiments; excludes discussion leaks."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import quote

from gfi_prototype.common import sha256_file
from gfi_prototype.retrieval import EvidenceStore


def fit_packet(packet, max_chars=42000):
    result = {key: copy.deepcopy(value) for key, value in packet.items() if key != 'evidence'}
    issue = [item for item in packet['evidence'] if item.get('kind') == 'issue']
    sources = [item for item in packet['evidence'] if item.get('path')]
    tests = [item for item in sources if item.get('kind') == 'test'][:1]
    ordered = issue + tests + [item for item in sources if item not in tests]
    result['evidence'] = []
    for item in ordered:
        candidate = {**result, 'evidence': result['evidence'] + [item]}
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_chars:
            result['evidence'].append(copy.deepcopy(item))
        elif item.get('kind') == 'issue':
            raise ValueError('Issue evidence exceeds the configured context budget')
    result['source_evidence_found'] = sum(bool(item.get('path')) for item in result['evidence'])
    return result


class SnapshotRetriever:
    def __init__(self, root, repo, max_evidence_chars=42000):
        self.folder = Path(root) / 'data' / repo.replace('/', '__')
        self.store = EvidenceStore(self.folder)
        self.max_evidence_chars = max_evidence_chars
        self._indexed = False

    def _ensure_index(self):
        if not self._indexed:
            self.store.build_index()
            self._indexed = True
        if sha256_file(self.store.archive) != self.store.snapshot['archive_sha256']:
            raise ValueError('Source snapshot changed')

    def initial(self, issue_number, issue_only=False):
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError('Invalid issue number')
        path = self.folder / 'issues' / f'{issue_number}.json'
        bundle = json.loads(path.read_text(encoding='utf-8'))
        issue = bundle['issue']
        if bundle.get('repo') != self.store.repo or issue['number'] != issue_number or not bundle.get('complete'):
            raise ValueError('Incomplete or mismatched raw issue')
        packet = {'repo': self.store.repo, 'issue_number': issue_number, 'title': issue['title'],
                  'issue_url': issue['html_url'], 'source_commit': self.store.sha,
                  'source_archive_sha256': self.store.snapshot['archive_sha256'],
                  'demo_assumption': True, 'effective_state': 'open',
                  'scope': 'current_pinned_snapshot_task_understanding_not_historical_repair',
                  'comment_policy': 'excluded_in_all_experimental_conditions',
                  'evidence': [{'id': 'I1', 'kind': 'issue', 'text': (issue.get('body') or '')[:6500],
                      'url': issue['html_url'], 'bundle_sha256': sha256_file(path)}], 'source_evidence_found': 0}
        if not issue_only:
            self._ensure_index()
            found = self.store.evidence_bundle(issue_number, max_sources=8)
            packet['evidence'] += [item for item in found['evidence'] if item.get('path')]
        return fit_packet(packet, self.max_evidence_chars)

    def supplement(self, packet, queries, limit=4):
        if packet['repo'] != self.store.repo or packet['source_commit'] != self.store.sha:
            raise ValueError('Research packet identity mismatch')
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('Supplement limit must be 1..8')
        self._ensure_index()
        key = lambda item: (item.get('path'), item.get('start_line'), item.get('end_line'))
        existing = {key(item) for item in packet['evidence'] if item.get('path')}
        used_ids = {item['id'] for item in packet['evidence']}
        next_id = max([int(value[1:]) for value in used_ids if re.fullmatch(r'E\d+', value)] or [0]) + 1
        added = []
        for query in queries[:4]:
            if not isinstance(query, str):
                raise ValueError('Retrieval queries must be text')
            for row in self.store.search(query[:600], limit=8):
                if key(row) in existing:
                    continue
                ref = {**row, 'id': f'E{next_id}', 'commit_sha': self.store.sha,
                       'url': f'https://github.com/{self.store.repo}/blob/{self.store.sha}/'
                              f'{quote(row["path"], safe="/")}#L{row["start_line"]}-L{row["end_line"]}'}
                next_id += 1
                existing.add(key(row))
                added.append(ref)
                if len(added) >= limit:
                    break
            if len(added) >= limit:
                break
        if not added:
            return copy.deepcopy(packet), []
        proposed = {**packet, 'evidence': [item for item in packet['evidence'] if item.get('kind') == 'issue']
                    + added + [item for item in packet['evidence'] if item.get('path')]}
        result = fit_packet(proposed, self.max_evidence_chars)
        actual = [item['id'] for item in result['evidence'] if item['id'] not in used_ids]
        if not actual:
            return copy.deepcopy(packet), []
        self.store.verify_bundle(result)
        return result, actual
