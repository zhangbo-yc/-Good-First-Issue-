"""Offline independent gold collection, blinded review and conservative metrics.

score_reviews(bundle_dir, reviews=None, gold=None) accepts JSON arrays or paths.
Defaults read assessor/reviewer_1.json, reviewer_2.json and gold/gold.json.
Missing labels remain pending. The private directory must never go to assessors.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import random

from .protocol import source_annotation_template


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def _load(value):
    return json.loads(Path(value).read_text(encoding='utf-8-sig')) if isinstance(value, (str, Path)) else value


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get('text', value.get('claim', value.get('name', value.get('stage', ''))))
    return ''


def _claim(value, requirement_id):
    result = {'requirement_id': requirement_id, 'text': _text(value)}
    if isinstance(value, dict):
        for key in ('evidence_ids', 'technology_id', 'role_in_task', 'rationale', 'entry_conditions'):
            if key in value:
                result[key] = value[key]
    return result


def _prediction(result):
    original = result.get('analysis') or {}
    # An explicitly gated object is the actual delivered answer, including an empty answer.
    analysis = result.get('gated') or original
    requirements = analysis.get('requirements', analysis.get('technology_stack', []))
    stage = _claim(analysis.get('stage', analysis.get('newcomer_stage', '')), 'stage')
    stage['eligibility'] = 'abstention' if stage['text'] in ('信息不足', '') else 'judgment'
    goal = analysis.get('goal', analysis.get('task_goal', ''))
    if (isinstance(goal, str) and isinstance(original.get('task_goal'), dict)
            and original['task_goal'].get('text') == goal):
        goal = original['task_goal']
    return {'goal': _claim(goal, 'goal'),
            'requirements': [_claim(item, f'R{i}') for i, item in enumerate(requirements, 1)],
            'stage': stage,
            'acceptance': [_claim(item, f'C{i}') for i, item in enumerate(
                analysis.get('acceptance', analysis.get('acceptance_criteria', [])), 1)],
            'unknowns': [str(v) for v in analysis.get('unknowns', [])]}


def _public_evidence(evidence):
    return {key: evidence[key] for key in
            ('kind', 'url', 'excerpt', 'text', 'path', 'start_line', 'end_line', 'commit_sha')
            if key in evidence}


def _evidence_key(evidence):
    return json.dumps(_public_evidence(evidence), sort_keys=True, ensure_ascii=False)


def _remap_citations(prediction, mapping):
    for claim in [prediction['goal'], prediction['stage'], *prediction['requirements'], *prediction['acceptance']]:
        if 'evidence_ids' in claim:
            claim['evidence_ids'] = [mapping[eid] for eid in claim['evidence_ids'] if eid in mapping]
    return prediction


def build_review_bundle(root, protocol, results, output_dir):
    """Write assessor/, gold/ (independent inputs), private/ (method mapping).

    Optional result.packet/evidence_bundle supplies public evidence excerpts.
    Reviewer schemas are written as editable JSON, with null unreviewed labels.
    """
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('Refusing to overwrite review materials')
    tasks = {task['task_id']: task for task in protocol['tasks']}
    results = list(results)
    identities = [(r['task_id'], r.get('variant')) for r in results]
    if len(set(identities)) != len(identities) or any(r['task_id'] not in tasks for r in results):
        raise ValueError('Duplicate or unknown result task/variant')
    common = defaultdict(dict)
    for result in results:
        packet = result.get('packet') or result.get('evidence_bundle') or {}
        for evidence in packet.get('evidence', []):
            if evidence.get('kind') != 'comment':
                common[result['task_id']][_evidence_key(evidence)] = _public_evidence(evidence)
    common_ids = {task_id: {key: f'X{index:03d}' for index, key in enumerate(sorted(rows), 1)}
                  for task_id, rows in common.items()}
    common_rows = {task_id: [dict(common[task_id][key], id=identifier)
                    for key, identifier in ids.items()] for task_id, ids in common_ids.items()}
    random.Random(protocol.get('seed', 20261017)).shuffle(results)
    predictions, private, forms = [], [], []
    for index, result in enumerate(results, 1):
        prediction_id = f'P{index:04d}'
        packet = result.get('packet') or result.get('evidence_bundle') or {}
        citation_map = {evidence['id']: common_ids[result['task_id']][_evidence_key(evidence)]
                        for evidence in packet.get('evidence', []) if evidence.get('kind') != 'comment'}
        item = {'prediction_id': prediction_id, 'task_id': result['task_id'],
                **_remap_citations(_prediction(result), citation_map),
                'evidence': common_rows.get(result['task_id'], [])}
        predictions.append(item)
        private.append({'prediction_id': prediction_id, 'task_id': result['task_id'],
                        'variant': result.get('variant'), 'run_id': result.get('run_id'),
                        'status': result.get('status')})
        forms.append({'prediction_id': prediction_id, 'stage_reasonable': None,
            'goal_correct': None, 'goal_citation_supported': None,
            'requirements': [{'requirement_id': r['requirement_id'], 'correct': None,
                'citation_supported': None, 'gold_ids': None} for r in item['requirements']], 'notes': None})
    gold = source_annotation_template(root, list(tasks.values()))
    rules_path = Path(root) / 'technology_rules.json'
    rules = _load(rules_path) if rules_path.exists() else None
    if isinstance(rules, dict):
        rules = rules['rules']
    _write(output_dir / 'gold' / 'optional_technology_vocabulary.json', [r['id'] for r in rules] if rules is not None else None)
    _write(output_dir / 'gold' / 'gold.json', gold)
    _write(output_dir / 'assessor' / 'sources.json', [{k: v for k, v in row.items() if k in
        ('task_id', 'title', 'issue_url', 'issue_body', 'source_url', 'source_commit')} for row in gold])
    _write(output_dir / 'assessor' / 'predictions.json', predictions)
    for reviewer in ('reviewer_1', 'reviewer_2'):
        _write(output_dir / 'assessor' / f'{reviewer}.json', [dict(row, reviewer_id=reviewer) for row in forms])
    _write(output_dir / 'private' / 'mapping.json', private)
    (output_dir / 'REVIEW_GUIDE.md').write_text(
        'Give gold/ to independent source annotators before they see any predictions. '
        'They fill completed=true, annotator_id, expected_technology_ids and requirements '
        '[{"id":"G1","text":"requirement supported by source"}], plus source-based stage/notes. '
        'The optional technology vocabulary is only a coding aid: add independent new concept IDs '
        'freely, including requirements absent the system vocabulary. Never derive gold from system '
        'predictions. Give only assessor/ and the independently '
        'completed gold to two reviewers. Keep private/ withheld. Each reviewer independently '
        'fills true/false for goal_correct, goal_citation_supported, each requirement correct '
        'and citation_supported, and stage_reasonable, plus gold_ids '
        'with equivalent gold requirement IDs (empty list means no equivalent). null means pending. '
        'Judge citations against the supplied evidence and pinned source; missing excerpts must '
        'remain pending until independently inspected. Do not infer correctness from citation syntax. '
        'Do not consult issue comments. Requirement recall counts distinct gold IDs matched by '
        'correct predictions. Scores average the two reviewers; disagreement is retained. '
        'Stage eligibility=abstention means the answer withheld a stage (including 信息不足); judge '
        'whether that abstention is reasonable from independent sources, never default it to false. '
        'Empty denominator metrics are null. Cohen kappa pools paired binary correctness, citation '
        'goal and stage labels. Recommendation review is separate. Sources describe the collection-time '
        'snapshot, not historical repair outcomes.\n', encoding='utf-8')
    return {'output_dir': str(output_dir), 'predictions': len(predictions), 'status': 'pending'}


def _kappa(pairs):
    if not pairs:
        return None
    n = len(pairs)
    agreement = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(first[k] * second[k] for k in set(first) | set(second)) / n**2
    return (agreement - chance) / (1 - chance) if chance != 1 else None


def score_reviews(bundle_dir, reviews=None, gold=None):
    """Validate all IDs/labels. Return null effect metrics until every form is complete."""
    bundle_dir = Path(bundle_dir)
    predictions = _load(bundle_dir / 'assessor' / 'predictions.json')
    prediction_map = {p['prediction_id']: p for p in predictions}
    gold = _load(gold if gold is not None else bundle_dir / 'gold' / 'gold.json')
    if reviews is None:
        reviews = sum((_load(bundle_dir / 'assessor' / f'{r}.json') for r in ('reviewer_1', 'reviewer_2')), [])
    else:
        reviews = _load(reviews)
    if not isinstance(gold, list) or not isinstance(reviews, list):
        raise ValueError('Gold and reviews must be arrays')
    gold_map = {}
    pending = []
    expected_tasks = {p['task_id'] for p in predictions}
    known_tasks = {s['task_id'] for s in _load(bundle_dir / 'assessor' / 'sources.json')}
    for row in gold:
        task_id = row.get('task_id')
        if task_id in gold_map or task_id not in known_tasks:
            raise ValueError('Duplicate or unknown gold task')
        gold_map[task_id] = row
        requirements = row.get('requirements')
        if requirements is not None:
            if not isinstance(requirements, list) or any(not isinstance(r, dict) or not r.get('id') or not r.get('text') for r in requirements):
                raise ValueError('Gold requirements require id and text')
            if len({r['id'] for r in requirements}) != len(requirements):
                raise ValueError('Duplicate gold requirement ID')
        technologies = row.get('expected_technology_ids')
        if technologies is not None and (not isinstance(technologies, list) or any(not isinstance(t, str) or not t for t in technologies) or len(set(technologies)) != len(technologies)):
            raise ValueError('Invalid expected technology IDs')
        if (row.get('completed') is not True or not row.get('annotator_id') or requirements is None
                or technologies is None or not _text(row.get('goal')) or not _text(row.get('stage'))):
            pending.append(f'gold:{task_id}')
    for task_id in expected_tasks - set(gold_map):
        pending.append(f'gold:{task_id}')
    review_map = {}
    for row in reviews:
        key = (row.get('reviewer_id'), row.get('prediction_id'))
        if key[0] not in ('reviewer_1', 'reviewer_2') or key[1] not in prediction_map or key in review_map:
            raise ValueError('Duplicate or unknown reviewer/prediction ID')
        review_map[key] = row
        prediction = prediction_map[key[1]]
        requirement_ids = {r['requirement_id'] for r in prediction['requirements']}
        items = row.get('requirements')
        if not isinstance(items, list):
            raise ValueError('Review requirements must be an array')
        if len({r.get('requirement_id') for r in items}) != len(items) or any(r.get('requirement_id') not in requirement_ids for r in items):
            raise ValueError('Duplicate or unknown reviewed requirement ID')
        if {r.get('requirement_id') for r in items} != requirement_ids:
            pending.append(f'requirements:{key}')
        stage = row.get('stage_reasonable')
        if stage is not None and type(stage) is not bool:
            raise ValueError('Review labels must be boolean or null')
        if stage is None:
            pending.append(f'stage:{key}')
        for label in ('goal_correct', 'goal_citation_supported'):
            if row.get(label) is not None and type(row[label]) is not bool:
                raise ValueError('Goal labels must be boolean or null')
            if row.get(label) is None:
                pending.append(f'{label}:{key}')
        gold_row = gold_map.get(prediction['task_id'], {})
        valid_gold = {g['id'] for g in gold_row.get('requirements') or []}
        for item in items:
            for label in ('correct', 'citation_supported'):
                if item.get(label) is not None and type(item[label]) is not bool:
                    raise ValueError('Review labels must be boolean or null')
                if item.get(label) is None:
                    pending.append(f'{label}:{key}')
            ids = item.get('gold_ids')
            if ids is not None and (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not set(ids).issubset(valid_gold)):
                raise ValueError('Unknown or duplicate gold equivalence ID')
            if ids is None:
                pending.append(f'equivalence:{key}')
            if item.get('correct') is False and ids:
                raise ValueError('Incorrect requirement cannot match gold')
    for prediction_id in prediction_map:
        for reviewer in ('reviewer_1', 'reviewer_2'):
            if (reviewer, prediction_id) not in review_map:
                pending.append(f'review:{reviewer}:{prediction_id}')
    empty = {'status': 'pending', 'requirement_precision': None, 'requirement_recall': None,
             'goal_correctness': None, 'goal_citation_support': None,
             'requirement_coverage': None, 'citation_support': None, 'stage_reasonableness': None,
             'cohen_kappa': None, 'by_prediction': [], 'paired_results': [], 'pending': pending}
    if pending or not predictions:
        return empty
    pairs, precision, recall, citation, stage, per_prediction = [], [], [], [], [], []
    goal_correctness, goal_citation_support = [], []
    for prediction in predictions:
        pid = prediction['prediction_id']
        gold_ids = {r['id'] for r in gold_map[prediction['task_id']]['requirements']}
        scores = []
        for reviewer in ('reviewer_1', 'reviewer_2'):
            row = review_map[(reviewer, pid)]
            items = row['requirements']
            correct = [r['correct'] for r in items]
            supported = [r['citation_supported'] for r in items]
            matched = {gid for r in items if r['correct'] for gid in r['gold_ids']}
            scores.append({'reviewer_id': reviewer,
                'goal_correctness': float(row['goal_correct']),
                'goal_citation_support': float(row['goal_citation_supported']),
                'requirement_precision': sum(correct) / len(correct) if correct else None,
                'requirement_recall': len(matched) / len(gold_ids) if gold_ids else None,
                'citation_support': sum(supported) / len(supported) if supported else None,
                'stage_reasonableness': float(row['stage_reasonable'])})
            precision.extend(correct)
            citation.extend(supported)
            if gold_ids:
                recall.append(len(matched) / len(gold_ids))
            stage.append(row['stage_reasonable'])
            goal_correctness.append(row['goal_correct'])
            goal_citation_support.append(row['goal_citation_supported'])
        a, b = [review_map[(r, pid)] for r in ('reviewer_1', 'reviewer_2')]
        b_items = {r['requirement_id']: r for r in b['requirements']}
        pairs.append((a['stage_reasonable'], b['stage_reasonable']))
        for label in ('goal_correct', 'goal_citation_supported'):
            pairs.append((a[label], b[label]))
        for item in a['requirements']:
            for label in ('correct', 'citation_supported'):
                pairs.append((item[label], b_items[item['requirement_id']][label]))
        per_prediction.append({'prediction_id': pid, 'task_id': prediction['task_id'], 'reviews': scores,
            'goal_correctness': sum(s['goal_correctness'] for s in scores) / len(scores),
            'goal_citation_support': sum(s['goal_citation_support'] for s in scores) / len(scores)})
    mean = lambda values: sum(values) / len(values) if values else None
    # Per-prediction paired rows permit A–D comparison only after unblinding privately.
    paired = defaultdict(list)
    for row in per_prediction:
        paired[row['task_id']].append(row)
    return {'status': 'complete', 'requirement_precision': mean(precision), 'requirement_recall': mean(recall),
            'goal_correctness': mean(goal_correctness), 'goal_citation_support': mean(goal_citation_support),
            'requirement_coverage': mean(recall), 'citation_support': mean(citation),
            'stage_reasonableness': mean(stage), 'cohen_kappa': _kappa(pairs),
            'by_prediction': per_prediction, 'paired_results': [{'task_id': k, 'predictions': v} for k, v in paired.items()],
            'pending': [], 'aggregation': 'precision/citation micro across requirement labels; recall/goal/stage macro; two reviewers equal weight'}


def build_recommendation_review_bundle(root, protocol, recommendations, output_dir):
    """Blind recommendation lists. Input rows: method/source_task_id/recommendations.

    recommendation entries must have task_id; all algorithm scores are discarded.
    Returns a pending bundle with two independent boolean reasonableness forms.
    """
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('Refusing to overwrite recommendation reviews')
    allowed = set(protocol['candidate_pool'])
    sources = {task['task_id'] for task in protocol['tasks']} | allowed
    rows, mapping, forms = [], [], []
    task_meta = {task['task_id']: task for task in protocol['tasks']}

    def source_row(task_id):
        repo, number_text = task_id.rsplit('#', 1)
        number = int(number_text)
        path = Path(root) / 'data' / repo.replace('/', '__') / 'issues' / f'{number}.json'
        issue = {}
        if path.exists():
            issue = _load(path).get('issue', {})
        meta = task_meta.get(task_id, {})
        commit = meta.get('source_commit') or protocol.get('source_snapshots', {}).get(repo, {}).get('source_commit', '')
        return {'task_id': task_id, 'title': issue.get('title') or meta.get('title') or task_id,
            'issue_url': issue.get('html_url') or f'https://github.com/{repo}/issues/{number}',
            'issue_body': issue.get('body') or '', 'source_commit': commit,
            'source_url': f'https://github.com/{repo}/tree/{commit}' if commit else f'https://github.com/{repo}'}
    recommendations = list(recommendations)
    random.Random(protocol.get('seed', 20261017)).shuffle(recommendations)
    seen = set()
    for i, row in enumerate(recommendations, 1):
        key = (row.get('method'), row.get('source_task_id'))
        if key in seen or not key[0] or key[1] not in sources:
            raise ValueError('Duplicate or invalid recommendation identity')
        seen.add(key)
        ids = [r['task_id'] if isinstance(r, dict) else r for r in row['recommendations']]
        if len(set(ids)) != len(ids) or any(t not in allowed or t == key[1] for t in ids):
            raise ValueError('Recommendation outside frozen pool, duplicate or self')
        review_id = f'L{i:04d}'
        rows.append({'review_id': review_id, 'source_task_id': key[1], 'recommended_task_ids': ids})
        mapping.append({'review_id': review_id, 'method': key[0], 'source_task_id': key[1]})
        forms.append({'review_id': review_id, 'recommendations': [{'task_id': t, 'relevant': None,
            'knowledge_reuse': None, 'new_load_manageable': None, 'reasonable': None,
            'notes': None} for t in ids]})
    _write(output_dir / 'assessor' / 'recommendations.json', rows)
    involved = sorted({row['source_task_id'] for row in rows} |
                      {task_id for row in rows for task_id in row['recommended_task_ids']})
    _write(output_dir / 'assessor' / 'sources.json', [source_row(task_id) for task_id in involved])
    for reviewer in ('reviewer_1', 'reviewer_2'):
        _write(output_dir / 'assessor' / f'{reviewer}.json', [dict(row, reviewer_id=reviewer) for row in forms])
    _write(output_dir / 'private' / 'mapping.json', mapping)
    (output_dir / 'REVIEW_GUIDE.md').write_text(
        'Give assessor/ only to each reviewer; keep private/ hidden until both finish. '
        'Use sources.json and the pinned source links, never method scores or issue comments. '
        'For every recommended task set relevant=true only when it addresses a meaningfully related task; '
        'knowledge_reuse=true only when prior task knowledge is genuinely reusable; '
        'new_load_manageable=true only when the additional concepts form a reasonable next step for a newcomer; '
        'reasonable is the overall judgment. Use false rather than null after judging; null means pending. '
        'Reviewers work independently.\n', encoding='utf-8')
    return {'output_dir': str(output_dir), 'status': 'pending', 'lists': len(rows)}


def score_recommendation_reviews(bundle_dir, reviews=None):
    """Score independent recommendation reasonableness; null until both reviewers finish."""
    bundle_dir = Path(bundle_dir)
    packets = {r['review_id']: r for r in _load(bundle_dir / 'assessor' / 'recommendations.json')}
    if reviews is None:
        reviews = sum((_load(bundle_dir / 'assessor' / f'{r}.json') for r in ('reviewer_1', 'reviewer_2')), [])
    else:
        reviews = _load(reviews)
    seen, ratings, pending = set(), {}, []
    criteria = ('relevant', 'knowledge_reuse', 'new_load_manageable', 'reasonable')
    for row in reviews:
        reviewer, review_id = row.get('reviewer_id'), row.get('review_id')
        key = reviewer, review_id
        if reviewer not in ('reviewer_1', 'reviewer_2') or review_id not in packets or key in seen:
            raise ValueError('Duplicate or unknown recommendation review identity')
        seen.add(key)
        expected = set(packets[review_id]['recommended_task_ids'])
        items = row.get('recommendations', [])
        actual = [item.get('task_id') for item in items]
        if len(set(actual)) != len(actual) or not set(actual).issubset(expected):
            raise ValueError('Duplicate or unknown recommended task')
        if set(actual) != expected:
            pending.append(f'missing:{key}')
        for item in items:
            for criterion in criteria:
                value = item.get(criterion)
                if value is not None and type(value) is not bool:
                    raise ValueError('Recommendation rating must be boolean or null')
                if value is None:
                    pending.append(f'{criterion}:{key}:{item["task_id"]}')
                else:
                    ratings[(reviewer, review_id, item['task_id'], criterion)] = value
    for review_id in packets:
        for reviewer in ('reviewer_1', 'reviewer_2'):
            if (reviewer, review_id) not in seen:
                pending.append(f'review:{reviewer}:{review_id}')
    if pending or not packets:
        return {'status': 'pending', 'recommendation_reasonableness': None, 'relevance': None,
                'knowledge_reuse': None, 'new_load_manageable': None,
                'cohen_kappa': None, 'by_list': [], 'pending': pending}
    pairs = [(ratings[('reviewer_1', review_id, task_id, criterion)],
              ratings[('reviewer_2', review_id, task_id, criterion)])
             for review_id, row in packets.items() for task_id in row['recommended_task_ids']
             for criterion in criteria]
    by_list = []
    for review_id, row in packets.items():
        criterion_values = {criterion: [ratings[(reviewer, review_id, task_id, criterion)]
            for reviewer in ('reviewer_1', 'reviewer_2') for task_id in row['recommended_task_ids']]
            for criterion in criteria}
        by_list.append({'review_id': review_id, 'source_task_id': row['source_task_id'],
                        'recommended_items': len(row['recommended_task_ids']),
                        **{criterion: sum(values)/len(values) if values else None
                           for criterion, values in criterion_values.items()}})
    overall = {criterion: [value for key, value in ratings.items() if key[-1] == criterion]
               for criterion in criteria}
    return {'status': 'complete', 'recommendation_reasonableness':
            sum(overall['reasonable'])/len(overall['reasonable']) if overall['reasonable'] else None,
            'relevance': sum(overall['relevant'])/len(overall['relevant']) if overall['relevant'] else None,
            'knowledge_reuse': sum(overall['knowledge_reuse'])/len(overall['knowledge_reuse']) if overall['knowledge_reuse'] else None,
            'new_load_manageable': sum(overall['new_load_manageable'])/len(overall['new_load_manageable']) if overall['new_load_manageable'] else None,
            'cohen_kappa': _kappa(pairs), 'by_list': by_list, 'pending': []}


def compare_variant_scores(bundle_dir, scored):
    """Unblind only after complete independent scoring; report per condition."""
    if scored.get('status') != 'complete':
        return {'status': 'pending', 'by_variant': {}, 'paired_task_count': 0}
    mapping = {row['prediction_id']: row for row in _load(Path(bundle_dir) / 'private' / 'mapping.json')}
    grouped = defaultdict(list)
    for row in scored['by_prediction']:
        if row['prediction_id'] not in mapping:
            raise ValueError('Missing private method mapping')
        reviews = row['reviews']
        def average(key):
            values = [item[key] for item in reviews if item[key] is not None]
            return sum(values) / len(values) if values else None
        grouped[mapping[row['prediction_id']]['variant']].append({'task_id': row['task_id'],
            'goal_correctness': row['goal_correctness'], 'goal_citation_support': row['goal_citation_support'],
            'requirement_precision': average('requirement_precision'),
            'requirement_recall': average('requirement_recall'),
            'citation_support': average('citation_support'),
            'stage_reasonableness': average('stage_reasonableness')})
    metrics = ('goal_correctness', 'goal_citation_support', 'requirement_precision',
               'requirement_recall', 'citation_support', 'stage_reasonableness')
    by_variant = {}
    for variant, rows in sorted(grouped.items()):
        by_variant[variant] = {'tasks': len(rows), **{metric:
            (sum(row[metric] for row in rows if row[metric] is not None) /
             sum(row[metric] is not None for row in rows)) if any(row[metric] is not None for row in rows) else None
            for metric in metrics}}
    sets = [set(row['task_id'] for row in rows) for rows in grouped.values()]
    paired = len(set.intersection(*sets)) if sets else 0
    return {'status': 'complete', 'by_variant': by_variant, 'paired_task_count': paired,
            'note': 'Descriptive paired comparison; no significance claim is made.'}


def compare_recommendation_methods(bundle_dir, scored):
    if scored.get('status') != 'complete':
        return {'status': 'pending', 'by_method': {}}
    mapping = {row['review_id']: row for row in _load(Path(bundle_dir) / 'private' / 'mapping.json')}
    grouped = defaultdict(list)
    for row in scored.get('by_list', []):
        if row['review_id'] not in mapping:
            raise ValueError('Missing private recommendation mapping')
        grouped[mapping[row['review_id']]['method']].append(row)
    criteria = ('relevant', 'knowledge_reuse', 'new_load_manageable', 'reasonable')
    by_method = {}
    for method, rows in sorted(grouped.items()):
        defined = {criterion: [row[criterion] for row in rows if row[criterion] is not None]
                   for criterion in criteria}
        by_method[method] = {'lists': len(rows),
            'recommended_items': sum(row['recommended_items'] for row in rows),
            **{criterion: sum(values)/len(values) if values else None
               for criterion, values in defined.items()}}
    return {'status': 'complete', 'by_method': by_method}
