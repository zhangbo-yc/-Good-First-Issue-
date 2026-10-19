"""Frozen recommendation experiment integration; isolated from portal results."""
import copy
import json
from pathlib import Path

from gfi_prototype import analysis_cache, unified
from gfi_prototype.common import atomic_json, utc_now
from gfi_prototype.research.directional import rank_directional, rank_random
from gfi_prototype.research.evidence import audit_analysis, quality_score
from gfi_prototype.task_matching import match_tasks


def sum_attempt_resources(attempts):
    rows = [item.get('resources', {}) for item in attempts]
    return {'model_calls': sum(row.get('model_calls', 0) for row in rows),
            'prompt_tokens': sum(row.get('prompt_tokens', 0) for row in rows),
            'completion_tokens': sum(row.get('completion_tokens', 0) for row in rows),
            'total_tokens': sum(row.get('total_tokens', 0) for row in rows),
            'elapsed_seconds': round(sum(row.get('elapsed_seconds', 0) for row in rows), 4),
            'usage_complete': all(row.get('usage_complete', False) for row in rows)}


def profile_from_result(result, rules):
    if result.get('variant') != 'D' or result.get('status') != 'complete' or not result.get('gated'):
        raise ValueError('Directional recommendation sources require a complete D result')
    requirements = result['gated'].get('requirements', [])
    if not requirements or not result['gated'].get('goal'):
        raise ValueError('Source has no supported goal or technology requirements')
    raw = copy.deepcopy(result['analysis'])
    gated_goal = result['gated']['goal']
    raw['task_goal'] = copy.deepcopy(gated_goal) if isinstance(gated_goal, dict) else {
        **raw['task_goal'], 'text': gated_goal}
    raw['technology_stack'] = requirements
    packet = result['packet']
    meta = {'repo': packet['repo'], 'issue_number': packet['issue_number'],
            'title': packet.get('title', result['task_id']), 'url': packet.get('issue_url', ''),
            'source_commit': packet['source_commit'], 'issue_content_sha256': ''}
    profile = unified.analysis_profile(raw, meta, rules, packet['evidence'])
    profile['record_type'] = 'task'
    value = quality_score(result['audit'])
    if value is None:
        raise ValueError('Source technology quality is unknown')
    return profile, value


def load_candidate_profiles(root, protocol, rules):
    profiles = []
    for task_id in protocol['candidate_pool']:
        repo = task_id.rsplit('#', 1)[0]
        profile = analysis_cache.profile_from_cached_analysis(
            Path(root) / 'data' / repo.replace('/', '__'), task_id, rules)
        profile['record_type'] = 'task'
        profiles.append(profile)
    return profiles


def run_recommendation_methods(source, candidates, quality_by_task, top_k=3, seed=20261017):
    candidates = list(candidates)
    return {'random': rank_random(source, candidates, top_k, seed),
            'jaccard': match_tasks(source, candidates, top_k),
            'directional': rank_directional(source, candidates, quality_by_task, top_k)}


def audit_candidate_profiles(root, protocol, rules, session_factory, output_path, repos=None):
    """Audit each frozen candidate once. Resume complete records; never overwrite audits."""
    output_path = Path(output_path)
    stored = json.loads(output_path.read_text(encoding='utf-8')) if output_path.exists() else {}
    profiles = load_candidate_profiles(root, protocol, rules)
    if repos is not None:
        repos = set(repos)
        profiles = [profile for profile in profiles if profile['repo'] in repos]
    for profile in profiles:
        task_id = profile['task_id']
        if (stored.get(task_id, {}).get('status') == 'complete'
                and isinstance(stored[task_id].get('resources'), dict)):
            continue
        repo = profile['repo']
        folder = Path(root) / 'data' / repo.replace('/', '__')
        entry = analysis_cache.read_analysis_cache(folder)[task_id]
        run = folder / entry['run_dir']
        result = json.loads((run / 'analysis.json').read_text(encoding='utf-8'))
        packet = json.loads((run / 'evidence_bundle.json').read_text(encoding='utf-8'))
        session = session_factory(task_id)
        history = list(stored.get(task_id, {}).get('attempts', []))
        try:
            audit = audit_analysis(result['analysis'], packet, session)
            attempt = {'status': 'complete', 'resources': session.resources(), 'updated_at': utc_now(),
                       'error_type': None, 'http_status': None}
            history.append(attempt)
            stored[task_id] = {'status': 'complete', 'audit': audit, 'quality': quality_score(audit),
                               'resources': session.resources(), 'cumulative_resources': sum_attempt_resources(history),
                               'attempts': history, 'updated_at': utc_now(),
                               'analysis_run': entry['run_dir']}
        except Exception as exc:
            attempt = {'status': 'failed', 'resources': session.resources(), 'updated_at': utc_now(),
                       'error_type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None)}
            history.append(attempt)
            stored[task_id] = {'status': 'failed', 'error_type': type(exc).__name__,
                               'http_status': getattr(exc, 'status_code', None),
                               'resources': session.resources(), 'cumulative_resources': sum_attempt_resources(history),
                               'attempts': history, 'updated_at': utc_now()}
        atomic_json(output_path, stored)
    return stored
