"""Full local dataset adapted to a newcomer product, not a raw-data inspector."""
import json
import math
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, urlencode

from gfi_prototype import artifacts
from gfi_prototype.storage import read_json_or as _read_json, read_jsonl as _jsonl
from gfi_prototype.common import sha256_text
from gfi_prototype.project_catalog import PROJECTS, TYPE_LABELS


def _guide_text(value, evidence):
    """Turn internal citations into reading labels, keeping audit details internal."""
    text = str(value or '')
    text = re.sub(r'[（(]\s*[ICEST]\d+(?:\s*[、，,;/]\s*[ICEST]?\d+)*\s*[）)]', '', text)
    references = {ref['id']: (Path(ref['path']).name if ref.get('path') else
                  '任务说明' if ref.get('kind') == 'issue' else '相关背景') for ref in evidence}
    text = re.sub(r'(?<![A-Za-z0-9_])[ICEST]\d+(?![A-Za-z0-9_])',
                  lambda match: references.get(match.group(), '任务说明' if match.group().startswith('I') else '相关实现'), text)
    text = re.sub(r'([A-Za-z0-9_.-]+\.(?:py|rst|md))[/、]\1', r'\1', text)
    text = text.replace('未在证据包中给出', '仍需要进一步确认').replace('证据中未显示', '还需要确认')
    parts = re.split(r'(?<=[。；;])', text)
    secondary = r'评论|认领|指派|实时状态|(?<![A-Za-z])PR(?![A-Za-z])'
    return ''.join(part for part in parts if not re.search(secondary, part)).strip(' ；;')


def _basis_label(basis, claim_kind='requirement'):
    if basis == 'explicit':
        return 'Issue明确要求' if claim_kind == 'acceptance' else '材料明确'
    if basis == 'inferred':
        return '建议验证项' if claim_kind == 'acceptance' else '依据证据推断'
    return '待进一步确认'


def _source_rows(evidence, evidence_ids, repo):
    """Expose human-readable pinned source links, never raw evidence packets or IDs."""
    by_id = {item.get('id'): item for item in evidence if item.get('id')}
    result, seen = [], set()
    labels = {'source': '源码', 'test': '测试', 'document': '项目文档', 'issue': 'Issue'}
    for evidence_id in evidence_ids or []:
        ref = by_id.get(evidence_id)
        if not ref or not ref.get('path'):
            continue
        url = ref.get('url', '')
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
                or not parsed.path.startswith(f'/{repo}/blob/')):
            continue
        path = ref['path']
        start, end = ref.get('start_line'), ref.get('end_line')
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        location = path
        if isinstance(start, int):
            location += f':{start}' + (f'-{end}' if isinstance(end, int) and end != start else '')
        result.append({'label': location, 'url': url,
                       'kind': labels.get(ref.get('kind'), '相关源码')})
    return result


class Portal:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.records = {}
        self.projects = {}
        try:
            _, profiles, matches, _ = artifacts.latest_demo_data(self.root)
        except (OSError, ValueError):
            profiles, matches = [], []
        self.profiles = {row['task_id']: row for row in profiles}
        self.matches = {row['source_task_id']: row.get('recommendations', []) for row in matches}
        for folder in sorted((self.root / 'data').glob('*')):
            repository = _read_json(folder / 'repository.json')
            if not isinstance(repository, dict) or not (folder / 'issues.jsonl').exists():
                continue
            repo = repository['full_name']
            triage = {row['number']: row for row in _jsonl(folder / 'processed/triage.jsonl')}
            metadata = PROJECTS.get(repo, {'name': repository.get('name', repo), 'domain': '开源项目',
                'icon': 'code', 'color': '#6258d6', 'tagline': '从具体任务开始了解这个项目',
                'description': '浏览项目中已整理的任务与贡献资料。', 'topics': [repository.get('language') or '代码'],
                'modules': [], 'background': '从项目文档了解基础要求。', 'learning': '结合任务阅读项目实现。'})
            snapshot = _read_json(folder / 'source_snapshot.json', {})
            self.projects[repo] = {**metadata, 'repo': repo, 'slug': repo.replace('/', '__'),
                'url': repository.get('html_url', f'https://github.com/{repo}'),
                'stars': repository.get('stargazers_count', 0), 'language': repository.get('language') or 'Python',
                'license': (repository.get('license') or {}).get('spdx_id', ''),
                'commit': snapshot.get('commit_sha', ''), 'reading_links': self._project_links(folder, repo, snapshot)}
            for bundle in _jsonl(folder / 'issues.jsonl'):
                issue = bundle['issue']
                task_id = f"{repo}#{issue['number']}"
                classified = triage.get(issue['number'], {})
                kind = 'task' if classified.get('record_type', 'task') == 'task' else 'guide'
                self.records[task_id] = {'repo': repo, 'number': issue['number'], 'title': issue.get('title') or '',
                    'body': issue.get('body') or '', 'url': issue.get('html_url') or f'https://github.com/{repo}/issues/{issue["number"]}',
                    'kind': kind, 'types': classified.get('task_types', ['needs_review']),
                    'labels': [item['name'] for item in issue.get('labels', []) if isinstance(item, dict)]}
        self._summaries = {}
        self._summary_stamp = None
        self._refresh_summaries()

    @staticmethod
    def _project_links(folder, repo, snapshot):
        sha = snapshot.get('commit_sha') or 'HEAD'
        links = []
        for name in ('README.md', 'README.rst', 'CONTRIBUTING.md', 'CONTRIBUTING.rst',
                     'doc/src/contributing/index.rst', 'doc/source/ray-contribute/getting-involved.rst'):
            if (folder / 'source' / name).is_file():
                links.append({'label': '项目介绍' if name.startswith('README') else '贡献指南',
                              'path': name, 'url': f'https://github.com/{repo}/blob/{sha}/{name}'})
        return links

    def _refresh_summaries(self):
        paths = [self.root / 'data' / repo.replace('/', '__') / 'newcomer_summaries.json' for repo in self.projects]
        stamp = tuple((p.as_posix(), p.stat().st_mtime_ns if p.exists() else 0) for p in paths)
        if stamp == self._summary_stamp:
            return
        summaries = {}
        for path in paths:
            entries = _read_json(path, {})
            if not isinstance(entries, dict):
                continue
            for task_id, value in entries.items():
                raw = self.records.get(task_id)
                if (raw and isinstance(value, dict) and isinstance(value.get('title_zh'), str)
                        and isinstance(value.get('description'), str)
                        and value.get('origin') == 'deepseek_flash_newcomer_summary'
                        and value.get('body_sha256') == sha256_text(raw['body'])
                        and value.get('title_sha256') == sha256_text(raw['title'])):
                    summaries[task_id] = value
        self._summaries = summaries
        self._summary_stamp = stamp

    def _card(self, task_id):
        record = self.records[task_id]
        localized = self._summaries.get(task_id)
        profile = self.profiles.get(task_id, {})
        labels = [TYPE_LABELS.get(t, t) for t in record['types'] if t != 'needs_review']
        return {'task_id': task_id, 'repo': record['repo'], 'number': record['number'],
            'project_name': self.projects[record['repo']]['name'], 'kind': record['kind'], 'state': 'open',
            'title': localized['title_zh'] if localized else (profile.get('goal') or f"待整理任务 #{record['number']}"),
            'description': localized['description'] if localized else (profile.get('goal') or '中文任务说明正在整理，你可以先浏览项目介绍与其他已就绪的任务。'),
            'localized': bool(localized), 'has_analysis': bool(profile), 'url': record['url'],
            'types': record['types'], 'category_labels': labels or ['任务探索'],
            'technologies': [{'id': t['id'], 'name': t['name']} for t in profile.get('technology_tags', [])]}

    def catalog(self):
        self._refresh_summaries()
        projects = []
        order = list(PROJECTS)
        for repo in sorted(self.projects, key=lambda x: order.index(x) if x in order else 999):
            ids = [key for key, record in self.records.items() if record['repo'] == repo]
            tasks = [key for key in ids if self.records[key]['kind'] == 'task']
            preview_ids = sorted(tasks, key=lambda key: (key not in self.profiles,
                                 key != 'ManimCommunity/manim#4983', -self.records[key]['number']))[:3]
            type_counts = Counter(t for key in tasks for t in self.records[key]['types'])
            projects.append({**self.projects[repo], 'task_count': len(tasks), 'guide_count': len(ids) - len(tasks),
                'record_count': len(ids), 'analysis_count': sum(key in self.profiles for key in tasks),
                'summary_count': sum(key in self._summaries for key in ids),
                'type_filters': [{'id': key, 'name': TYPE_LABELS.get(key, key), 'count': value}
                                 for key, value in type_counts.most_common() if key != 'needs_review'],
                'previews': [self._card(key) for key in preview_ids]})
        return {'projects': projects, 'totals': {'projects': len(projects), 'records': len(self.records),
            'tasks': sum(record['kind'] == 'task' for record in self.records.values()),
            'guides': sum(record['kind'] == 'guide' for record in self.records.values()),
            'analyses': sum(key in self.profiles for key in self.records), 'chinese_summaries': len(self._summaries)},
            'notice': '任务均按开放、待解决展示，方便探索与演示。'}

    def issues(self, repo=None, query='', kind='task', ready=False, label='', page=1, page_size=12):
        if repo and repo not in self.projects:
            raise KeyError('Project not found')
        if kind not in {'task', 'guide', 'all'} or type(page) is not int or page < 1:
            raise ValueError('Invalid filters')
        if type(page_size) is not int or not 1 <= page_size <= 48:
            raise ValueError('Invalid page size')
        self._refresh_summaries()
        query = query.strip().casefold()
        result = []
        for key, record in self.records.items():
            if (repo and record['repo'] != repo) or (kind != 'all' and record['kind'] != kind):
                continue
            if ready and key not in self.profiles:
                continue
            if label and label not in record['types']:
                continue
            row = self._card(key)
            haystack = ' '.join([row['title'], row['description'], record['title'], str(record['number']),
                                 record['repo'], *record['labels'], *[t['name'] for t in row['technologies']]]).casefold()
            if query and query not in haystack:
                continue
            result.append(row)
        result.sort(key=lambda row: (not row['has_analysis'], -row['number'], row['task_id']))
        total = len(result)
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages)
        start = (page - 1) * page_size
        return {'items': result[start:start + page_size], 'total': total, 'page': page,
                'pages': pages, 'page_size': page_size}

    def issue(self, repo, number):
        task_id = f'{repo}#{number}'
        if task_id not in self.records:
            raise KeyError('Issue not found')
        self._refresh_summaries()
        row = self._card(task_id)
        profile = self.profiles.get(task_id)
        result = {**row, 'project': self.projects[repo], 'requirements': [], 'background': '',
                  'stage': None, 'steps': [], 'acceptance': [], 'unknowns': [],
                  'acceptance_claims': [], 'goal_basis': '', 'goal_sources': [],
                  'evidence_sources': [], 'code_links': [], 'recommendations': [], 'analysis_source': '',
                  'goal': row['description']}
        result['source_search_url'] = f'https://github.com/{repo}/search?' + urlencode(
            {'q': self.records[task_id]['title'], 'type': 'code'})
        if profile:
            artifact = artifacts.analysis_artifact(self.root, profile)
            analysis = artifact['analysis']
            evidence = artifact['packet'].get('evidence', [])
            clean = lambda value: _guide_text(value, evidence)
            result['analysis_source'] = 'AI 证据分析' if artifact['kind'] == 'model' else 'AI 辅助整理'
            goal = analysis.get('task_goal') or analysis.get('goal') or {}
            if not isinstance(goal, dict):
                goal = {'text': goal}
            result['goal'] = clean(goal.get('text') or profile.get('goal', row['description']))
            result['goal_basis'] = _basis_label(goal.get('basis'), 'goal')
            result['goal_sources'] = _source_rows(evidence, goal.get('evidence_ids'), repo)
            requirements = (analysis.get('technology_stack') or analysis.get('required_skills')
                            or analysis.get('required_skills_draft') or profile.get('original_requirements', []))
            result['requirements'] = [{'name': clean(item.get('name', '')),
                'purpose': clean(item.get('role_in_task') or item.get('rationale') or ''),
                'basis_label': _basis_label(item.get('basis')),
                'sources': _source_rows(evidence, item.get('evidence_ids'), repo)} for item in requirements]
            result['background'] = clean(analysis.get('suitable_background') or analysis.get('suitable_background_draft') or profile.get('suitable_background', ''))
            stage = analysis.get('newcomer_stage')
            if stage:
                result['stage'] = {'stage': stage['stage'], 'rationale': clean(stage.get('rationale')),
                                  'entry_conditions': [clean(value) for value in stage.get('entry_conditions', [])],
                                  'sources': _source_rows(evidence, stage.get('evidence_ids'), repo)}
            result['steps'] = [clean(value) for value in analysis.get('getting_started', []) if clean(value)]
            criteria = analysis.get('acceptance_criteria') or analysis.get('acceptance_draft') or []
            for item in criteria:
                text = clean(item.get('text') or item.get('criterion'))
                if not text:
                    continue
                label = _basis_label(item.get('basis'), 'acceptance')
                result['acceptance'].append(text)
                result['acceptance_claims'].append({'text': text, 'basis_label': label,
                    'sources': _source_rows(evidence, item.get('evidence_ids'), repo)})
            result['unknowns'] = [clean(value) for value in analysis.get('unknowns', []) if clean(value)]
            cited_ids = list(goal.get('evidence_ids') or [])
            for item in requirements:
                cited_ids.extend(item.get('evidence_ids') or [])
            if stage:
                cited_ids.extend(stage.get('evidence_ids') or [])
            for item in criteria:
                cited_ids.extend(item.get('evidence_ids') or [])
            result['evidence_sources'] = _source_rows(evidence, cited_ids, repo)
            seen = set()
            relevant_paths = {item['path'] for item in analysis.get('related_files', []) + analysis.get('related_tests', [])}
            for ref in evidence:
                url = ref.get('url', '')
                parsed = urlparse(url)
                if (ref.get('path') and parsed.scheme == 'https' and parsed.netloc == 'github.com'
                        and parsed.path.startswith(f'/{repo}/blob/') and ref['path'] not in seen
                        and (not relevant_paths or ref['path'] in relevant_paths)):
                    seen.add(ref['path'])
                    result['code_links'].append({'path': ref['path'], 'url': url,
                        'kind': 'test' if 'test' in ref.get('kind', '') or 'test' in ref['path'] else 'code'})
            for candidate in self.matches.get(task_id, [])[:3]:
                other_id = candidate['task_id']
                if other_id not in self.records or self.records[other_id]['repo'] != repo or self.records[other_id]['kind'] != 'task':
                    continue
                shared = [t['name'] for t in candidate.get('shared_technologies', [])]
                extra = [t['name'] for t in candidate.get('additional_technologies', [])]
                result['recommendations'].append({**self._card(other_id), 'shared': shared, 'additional': extra,
                    'reason': f'两个任务都会用到{"、".join(shared)}，可以对照理解相近的技术。',
                    'new_learning': f'还会接触{"、".join(extra)}。' if extra else '已标注的技术中没有额外新增项。',
                    'general_overlap': candidate.get('match_strength') == 'generic_only'})
        result['other_tasks'] = [item for item in self.issues(repo, page_size=4)['items'] if item['task_id'] != task_id][:3]
        return result
