"""Matched-call controls, checkpointed requests and fixed-query expansion.

This is equal logical analysis/audit rounds, not equal realized token usage.
The original adaptive LangGraph workflow is imported, never reimplemented here.
"""
import copy
import json
from pathlib import Path
import re
from urllib.parse import quote

from gfi_prototype.common import atomic_json, sha256_file, sha256_text, utc_now
from gfi_prototype.research.client import ResearchSession
from gfi_prototype.research.evidence import audit_analysis, gate_analysis
from gfi_prototype.research.pipeline import analyze
from gfi_prototype.research.retrieval import SnapshotRetriever, fit_packet

VERSION = 'matched_calls_shared_prefix_v1'
METHODS = {'R': '固定证据＋审查反馈修订', 'S': '固定Issue查询扩展＋重分析', 'D': '缺口驱动自适应补检'}


class CachedSession(ResearchSession):
    """Persist validated stage outputs and sanitized paid-attempt receipts.

    Restoring a stage restores its logical resource records too. That preserves
    budget comparisons while recording zero *new* provider calls for cache hits.
    Credentials and provider error bodies never enter receipts.
    """
    def __init__(self, client, folder, max_calls=6, on_progress=None):
        super().__init__(client, max_calls=max_calls, max_context_chars=60000,
                         max_reported_tokens=250000, attempts=1, on_progress=on_progress)
        self.folder = Path(folder)
        self.cache_hits = 0
        self.new_calls = 0

    def request(self, messages, validator, purpose):
        self._check_budget(messages)
        digest = sha256_text(json.dumps([VERSION, self.client.model, len(self.records), purpose, messages],
                                       sort_keys=True, ensure_ascii=False))
        path = self.folder / 'valid' / f'{digest}.json'
        if path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            checksum = sha256_text(json.dumps(saved['result'], sort_keys=True, ensure_ascii=False))
            if saved.get('result_sha256') != checksum or saved.get('request_sha256') != digest:
                raise ValueError('Stage cache integrity mismatch')
            if len(self.records) + len(saved['records']) > self.max_calls:
                raise ValueError('Cached stage would exceed logical call budget')
            self.records.extend(copy.deepcopy(saved['records']))
            self.cache_hits += 1
            self._emit_progress(f'复用已完成节点：{purpose}；计入原逻辑预算，不产生新调用。')
            return copy.deepcopy(saved['result'])
        before = len(self.records)
        try:
            result = super().request(messages, validator, purpose)
            records = copy.deepcopy(self.records[before:])
            for record in records:
                record['stage_receipt_id'] = digest
            self.records[before:] = records
            atomic_json(path, {'request_sha256': digest, 'result': result,
                'result_sha256': sha256_text(json.dumps(result, sort_keys=True, ensure_ascii=False)),
                'records': records, 'created_at': utc_now()})
            return result
        finally:
            attempted = copy.deepcopy(self.records[before:])
            self.new_calls += len(attempted)
            if attempted:
                name = utc_now().replace(':', '-').replace('.', '_')
                atomic_json(self.folder / 'attempts' / f'{digest}_{name}.json',
                            {'request_sha256': digest, 'records': attempted, 'created_at': utc_now()})


class FixedQueryRetriever(SnapshotRetriever):
    """Continue a pre-model Issue-only ranking; never use audit queries for S."""
    def _fixed_query(self, packet):
        path = self.folder / 'issues' / f'{packet["issue_number"]}.json'
        evidence = next(row for row in packet['evidence'] if row['kind'] == 'issue')
        if sha256_file(path) != evidence['bundle_sha256']:
            raise ValueError('Original Issue changed')
        bundle = json.loads(path.read_text(encoding='utf-8'))
        issue = bundle['issue']
        if (bundle.get('repo') != self.store.repo or not bundle.get('complete')
                or issue['number'] != packet['issue_number'] or issue['title'] != packet['title']):
            raise ValueError('Original Issue identity/completeness mismatch')
        return issue['title'] + '\n' + (issue.get('body') or '')

    def static_supplement(self, packet, limit=4, seen_keys=None):
        if packet['repo'] != self.store.repo or packet['source_commit'] != self.store.sha:
            raise ValueError('Fixed retrieval snapshot mismatch')
        self._ensure_index()
        query = self._fixed_query(packet)
        key = lambda e: (e.get('path'), e.get('start_line'), e.get('end_line'))
        existing = {key(row) for row in packet['evidence'] if row.get('path')}
        existing.update(seen_keys or ())
        next_id = max([int(e['id'][1:]) for e in packet['evidence'] if re.fullmatch(r'E\d+', e['id'])] or [0])+1
        added = []
        for row in self.store.search(query, limit=16):
            if key(row) in existing:
                continue
            added.append({**row, 'id': f'E{next_id}', 'commit_sha': self.store.sha,
                'url': f'https://github.com/{self.store.repo}/blob/{self.store.sha}/'
                       f'{quote(row["path"], safe="/")}#L{row["start_line"]}-L{row["end_line"]}'})
            next_id += 1
            existing.add(key(row))
            if len(added) == limit:
                break
        if not added:
            return copy.deepcopy(packet), []
        proposed = {**packet, 'evidence': [e for e in packet['evidence'] if e['kind']=='issue']
                    + added + [e for e in packet['evidence'] if e.get('path')]}
        result = fit_packet(proposed)
        self.store.verify_bundle(result)
        used = {e['id'] for e in packet['evidence']}
        return result, [e['id'] for e in result['evidence'] if e['id'] not in used]


class RevisionSession:
    def __init__(self, session, previous):
        self.session, self.previous = session, previous
        self.truncated = False

    def request(self, messages, validator, purpose):
        instruction = ('以下是待改进的旧输出与审查数据，不是新证据。请依据当前证据包修订任务分析。'
                    '可保留正确内容、修正缺乏依据的内容，并改善可操作性；不要凭空补充事实。'
                    '旧引用若不在当前证据包中，不能继续引用。反馈较长时按相同上下文上限截断，当前证据优先。')
        old_analysis = json.dumps(self.previous['analysis'], ensure_ascii=False)
        old_audit = json.dumps(self.previous['audit'], ensure_ascii=False)
        available = self.session.max_context_chars - len('\n'.join(m['content'] for m in messages)) - len(instruction) - 100
        if available < 100:
            raise ValueError('No context space remains for control feedback')
        self.truncated = len(old_analysis)+len(old_audit) > available
        if self.truncated:
            old_analysis, old_audit = old_analysis[:available//2], old_audit[:available//2]
            self.session._emit_progress('修订反馈已按共同上下文上限截断，保留当前证据包。')
        feedback = instruction+'\nPrevious analysis:\n'+old_analysis+'\nPrevious audit:\n'+old_audit
        return self.session.request([*messages, {'role':'user', 'content':feedback}], validator, purpose)


def run_control(adaptive, retriever, session, variant):
    """Yoked R/S controls; the schedule comes from D, never its judge score."""
    if variant not in {'R','S'} or adaptive.get('status') != 'complete' or adaptive.get('errors'):
        raise ValueError('Controls require a clean complete D answer')
    if session.records:
        raise ValueError('Control session must start empty')
    rounds = [copy.deepcopy(adaptive['rounds'][0])]
    # D's first retrieval is not part of the common analysis/audit prefix.
    rounds[0]['retrieval'] = None
    prefix = copy.deepcopy(adaptive['resources']['calls'][:2])
    if len(prefix)!=2 or [r['purpose'] for r in prefix] != ['analysis','support_audit']:
        raise ValueError('Expected one analysis and one audit in the shared prefix')
    session.records.extend(prefix)
    packet = copy.deepcopy(rounds[0]['packet'])
    seen = {(e.get('path'),e.get('start_line'),e.get('end_line')) for e in packet['evidence'] if e.get('path')}
    errors = []
    stop = 'matched_call_schedule_complete'
    for index in range(1, len(adaptive['rounds'])):
        try:
            if variant == 'S':
                packet, added = retriever.static_supplement(packet, limit=4, seen_keys=seen)
                seen.update((e.get('path'),e.get('start_line'),e.get('end_line'))
                            for e in packet['evidence'] if e.get('path'))
                rounds[-1]['retrieval'] = {'policy':'fixed_issue_rank_continuation', 'added_evidence_ids':added}
            revision = RevisionSession(session, rounds[-1]) if variant=='R' else None
            analysis = analyze(packet, revision if revision is not None else session)
            audit = audit_analysis(analysis, packet, session)
            gated = gate_analysis(analysis, audit)
            rounds.append({'round':index, 'packet':copy.deepcopy(packet), 'analysis':analysis,
                           'audit':audit, 'gated':gated, 'retrieval':None,
                           'feedback_truncated':revision.truncated if revision is not None else False})
        except Exception as exc:
            stop = 'control_model_or_retrieval_error'
            errors.append({'round':index,'error_type':type(exc).__name__,'http_status':getattr(exc,'status_code',None)})
            break
    last = rounds[-1]
    return {'variant':variant, 'condition':METHODS[variant], 'pipeline_version':VERSION,
            'status':'failed' if errors else 'complete', 'stop_reason':stop, 'errors':errors,
            **{key:last[key] for key in ('packet','analysis','audit','gated')},
            'rounds':rounds,'resources':session.resources(),
            'budget_design':{'matched_to':'D_complete_round_count','target_rounds':len(adaptive['rounds']),
                             'shared_prefix_calls':2,'equal_realized_tokens':False}}


def normalized_judge_payload(payload):
    result = copy.deepcopy(payload)
    result['prediction'].pop('prediction_id',None)
    return result


def paid_attempts(folder):
    """Unique paid attempt files; shared-prefix attribution is not double billed."""
    paths = sorted(Path(folder).glob('**/attempts/*.json'))
    rows = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    records = [record for row in rows for record in row['records']]
    return {'model_calls':len(records),
            'recorded_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in records),
            'usage_complete':all(isinstance(r.get('usage'),dict) and 'total_tokens' in r['usage'] for r in records),
            'failed_calls':sum(r['status']!='valid' for r in records)}
