#!/usr/bin/env python3
"""Materialize explicitly authored step-two drafts and validate cited local evidence."""
import argparse
import collections
import csv
import json
from pathlib import Path
from urllib.parse import quote

from gfi_prototype.common import atomic_json, sha256_file, utc_now

ANNOTATION_REPOSITORIES = ('sympy/sympy', 'napari/napari', 'dask/dask', 'ManimCommunity/manim', 'ray-project/ray')


def validate_draft_selection(drafts):
    identities = [(draft['repo'], draft['number']) for draft in drafts]
    counts = collections.Counter(repo for repo, number in identities)
    if len(identities) != len(set(identities)) or counts != {repo: 10 for repo in ANNOTATION_REPOSITORIES}:
        raise ValueError('Task drafts require exactly ten distinct samples from each of the five repositories')
    return counts


def code_evidence(source, repo, sha, spec):
    evidence_id, relative, anchor = spec
    path = (source / relative).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ValueError('Evidence path escapes source tree')
    lines = path.read_text(encoding='utf-8').splitlines()
    hits = [i for i, line in enumerate(lines) if anchor in line]
    if not hits:
        raise ValueError(f'Evidence anchor not found: {repo}:{relative}: {anchor}')
    index = hits[0]
    first, last = max(0, index - 3), min(len(lines), index + 13)
    return {'id': evidence_id, 'kind': 'test_reference' if evidence_id.startswith('T') else 'source_or_document',
            'repo': repo, 'commit_sha': sha, 'path': relative, 'start_line': first+1,
            'end_line': last, 'anchor': anchor, 'excerpt': '\n'.join(lines[first:last]),
            'file_sha256': sha256_file(path),
            'url': f'https://github.com/{repo}/blob/{sha}/{quote(relative, safe="/")}#L{first+1}-L{last}',
            'validation': 'local_file_anchor_and_lines_checked', 'semantics_human_reviewed': False}


def materialize(root, draft):
    repo, number = draft['repo'], draft['number']
    folder = root / 'data' / repo.replace('/', '__')
    path = folder / 'issues' / f'{number}.json'
    bundle = json.loads(path.read_text(encoding='utf-8'))
    issue = bundle['issue']
    if bundle['repo'] != repo or issue['number'] != number or not bundle['complete']:
        raise ValueError('Source bundle identity/completeness mismatch')
    snapshot = json.loads((folder / 'source_snapshot.json').read_text(encoding='utf-8'))
    if draft['issue_quote'] not in (issue.get('body') or ''):
        raise ValueError(f'Issue evidence not found: {repo}#{number}')
    evidence = [{'id': 'I1', 'kind': 'issue', 'url': issue['html_url'], 'excerpt': issue['body'], 'anchor': draft['issue_quote'],
                 'snapshot_at': bundle['fetched_at'], 'source_bundle_sha256': sha256_file(path)}]
    if draft.get('comment_quote'):
        candidates = [c for c in bundle['comments'] if draft['comment_quote'] in (c.get('body') or '')]
        if not candidates:
            raise ValueError(f'Comment evidence not found: {repo}#{number}')
        comment = candidates[-1]
        position = comment['body'].index(draft['comment_quote'])
        evidence.append({'id': 'C1', 'kind': 'comment', 'url': comment['html_url'],
                         'excerpt': comment['body'][max(0, position-80):position+600],
                         'author': comment['user']['login'], 'author_association': comment.get('author_association'),
                         'created_at': comment['created_at'], 'snapshot_at': bundle['fetched_at']})
    evidence.extend(code_evidence(folder/'source', repo, snapshot['commit_sha'], spec) for spec in draft['evidence'])
    ids = [item['id'] for item in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate evidence IDs')
    skills = []
    for name, reason, references in draft['skills']:
        if not references or not set(references).issubset(ids):
            raise ValueError(f'Skill evidence missing: {repo}#{number}: {name}')
        skills.append({'name': name, 'rationale': reason, 'evidence_ids': references,
                       'requirement_status': 'inferred_pending_independent_review'})
    cleaned = [json.loads(line) for line in (folder/'processed/tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    triage = next((record for record in cleaned if record['number'] == number), None)
    if not triage or not triage['candidate_record'] or issue['state'] != 'open':
        raise ValueError('Selected item is not an open step-one task candidate')
    return {'annotation_schema_version': 1, 'repo': repo, 'issue_number': number, 'title': issue['title'],
            'issue_url': issue['html_url'], 'source_commit': snapshot['commit_sha'],
            'annotation_origin': 'assistant_source_review', 'human_review_status': 'pending', 'is_ground_truth': False,
            'sample_role': 'purposive_development_pilot', 'issue_snapshot_at': bundle['fetched_at'],
            'issue_state_at_snapshot': issue['state'],
            'availability_from_step_one': triage['availability'], 'current_availability_verified': False,
            'goal': draft['goal'], 'required_skills_draft': skills, 'suitable_background_draft': draft['suitable_for'],
            'acceptance_draft': [{'criterion': criterion, 'status': 'proposed_pending_maintainer_or_reviewer_confirmation'}
                                 for criterion in draft['acceptance']],
            'blockers': draft['blockers'], 'unknowns': draft['unknowns'],
            'source_observation': draft['source_observation'], 'runtime_reproduction': 'not_run',
            'project_tests_executed': False, 'evidence': evidence,
            'related_test_references': [e['id'] for e in evidence if e['kind'] == 'test_reference'],
            'limitations': ['Static snapshot evidence; Issue and source are not a historical time-aligned replay.',
                            'No exact completion time or success probability estimated.',
                            'Independent human review required before use as evaluation ground truth.']}


def write_jsonl(path, records):
    temp = path.with_suffix('.jsonl.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
    temp.replace(path)


def write_review_form(path, records):
    if path.exists():
        # Human review is external work: never replace a form that may be filled in.
        return
    fields = ['repo', 'issue_number', 'issue_url', 'reviewer_id', 'skills_judgment', 'code_links_judgment',
              'goal_acceptance_judgment', 'blockers_unknowns_judgment', 'overall_decision', 'review_notes']
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key, '') for key in fields})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.root.resolve()
    drafts = json.loads((root/'annotation_drafts.json').read_text(encoding='utf-8'))
    drafts += json.loads((root/'annotation_drafts_additional.json').read_text(encoding='utf-8'))
    drafts += json.loads((root/'annotation_drafts_ray.json').read_text(encoding='utf-8'))
    counts = validate_draft_selection(drafts)
    # Validate all records before publishing any drafts.
    records = [materialize(root, draft) for draft in drafts]
    for repo in counts:
        subset = [r for r in records if r['repo'] == repo]
        folder = root/'data'/repo.replace('/', '__')/'annotations'
        folder.mkdir(exist_ok=True)
        write_jsonl(folder/'task_analysis_drafts.jsonl', subset)
        write_review_form(folder/'review_form.csv', subset)
        text = ['# 任务分析草稿：'+repo, '',
                '助手阅读快照后编写；独立人工复核待完成；不是最终评测标准答案。拟议验收条件需确认，项目测试未执行。', '']
        for r in subset:
            text += [f"## #{r['issue_number']} {r['title']}", '', r['goal'], '',
                     f"任务链接：{r['issue_url']}", f"源码版本：{r['source_commit']}", '', '### 技能与依据', '']
            text += [f"- {s['name']}：{s['rationale']}（{', '.join(s['evidence_ids'])}）" for s in r['required_skills_draft']]
            text += ['', '适合背景：'+r['suitable_background_draft'], '', '### 验收建议（待确认）', '']
            text += ['- '+a['criterion'] for a in r['acceptance_draft']]
            text += ['', '### 阻碍与未知项', ''] + ['- '+s for s in r['blockers']+r['unknowns']]
            text += ['', '源码观察：'+r['source_observation'], '', '### 证据', '']
            for e in r['evidence']:
                text += [f"- [{e['id']}]({e['url']})：" + (f"{e['path']}:{e['start_line']}" if 'path' in e else e['excerpt'][:170].replace('\n',' '))]
            text.append('')
        (folder/'TASK_ANALYSIS_DRAFTS.md').write_text('\n'.join(text), encoding='utf-8')
    summary = {'generated_at': utc_now(), 'counts': dict(counts), 'total_drafts': len(records),
               'source_and_test_evidence_count': sum('path' in e for r in records for e in r['evidence']),
               'independent_human_reviews_completed': 0, 'ground_truth_records': 0,
               'next_step_not_started': 'independent_review_and_versioned_gfi_analysis_fields'}
    atomic_json(root/'data/annotation_summary.json', summary)
    report = [f'# 第②步：{len(records)}条任务分析草稿', '',
              '范围：SymPy、napari、Dask、Manim、Ray各10条开放具体任务，采用目的性覆盖抽样，用作开发与试标材料。',
              '身份：助手阅读现有快照后编写，独立人工复核待完成，不能称为标准答案。', '',
              '## 本次交付', '',
              f"- 共{len(records)}条草稿，核验{summary['source_and_test_evidence_count']}处固定commit源码/测试位置。",
              '- 每条包含任务目标、3项技能要求及依据、适合背景、拟议验收、阻碍、未知项和源码观察。',
              '- Issue和关键评论与源码版本分别保存，所有路径、行号、摘录和文件哈希已在本地校验。',
              '- 本导出器不运行下载仓库的代码或项目测试，不核实最新认领/PR状态；独立模型分析结果单独保存。', '',
              '## 样本目录', '', '| 项目/任务 | 目标摘要 |', '| --- | --- |']
    for record in records:
        report.append(f"| [{record['repo']}#{record['issue_number']}]({record['issue_url']}) | {record['goal']} |")
    report += ['', '## 需要复核的具体发现', '',
               '- SymPy #28470：当前文档已包含LLVM automodule入口，仍需核实PR和文档可发现性。',
               '- SymPy #15331：评论声称替换了图示，而本地快照仍有ASCII字符图；PR状态未核验。',
               '- SymPy #11345：存在API设计异议，需要先确认任务是否仍受维护者支持。',
               '- SymPy #12267：已有按该Issue编号命名的回归测试，不能按从零实现处理。',
               '- SymPy #18081：现有回归期待未求值交集，安全回退与原帖要求的精确求解是不同目标。',
               '- napari #1256、#4292、#4585：分别已有插值、Labels空间变换继承、pooch缓存等相关实现，仍需确认剩余范围。',
               '- napari #4234：应区分无环图与唯一迁移路径；不能把原帖简单解释成只检查有无环。',
               '- napari #4292：translation指空间平移，不应作为国际化技能依据；本次重新清洗时已修正该规则。',
               '- napari #9195：点坐标投影、Points显示projection_mode和图像投影菜单是不同功能。', '',
               '- Dask #1296：当前HDF5写入代码与旧帖描述不同，延迟写入还涉及文件资源生命周期，不能仅增加一个return。',
               '- Dask #6659：分区局部索引不等于数据行重复；维护者讨论不支持直接改变默认行为。',
               '- Dask #11447：DataFrame默认表达式图与任务图走不同可视化入口，末尾与MultiIndex有关的评论不作为本任务依据。',
               '- Manim #3220：已有针对虚线箭头透明度的相关测试，需先复现剩余问题。',
               '- Manim #2018：当前示例与变更记录已有Notebook修复线索，需先确认Colab是否仍有独立问题。',
               '- Manim #3622：跨多个PR的lint规则跟踪帖已归入汇总，不进入具体任务草稿。',
               '- Manim #4983：基类异常消息已经动态化，子类仍需核对；任务作者明确鼓励新人理解代码后自行贡献。',
               '- Ray #11509：正文仅有外部讨论引用，stop的本机停止语义与address需求需要区分。',
               '- Ray #30601：S3下载客户端从旧packaging位置移动到protocol.py；默认签名与UNSIGNED路径均需考虑。',
               '- Ray #38061：忙池只排队而未生成远程引用，不能简单添加return ObjectRef。',
               '- Ray #39737：接受生成器与真正流式读取是不同目标，from_arrow当前返回物化数据。',
               '- Ray #52656：当前模板已有Jupyter主题颜色变量，需核查原帖旧版问题是否仍复现。',
               '- Ray #57729：当前已有异步UDF事件循环线程，不能按原帖版本直接断言当前仍失败。',
               '- 已关闭历史任务未进入本批开放任务草稿，也未作为成功解决标签；精确数量见当前cleaning_summary.json。', '',
               '## 结果位置与复核', '',
               '- 每项目 annotations/TASK_ANALYSIS_DRAFTS.md：便于逐条阅读。',
               '- 每项目 annotations/task_analysis_drafts.jsonl：结构化草稿与完整证据。',
               '- 每项目 annotations/review_form.csv：供独立评审填写，重复导出时不会覆盖现有表格。',
               '- docs/ANNOTATION_GUIDE.md：复核口径与标注身份说明。', '',
               '独立人工复核完成数为0，最终标准答案数为0。建议同伴先独立复核同一小批样本，记录分歧后再确定评测标签。',
               '第②步到此停止。']
    (root/'data/ANNOTATION_REPORT.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
