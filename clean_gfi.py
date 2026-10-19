#!/usr/bin/env python3
"""Step-one offline triage only: preserve raw GFI data and emit reviewable records."""
import argparse
import collections
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from gfi_prototype.common import atomic_json, sha256_file, utc_now


SCHEMA_VERSION = 3
TYPE_LABELS = {'task': '具体任务', 'umbrella': '汇总/跟踪帖', 'guide': '入门指引'}
AVAILABILITY_LABELS = {'needs_confirmation': '待确认', 'possibly_claimed': '可能已被认领',
                       'possibly_resolved': '疑似已解决', 'not_applicable': '不适用（非具体任务）',
                       'closed_in_snapshot': '快照中已关闭（历史样本）'}
PR_URL = re.compile(r'https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)\b')
ACTIVE = re.compile(r"\b(?:I am|I'm|I’m|I have started|I've started|I’ve started)\s+(?:currently\s+)?(?:working on|work on|implementing|fixing)\s+(?:this\b|it\b|the (?:issue|fix|class|implementation)\b)", re.I)
INTEREST = re.compile(r"\b(?:I would like to|I'd like to|I’d like to|I want to|I will|I can)\s+(?:work on|take(?: up)?|pick up|tackle|contribute to)\s+(?:this|it|the issue)\b", re.I)
WITHDRAWAL = re.compile(r"\bI(?: am|'m|’m)\s+(?:not|no longer)\s+working on\s+(?:this|it|the issue)|\bI\s+(?:will|shall)\s+work on another", re.I)
RESOLUTION_MENTION = re.compile(r'\bthis (?:issue )?can be closed(?: now)?\b|\b(?:already|has been|is now) (?:fixed|resolved)\b|\bno longer (?:an issue|reproducible)\b|\b(?:this|the) (?:issue|bug|problem)\b.{0,35}\b(?:fixed|resolved|closed)\b', re.I)
RESOLUTION = re.compile(r'\b(?:this(?: issue| bug| problem)?|the (?:issue|bug|problem))\s+(?:can be closed(?: now)?|(?:has been|is(?: now)?|was)\s+(?:fixed|resolved)|is no longer (?:an issue|reproducible))\b', re.I)
RESOLUTION_QUALIFIER = re.compile(r'\b(?:if|once|when|after|unless|until|provided|partial(?:ly)?|not|isn.t|wasn.t)\b|\?', re.I)
REMAINING_WORK = re.compile(r'\bremaining (?:work|issues?|problems?|tasks?|points?)\b|\b(?:work|issues?|problems?|points?|bugs?) (?:still )?remain\b|\bpoints that remain\b|\bstill (?:fails?|broken|not fixed|needs?|an issue)\b|\bwhat should be done\b|\bneeds? to be (?:made|fixed|done|changed)\b|\bpartial fix\b|\bnot (?:fully )?(?:fixed|resolved)\b', re.I)


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def clean_markdown(text):
    """Remove HTML template comments outside fenced code; retain code verbatim."""
    pieces, pending, fence = [], [], None
    def flush():
        if pending:
            pieces.append(re.sub(r'<!--.*?-->', '', ''.join(pending), flags=re.S))
            pending.clear()
    for line in (text or '').replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True):
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            pieces.append(line)
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = None
        elif match:
            flush()
            fence = match[1]
            pieces.append(line)
        else:
            pending.append(line)
    flush()
    return ''.join(pieces).strip()


def own_text(text):
    """Do not interpret quotes, code or forwarded email as the author's claim."""
    lines, fence = [], None
    for line in clean_markdown(text).splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            if fence and match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = None
            elif not fence:
                fence = match[1]
            continue
        if fence or line.lstrip().startswith('>'):
            continue
        if re.match(r'^On .+wrote:\s*$', line):
            break
        lines.append(line)
    return '\n'.join(lines)


def snippet(text, match):
    return re.sub(r'\s+', ' ', text[max(0, match.start()-55):match.end()+180]).strip()


def resolution_match(text):
    """Require a whole-issue assertion without questions or conditions on its line."""
    for match in RESOLUTION.finditer(text):
        start = text.rfind('\n', 0, match.start()) + 1
        end = text.find('\n', match.end())
        line = text[start:end if end >= 0 else len(text)]
        if not RESOLUTION_QUALIFIER.search(line):
            return match
    return None


def task_types(issue, body):
    title = issue['title']
    labels = [label['name'] for label in issue.get('labels', [])]
    scope = title + '\n' + '\n'.join(labels)
    patterns = {
        'documentation': r'documentation|docstrings?|\bwiki\b|sphinx|cheatsheet',
        'testing': r'\btests?\b|testing|doctest|xfail|\bci\b',
        'bugfix': r'\bbug\b|\bfails?\b|incorrect|weird|\berrors?\b|\bwarning\b|\bbroken\b',
        'feature': r'\bfeature\b|\benhancement\b|\badd\b|\bsupport\b|\benable\b|\bexpose\b',
        'refactor': r'code quality|type:refactor|clean up|reorganize|\bmerge\b|\bremove\b|\bmoved?\b',
        'performance': r'performance|\bslow\b',
        'internationalization': r'\b(?:internationali[sz]ation|locali[sz]ation|i18n|l10n)\b|_\.trans|\bmissing translations\b',
        'maintenance': r'\bmaintenance\b|\bci\b|releasing',
    }
    found, evidence = [], []
    for kind, pattern in patterns.items():
        match = re.search(pattern, scope, re.I)
        if match:
            found.append(kind)
            evidence.append({'task_type': kind, 'rule': pattern, 'matched_text': match[0], 'source': 'title_or_original_labels'})
    return found or ['needs_review'], evidence


def triage(bundle, override=None):
    issue, comments = bundle['issue'], bundle.get('comments', [])
    body = clean_markdown(issue.get('body') or '')
    reference_time = parse_time(bundle['fetched_at'])
    record_type, classification_evidence = 'task', []
    flags = {'snapshot_status_not_refreshed'}
    if override:
        if override['match_text'] not in (issue.get('body') or '') + '\n' + issue['title']:
            raise ValueError(f"Structural override no longer matches {bundle['repo']}#{issue['number']}")
        record_type = override['record_type']
        if record_type not in TYPE_LABELS:
            raise ValueError('Unsupported record type in override')
        flags.update(override.get('flags', []))
        classification_evidence.append({'url': issue['html_url'], 'excerpt': override['match_text'], 'reason': override['reason']})
        if override.get('comment_match_text'):
            matching_comments = [comment for comment in comments
                                 if override['comment_match_text'] in (comment.get('body') or '')]
            if not matching_comments:
                raise ValueError(f"Comment evidence for structural override no longer matches {bundle['repo']}#{issue['number']}")
            classification_evidence.append({'url': matching_comments[-1]['html_url'],
                                            'excerpt': override['comment_match_text'], 'reason': override['reason']})
    labels = [label['name'] for label in issue.get('labels', [])]
    signals, pr_refs = [], {}
    sources = [{'body': issue.get('body') or '', 'html_url': issue['html_url'],
                'created_at': issue.get('updated_at', bundle['fetched_at']), 'user': issue.get('user', {}),
                'author_association': issue.get('author_association', 'NONE'), 'source_kind': 'issue_body'}]
    sources.extend(dict(c, source_kind='comment') for c in comments)
    for source in sources:
        original, url = source.get('body') or '', source['html_url']
        text = own_text(original)
        timestamp = source.get('created_at') or bundle['fetched_at']
        age = max(0, (reference_time - parse_time(timestamp)).days)
        matches = []
        match = WITHDRAWAL.search(text)
        signal_type = 'withdrawal' if match else None
        if not match:
            for kind, pattern in [('work_in_progress', ACTIVE), ('interest', INTEREST)]:
                match = pattern.search(text)
                if match:
                    signal_type = kind
                    break
        if match:
            matches.append((signal_type, match))
        positive = resolution_match(text)
        mention = RESOLUTION_MENTION.search(text)
        if positive:
            matches.append(('possible_resolution', positive))
        elif mention:
            matches.append(('resolution_context_needs_review', mention))
        remaining = REMAINING_WORK.search(text)
        if remaining:
            matches.append(('remaining_work', remaining))
        for kind, match in matches:
            signals.append({'type': kind, 'url': url, 'excerpt': snippet(text, match),
                            'source_kind': source['source_kind'], 'author': (source.get('user') or {}).get('login'),
                            'author_association': source.get('author_association'), 'created_at': timestamp,
                            'timestamp_basis': 'issue_update_not_statement_date' if source['source_kind'] == 'issue_body' else 'comment_creation',
                            'age_at_snapshot_days': age, 'provisional': True})
        for match in PR_URL.finditer(original):
            pr_url = match[0]
            ref = pr_refs.setdefault(pr_url, {'url': pr_url, 'repo': match[1] + '/' + match[2],
                                            'number': int(match[3]), 'relation': 'unverified_reference',
                                            'verified': False, 'state': 'unknown', 'source_urls': []})
            if url not in ref['source_urls']:
                ref['source_urls'].append(url)
    # Existing API cross-reference summaries are still not proof of resolution.
    for pr in bundle.get('related_pull_requests', []):
        url = pr.get('html_url', '')
        match = PR_URL.fullmatch(url)
        if match:
            pr_refs.setdefault(url, {'url': url, 'repo': match[1] + '/' + match[2], 'number': int(match[3]),
                                    'relation': 'api_cross_reference_not_verified_as_solution', 'verified': False,
                                    'state': pr.get('state', 'unknown'), 'source_urls': [issue['html_url']]})
    if pr_refs:
        flags.add('pr_references_need_verification')
    assignees = [user['login'] for user in issue.get('assignees', [])]
    if assignees:
        signals.append({'type': 'assigned_in_snapshot', 'url': issue['html_url'],
                        'assignees': assignees, 'provisional': True})
    latest_by_author = {}
    for signal in sorted((s for s in signals if 'created_at' in s), key=lambda s: parse_time(s['created_at'])):
        if signal['type'] in ('work_in_progress', 'interest', 'withdrawal'):
            latest_by_author[signal.get('author')] = signal
    active = [s for s in latest_by_author.values() if s['type'] == 'work_in_progress' and s['age_at_snapshot_days'] <= 90]
    interests = [s for s in signals if s['type'] in ('work_in_progress', 'interest')]
    if any(s['age_at_snapshot_days'] > 90 for s in interests):
        flags.add('historical_interest')
    if any(s['age_at_snapshot_days'] <= 90 and s['type'] == 'interest' for s in interests):
        flags.add('recent_interest_not_assignment')
    if active:
        flags.add('recent_work_in_progress')
    availability = 'possibly_claimed' if active or assignees else 'needs_confirmation'
    if any('probably solved' in label.lower() for label in labels):
        flags.add('possible_resolution')
        signals.append({'type': 'possible_resolution_label', 'url': issue['html_url'],
                        'labels': [label for label in labels if 'probably solved' in label.lower()], 'provisional': True})
    resolutions = [s for s in signals if s['type'] == 'possible_resolution']
    for resolution in resolutions:
        later_remaining = [s['url'] for s in signals if s['type'] == 'remaining_work'
                           and parse_time(s['created_at']) >= parse_time(resolution['created_at'])]
        resolution['superseded_by'] = sorted(set(later_remaining))
        if later_remaining:
            flags.add('resolution_conflict')
        if resolution['age_at_snapshot_days'] > 90:
            flags.add('historical_resolution')
    if any(s['type'] == 'resolution_context_needs_review' for s in signals):
        flags.add('resolution_context_needs_review')
    if any(s['age_at_snapshot_days'] <= 90 and not s['superseded_by']
           and s['source_kind'] == 'comment' for s in resolutions):
        flags.add('possible_resolution')
    if 'possible_resolution' in flags:
        availability = 'possibly_resolved'
    if any(label.lower() == 'could close' for label in labels):
        flags.add('maintainer_triage_close_candidate')
    if (reference_time - parse_time(issue['updated_at'])).days >= 365:
        flags.add('long_inactive')
    if record_type != 'task':
        availability = 'not_applicable'
    if issue['state'] != 'open':
        flags.add('not_open_in_snapshot')
        availability = 'closed_in_snapshot' if record_type == 'task' else 'not_applicable'
    # Describe observable information, without pretending to have verified acceptance criteria.
    substantive = re.sub(r'^Original (?:issue|author|owner).*$', '', own_text(body), flags=re.M).strip()
    information = 'needs_context' if len(substantive) < 100 else 'description_present_acceptance_unverified'
    if information == 'needs_context':
        flags.add('insufficient_standalone_description')
    types, type_evidence = task_types(issue, body)
    return {'schema_version': SCHEMA_VERSION, 'repo': bundle['repo'], 'number': issue['number'],
            'title': issue['title'], 'url': issue['html_url'], 'state_at_snapshot': issue['state'],
            'snapshot_at': bundle['fetched_at'], 'issue_updated_at': issue['updated_at'],
            'record_type': record_type, 'record_type_label': TYPE_LABELS[record_type],
            'record_type_method': 'evidence_linked_structural_override' if override else 'default_task_pending_review',
            'classification_evidence': classification_evidence, 'task_types': types, 'task_type_evidence': type_evidence,
            'original_labels': labels, 'assignees': assignees, 'availability': availability,
            'availability_label': AVAILABILITY_LABELS[availability], 'availability_signals': signals,
            'pr_references': list(pr_refs.values()), 'information_completeness': information,
            'information_indicators': {'body_characters': len(body), 'comment_count': len(comments),
                                       'has_fenced_code': bool(re.search(r'^\s*(`{3,}|~{3,})', body, re.M)),
                                       'has_external_links': bool(re.search(r'https?://', body)),
                                       'acceptance_criteria_reviewed': False},
            'body_clean': body, 'quality_flags': sorted(flags),
            'candidate_record': record_type == 'task' and issue['state'] == 'open',
            'historical_record': issue['state'] == 'closed',
            'recommendation_ready': False, 'annotation_status': 'preliminary_triage_not_ground_truth',
            'source_bundle': f"issues/{issue['number']}.json"}


def write_jsonl(path, records):
    temp = path.with_suffix('.jsonl.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    temp.replace(path)


def write_csv(path, records):
    fields = ['repo', 'number', 'title', 'state_at_snapshot', 'candidate_record', 'historical_record',
              'record_type_label', 'task_types', 'availability_label',
              'information_completeness', 'quality_flags', 'assignees', 'pr_reference_count',
              'recommendation_ready', 'url']
    temp = path.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key, '') for key in fields}
            row['pr_reference_count'] = len(record['pr_references'])
            for key, value in row.items():
                if isinstance(value, list):
                    value = '; '.join(value)
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                row[key] = value
            writer.writerow(row)
    temp.replace(path)


def run(data_root, overrides):
    root = Path(data_root)
    inputs = sorted(root.glob('*/issues.jsonl'))
    if not inputs:
        raise ValueError('No project issues.jsonl files found')
    all_records, project_stats, duplicate_count, used_overrides = [], [], 0, set()
    hashes = {str(path): sha256_file(path) for path in inputs}
    for path in inputs:
        seen, records = {}, []
        for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            bundle = json.loads(line)
            if not bundle.get('complete'):
                raise ValueError(f'Incomplete source bundle at {path}:{line_number}')
            if 'pull_request' in bundle['issue']:
                raise ValueError(f'Unexpected PR record at {path}:{line_number}')
            if bundle['repo'].replace('/', '__') != path.parent.name:
                raise ValueError(f'Repository identity mismatch at {path}:{line_number}')
            key = f"{bundle['repo']}#{bundle['issue']['number']}"
            if key in seen:
                if seen[key] != bundle:
                    raise ValueError(f'Conflicting duplicate {key}')
                duplicate_count += 1
                continue
            seen[key] = bundle
            if key in overrides:
                used_overrides.add(key)
            records.append(triage(bundle, overrides.get(key)))
        folder = path.parent / 'processed'
        folder.mkdir(exist_ok=True)
        write_jsonl(folder / 'triage.jsonl', records)
        write_jsonl(folder / 'tasks.jsonl', [r for r in records if r['candidate_record']])
        write_jsonl(folder / 'historical_tasks.jsonl', [r for r in records if r['record_type'] == 'task' and r['historical_record']])
        write_jsonl(folder / 'non_task_records.jsonl', [r for r in records if r['record_type'] != 'task'])
        write_csv(folder / 'triage.csv', records)
        stats = {'repo': records[0]['repo'] if records else path.parent.name, 'total_records': len(records),
                 'record_types': dict(collections.Counter(r['record_type'] for r in records)),
                 'state_counts': dict(collections.Counter(r['state_at_snapshot'] for r in records)),
                 'candidate_records': sum(r['candidate_record'] for r in records),
                 'historical_records': sum(r['historical_record'] for r in records),
                 'historical_task_records': sum(r['record_type'] == 'task' and r['historical_record'] for r in records),
                 'availability': dict(collections.Counter(r['availability'] for r in records)),
                 'source_sha256': hashes[str(path)]}
        atomic_json(folder / 'cleaning_manifest.json', stats)
        project_stats.append(stats)
        all_records.extend(records)
    for path in inputs:
        if sha256_file(path) != hashes[str(path)]:
            raise RuntimeError(f'Source changed during cleaning: {path}')
    summary = {'schema_version': SCHEMA_VERSION, 'generated_at': utc_now(), 'scope': 'offline_step_one_only',
               'total_records': len(all_records), 'duplicate_records': duplicate_count,
               'record_types': dict(collections.Counter(r['record_type'] for r in all_records)),
               'state_counts': dict(collections.Counter(r['state_at_snapshot'] for r in all_records)),
               'candidate_records': sum(r['candidate_record'] for r in all_records),
               'historical_records': sum(r['historical_record'] for r in all_records),
               'historical_task_records': sum(r['record_type'] == 'task' and r['historical_record'] for r in all_records),
               'availability': dict(collections.Counter(r['availability'] for r in all_records)),
               'quality_flags': dict(collections.Counter(flag for r in all_records for flag in r['quality_flags'])),
               'projects': project_stats, 'unused_override_keys': sorted(set(overrides) - used_overrides),
               'raw_files_preserved': True, 'ground_truth_annotation_created': False}
    atomic_json(root / 'cleaning_summary.json', summary)
    report = ['# 第①步：GFI 数据清洗报告', '',
              '本次仅对已采集快照离线清洗，没有访问 GitHub、使用 Token、建立用户画像、开展技能人工标注或调用模型。', '',
              '## 数量核对', '', '| 项目 | 原始唯一记录 | 开放具体任务候选 | 已关闭具体任务 | 汇总/跟踪帖 | 入门指引 |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for stats in project_stats:
        count = stats['record_types']
        report.append(f"| {stats['repo']} | {stats['total_records']} | {stats['candidate_records']} | {stats['historical_task_records']} | {count.get('umbrella', 0)} | {count.get('guide', 0)} |")
    report.extend(['', f"共保留 {len(all_records)} 条唯一记录，完全重复记录 {duplicate_count} 条。所有原始 JSONL 的 SHA256 在处理前后保持一致。", '',
                   '## 字段和判断边界', '',
                   '- 每条补充记录类型、任务类型、可用状态线索、信息完整度指标、原始标签、证据链接和质量标记。',
                   '- task_types 来自标题与原始标签的可解释规则，可能多选；imported Bug 等历史标签可能过时，均需复核。',
                   f'- {len(used_overrides)}条结构性特殊记录通过已读正文中的明确片段分类，规则保存在 triage_overrides.json；其余默认具体任务仍是初筛。',
                   '- 开放具体任务进入tasks.jsonl；已关闭具体任务进入historical_tasks.jsonl，仅作历史研究样本，不作为当前可接手候选。',
                   '- availability 是采集时快照线索，不是当前可接手承诺。全部 recommendation_ready=false，推荐前需要刷新状态。',
                   '- 90天仅用于区分近期与历史认领线索；超过365天未更新只标记长期未活跃，不删除也不自动判定无效。',
                   '- 疑似解决仅参考近期评论中无条件的整项任务解决陈述，或快照中的“probably solved”标签；仍须核验。',
                   '- 部分文件修复、提问和附带条件的关闭建议只保留为上下文。超过90天的解决建议只保留为历史线索；后续剩余工作陈述会标记与先前解决建议的冲突。',
                   '- 认领意愿不等于已指派；引用、代码块及转发邮件不视为本人认领。任务指派可能指向维护者，也需要复核。',
                   '- PR URL 是未核验的引用，不能据此断言PR仍开放、解决本任务或已经合并。原数据关联PR摘要为空不等于没有PR。',
                   '- 描述长度等仅是信息完整性线索，没有核验验收条件，也没有输出技能标准答案。',
                   '- JSONL保留原文中的代码；仅去除代码围栏外的HTML模板注释，原文仍可从源文件恢复。', '',
                   '## 非具体任务记录及证据', '', '| 记录 | 分类 | 原因 |', '| --- | --- | --- |'])
    for record in all_records:
        if record['record_type'] != 'task':
            reason = record['classification_evidence'][0]['reason']
            report.append(f"| [{record['repo']}#{record['number']}]({record['url']}) | {record['record_type_label']} | {reason} |")
    report.extend(['', '## 可用状态初筛', '', '| 状态 | 数量 |', '| --- | ---: |'])
    for status, count in summary['availability'].items():
        report.append(f'| {AVAILABILITY_LABELS[status]} | {count} |')
    report.extend(['', '## 质量标记（可重叠）', '', '| 标记 | 数量 |', '| --- | ---: |'])
    for flag, count in sorted(summary['quality_flags'].items()):
        report.append(f'| {flag} | {count} |')
    report.extend(['', '## 输出文件', '',
                   '- 每项目 processed/triage.jsonl 与 triage.csv：所有记录及初筛结果。',
                   '- 每项目 processed/tasks.jsonl：具体任务候选，尚非已验证可推荐任务。',
                   '- 每项目 processed/historical_tasks.jsonl：已关闭的具体任务历史样本。',
                   '- 每项目 processed/non_task_records.jsonl：汇总帖与指引，保留作知识来源。',
                   '- 每项目 processed/cleaning_manifest.json：数量与输入SHA256。',
                   '- data/cleaning_summary.json：全局汇总。', '',
                   '## 停止位置', '',
                   '本报告仅记录第①步清洗；第②步草稿另见ANNOTATION_REPORT.md。本脚本不会生成用户画像或启动LangChain分析。'])
    (root / 'CLEANING_REPORT.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=Path('data'))
    parser.add_argument('--overrides', type=Path, default=Path(__file__).with_name('triage_overrides.json'))
    args = parser.parse_args()
    overrides = json.loads(args.overrides.read_text(encoding='utf-8'))
    summary = run(args.data_root, overrides)
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
