"""Independent, offline recommendation utilities for frozen research profiles.

A -> B reuse = sum(weights of shared tags) / sum(B tag weights).
Tag load = min(1, sum(B-only weights)/6). Module load = min(1,
number of B-only source modules/2) when both profiles cite source paths;
otherwise module load is unknown and contributes zero observed load.
Burden = min(1, .75*tag_load + .25*observed_module_load).
Utility = .5*reuse - .3*burden + .2*min(source_quality, candidate_quality).
This is a transparent utility, not a probability or verified learning progression.
Missing audit quality or unmapped requirements excludes a task, never implying
mastery. Module evidence describes cited paths, not exhaustive code familiarity.

rank_directional(source, candidates, quality_by_task, top_k=3, weights=None)
returns ranked explanation dictionaries. weights must contain nonnegative finite
reuse/burden/evidence coefficients. rank_random(source, candidates, top_k=3,
seed=20261017) returns a deterministic seeded baseline from the same structural
pool; it intentionally does not gate on model evidence quality. Both functions
reject duplicate candidate IDs and require top_k in 1..10. explain_exclusions
returns task_id/reason records for directional eligibility omissions.
"""
import math
from pathlib import PurePosixPath
import random
import re
from urllib.parse import unquote, urlparse

_DEFAULT_WEIGHTS = {'reuse': .5, 'burden': .3, 'evidence': .2}
_IGNORED_DIRECTORIES = {'doc', 'docs', 'test', 'tests', 'examples', '.github'}


def _finite(value, label, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)):
        raise ValueError(f'Invalid {label}')
    return float(value)


def _validate_top_k(top_k):
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise ValueError('top_k must be integer 1..10')


def _tags(profile):
    result = {}
    evidence = {item['id'] for item in profile.get('evidence', [])}
    for tag in profile.get('technology_tags', []):
        key = tag.get('id')
        if not isinstance(key, str) or not key.strip() or key in result:
            raise ValueError('Technology IDs must be nonempty and unique')
        weight = _finite(tag.get('weight'), 'technology weight')
        if weight <= 0:
            raise ValueError('Technology weight must be positive')
        ids = tag.get('evidence_ids', [])
        if not ids or not set(ids).issubset(evidence):
            raise ValueError('Technology cites missing evidence')
        result[key] = tag
    return result


def _pool(source, candidates, top_k):
    _validate_top_k(top_k)
    candidates = list(candidates)
    ids = [item['task_id'] for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate candidate task IDs')
    # Validate every profile, so malformed excluded data is never silently accepted.
    known_weights = {}
    for profile in [source] + candidates:
        for key, tag in _tags(profile).items():
            if key in known_weights and known_weights[key] != tag['weight']:
                raise ValueError('Technology profiles use incompatible rule weights')
            known_weights[key] = tag['weight']
    return candidates


def _structural_reason(source, candidate):
    if (source.get('record_type', 'task') != 'task'
            or type(source.get('issue_number')) is not int or source['issue_number'] <= 0
            or not source.get('title') or not source.get('goal')):
        return 'source_not_concrete_task'
    if candidate['task_id'] == source['task_id']:
        return 'self'
    if candidate['repo'] != source['repo']:
        return 'different_repository'
    if candidate.get('record_type', 'task') != 'task':
        return 'not_concrete_task'
    if (type(candidate.get('issue_number')) is not int or candidate['issue_number'] <= 0
            or not candidate.get('title') or not candidate.get('goal')):
        return 'not_concrete_task'
    return None


def _unmapped(profile):
    return bool(profile.get('unmapped_requirement_indexes') or profile.get('unmapped_indexes')
                or profile.get('unknown_technologies'))


def _quality(quality_by_task):
    return {key: None if value is None else _finite(value, 'evidence quality', maximum=1)
            for key, value in quality_by_task.items()}


def _reason(source, candidate, quality):
    reason = _structural_reason(source, candidate)
    if reason:
        return reason
    for label, profile in (('source', source), ('candidate', candidate)):
        if quality.get(profile['task_id']) is None:
            return f'missing_{label}_evidence_quality'
        if _unmapped(profile):
            return f'unmapped_{label}_technologies'
        if not profile.get('technology_tags'):
            return f'unknown_{label}_technologies'
    return None


def explain_exclusions(source, candidates, quality_by_task):
    """Explain omissions without assuming missing quality is a perfect audit."""
    candidates = _pool(source, candidates, 10)
    quality = _quality(quality_by_task)
    return [{'task_id': candidate['task_id'], 'reason': reason}
            for candidate in candidates if (reason := _reason(source, candidate, quality))]


def _modules(profile):
    paths = list(profile.get('source_paths', []))
    for item in profile.get('evidence', []):
        # Only immutable GitHub source citations qualify; Issue text is not a path.
        parsed = urlparse(item.get('url', ''))
        prefix = '/' + profile['repo'] + '/blob/'
        if parsed.hostname == 'github.com' and parsed.path.startswith(prefix):
            remainder = parsed.path[len(prefix):].split('/', 1)
            if len(remainder) == 2 and re.fullmatch(r'[0-9a-fA-F]{40}', remainder[0]):
                paths.append(unquote(remainder[1]))
    modules = set()
    for path in paths:
        if not isinstance(path, str) or '\\' in path:
            continue
        parts = PurePosixPath(path).parts
        if (not parts or PurePosixPath(path).is_absolute() or '..' in parts
                or any(part.lower() in _IGNORED_DIRECTORIES for part in parts[:-1])):
            continue
        if parts[0] == 'src':
            parts = parts[1:]
        # A module is the first two directories, or root for root-level files.
        directories = parts[:-1]
        modules.add('/'.join(directories[:2]) if directories else '<root>')
    return modules


def _metadata(source, candidate):
    return {**{key: candidate.get(key) for key in ('task_id', 'repo', 'issue_number', 'title', 'url', 'goal')},
            'source_task_id': source['task_id'],
            'relation_claim': 'technology_relation_only_not_verified_learning_progression'}


def rank_directional(source, candidates, quality_by_task, top_k=3, weights=None):
    """Rank eligible profiles with B-side coverage, absolute load and audit quality."""
    candidates = _pool(source, candidates, top_k)
    quality = _quality(quality_by_task)
    coefficients = dict(_DEFAULT_WEIGHTS if weights is None else weights)
    if set(coefficients) != set(_DEFAULT_WEIGHTS):
        raise ValueError('weights must contain reuse, burden and evidence')
    coefficients = {key: _finite(value, 'score weight') for key, value in coefficients.items()}
    a = _tags(source)
    source_modules = _modules(source)
    source_evidence = {item['id']: item for item in source.get('evidence', [])}
    ranked = []
    for candidate in candidates:
        if _reason(source, candidate, quality):
            continue
        b = _tags(candidate)
        shared, added = sorted(a.keys() & b.keys()), sorted(b.keys() - a.keys())
        total = sum(tag['weight'] for tag in b.values())
        reuse = sum(b[key]['weight'] for key in shared) / total
        added_weight = sum(b[key]['weight'] for key in added)
        tag_load = min(1., added_weight / 6)
        candidate_modules = _modules(candidate)
        module_available = bool(source_modules and candidate_modules)
        new_modules = sorted(candidate_modules - source_modules) if module_available else []
        module_load = min(1., len(new_modules) / 2) if module_available else None
        # Unknown cited-module coverage receives a neutral prior instead of the
        # same advantage as observed zero new modules.
        module_component = module_load if module_load is not None else .5
        burden = min(1., .75 * tag_load + .25 * module_component)
        source_quality, candidate_quality = quality[source['task_id']], quality[candidate['task_id']]
        evidence_quality = min(source_quality, candidate_quality)
        components = {'reuse': reuse, 'novelty_burden': burden, 'evidence_quality': evidence_quality}
        contributions = {'reuse': coefficients['reuse'] * reuse,
                         'burden': -coefficients['burden'] * burden,
                         'evidence': coefficients['evidence'] * evidence_quality}
        score = sum(contributions.values())
        refs = {item['id']: item for item in candidate.get('evidence', [])}
        row = dict(_metadata(source, candidate), score=score, components=components,
                   score_contributions=contributions, weights=coefficients,
                   score_meaning='transparent_utility_not_probability',
                   source_evidence_quality=source_quality, candidate_evidence_quality=candidate_quality,
                   shared_technologies=[dict(b[key], source_evidence=[source_evidence[eid] for eid in a[key]['evidence_ids']],
                                             candidate_evidence=[refs[eid] for eid in b[key]['evidence_ids']]) for key in shared],
                   additional_technologies=[dict(b[key]) for key in added],
                   added_technology_weight=added_weight, technology_burden=tag_load,
                   new_modules=new_modules, module_burden=module_load,
                   module_information_available=module_available,
                   module_missing_policy=None if module_available else 'neutral_prior_0.5',
                   module_scope='observed_cited_paths_only',
                   match_strength='specific_technology_overlap' if any(b[key]['weight'] > 1 for key in shared)
                   else ('generic_only' if shared else 'no_shared_technology'))
        ranked.append(row)
    ranked.sort(key=lambda row: (-row['score'], row['task_id']))
    return [dict(row, rank=index) for index, row in enumerate(ranked[:top_k], 1)]


def rank_random(source, candidates, top_k=3, seed=20261017):
    """Sample the structural frozen pool deterministically without audit gating."""
    candidates = _pool(source, candidates, top_k)
    pool = sorted((candidate for candidate in candidates if not _structural_reason(source, candidate)),
                  key=lambda candidate: candidate['task_id'])
    random.Random(seed).shuffle(pool)
    return [dict(_metadata(source, candidate), rank=index, method='random', seed=seed)
            for index, candidate in enumerate(pool[:top_k], 1)]
