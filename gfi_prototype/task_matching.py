"""Demo-only task-to-task technology matching. No personal profiles or status filtering."""
import hashlib
import json
from fractions import Fraction
import re

DEMO_POLICY = {'mode': 'demo', 'assume_all_open': True, 'assume_all_unresolved': True,
               'filter_by_state_assignment_or_pr': False, 'refresh_live_status': False}


def validate_rules(rules):
    if not rules or len({r['id'] for r in rules}) != len(rules):
        raise ValueError('Technology rules must have distinct IDs')
    for rule in rules:
        if (not isinstance(rule['id'], str) or not rule['id'] or type(rule['weight']) is not int
                or rule['weight'] not in (1, 2, 3) or not isinstance(rule['pattern'], str)
                or not rule['pattern']):
            raise ValueError('Invalid technology rule')


def demo_record(bundle, record_type):
    issue = bundle['issue']
    return {'task_id': f"{bundle['repo']}#{issue['number']}", 'repo': bundle['repo'],
            'issue_number': issue['number'], 'title': issue.get('title', ''),
            'url': issue.get('html_url'), 'state_at_snapshot': issue['state'],
            'record_type': record_type, 'effective_state': 'open', 'effective_resolution': 'unresolved',
            'demo_assumption': True, 'current_availability_verified': False}


def technology_profile(draft, rules):
    validate_rules(rules)
    evidence = {e['id']: e for e in draft['evidence']}
    if len(evidence) != len(draft['evidence']):
        raise ValueError('Duplicate evidence IDs')
    tags = {}
    unmapped = []
    for index, skill in enumerate(draft['required_skills_draft'], 1):
        ids = skill['evidence_ids']
        if not ids or not set(ids).issubset(evidence):
            raise ValueError('Skill cites missing evidence')
        text = skill['name'] + '\n' + skill['rationale']
        hits = [r for r in rules if re.search(r['pattern'], text, re.IGNORECASE)]
        if not hits:
            unmapped.append(index)
        for rule in hits:
            tag = tags.setdefault(rule['id'], {k: rule[k] for k in ('id', 'name', 'category', 'weight')})
            tag.setdefault('requirement_indexes', []).append(index)
            tag['evidence_ids'] = sorted(set(tag.get('evidence_ids', [])) | set(ids))
    result = {'task_id': f"{draft['repo']}#{draft['issue_number']}", 'repo': draft['repo'],
              'issue_number': draft['issue_number'], 'title': draft['title'], 'url': draft['issue_url'],
              'source_commit': draft['source_commit'], 'goal': draft['goal'],
              'suitable_background': draft['suitable_background_draft'],
              'technology_tags': sorted(tags.values(), key=lambda t: t['id']),
              'original_requirements': draft['required_skills_draft'], 'unmapped_requirement_indexes': unmapped,
              'evidence': [{'id': e['id'], 'url': e['url']} for e in draft['evidence']],
              'analysis_origin': 'existing_assistant_task_draft_with_rule_normalization',
              'is_ground_truth': False, 'demo_assumption': True,
              'effective_state': 'open', 'effective_resolution': 'unresolved'}
    return result


def match_cache_key(source, candidate, rule_version, top_k):
    payload = json.dumps({'rule_version': rule_version, 'top_k': top_k,
                          'source_tags': source['technology_tags'], 'source_evidence': source['evidence'],
                          'candidate_tags': candidate['technology_tags'],
                          'candidate_evidence': candidate['evidence']},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def match_tasks(source, candidates, top_k=3):
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise ValueError('top_k must be integer 1..10')
    if len({r['task_id'] for r in candidates}) != len(candidates):
        raise ValueError('Duplicate candidate task IDs')
    a = {t['id']: t for t in source['technology_tags']}
    source_evidence = {e['id']: e for e in source['evidence']}
    ranked = []
    for candidate in candidates:
        if (candidate['repo'] != source['repo'] or candidate['task_id'] == source['task_id']
                or candidate.get('record_type', 'task') != 'task'):
            continue
        b = {t['id']: t for t in candidate['technology_tags']}
        shared = sorted(a.keys() & b.keys())
        if not shared:
            continue
        if any(a[key]['weight'] != b[key]['weight'] for key in shared):
            raise ValueError('Technology profiles use incompatible rule weights')
        weights = {key: tag['weight'] for key, tag in {**a, **b}.items()}
        numerator = sum(weights[key] for key in shared)
        denominator = sum(weights.values())
        score = Fraction(numerator, denominator)
        refs = {e['id']: e for e in candidate['evidence']}
        reused = [{'id': key, 'name': a[key]['name'],
                   'source_evidence': [source_evidence[eid] for eid in a[key]['evidence_ids']],
                   'candidate_evidence': [refs[eid] for eid in b[key]['evidence_ids']]} for key in shared]
        additional = [b[key] for key in sorted(b.keys() - a.keys())]
        specific_count = sum(weights[key] > 1 for key in shared)
        row = {'task_id': candidate['task_id'], 'issue_number': candidate['issue_number'],
               'title': candidate['title'], 'url': candidate['url'], 'goal': candidate['goal'],
               'source_task_id': source['task_id'], 'similarity_score': round(float(score), 6),
               'score_fraction': {'numerator': numerator, 'denominator': denominator},
               'score_meaning': 'weighted_technology_tag_jaccard_not_success_probability',
               'match_strength': 'specific_technology_overlap' if specific_count else 'generic_only',
               'specific_shared_count': specific_count,
               'shared_technologies': reused, 'additional_technologies': additional,
               'candidate_evidence': candidate['evidence'], 'suitable_background': candidate['suitable_background'],
               'demo_assumption': True, 'effective_state': 'open', 'effective_resolution': 'unresolved',
               'status_used_for_filtering': False, 'current_availability_verified': False,
               'relation_claim': 'technology_similarity_only_not_verified_learning_progression'}
        ranked.append((score, specific_count, row))
    ranked.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]['task_id']))
    return [dict(row, rank=i) for i, (_, _, row) in enumerate(ranked[:top_k], 1)]
