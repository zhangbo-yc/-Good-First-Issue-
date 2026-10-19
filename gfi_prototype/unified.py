"""Map validated v2 model analyses onto the matching layer's technology profiles."""
import re

from gfi_prototype.task_matching import validate_rules


def normalize_analysis_tags(analysis, rules, evidence):
    if 'technology_stack' not in analysis:
        raise ValueError('v2 analysis must contain technology_stack; v1 analyses are not mapped by this adapter')
    validate_rules(rules)
    evidence_by_id = {item['id']: item for item in evidence}
    if len(evidence_by_id) != len(evidence):
        raise ValueError('Duplicate evidence IDs')
    tags = {}
    unmapped = []
    for index, technology in enumerate(analysis['technology_stack'], 1):
        ids = technology['evidence_ids']
        if not ids or not set(ids).issubset(evidence_by_id):
            raise ValueError('Technology cites missing evidence')
        text = technology['name'] + '\n' + technology['role_in_task']
        hits = [rule for rule in rules if re.search(rule['pattern'], text, re.IGNORECASE)]
        if not hits:
            unmapped.append(index)
        for rule in hits:
            tag = tags.setdefault(rule['id'], {key: rule[key] for key in ('id', 'name', 'category', 'weight')})
            tag.setdefault('requirement_indexes', []).append(index)
            tag['evidence_ids'] = sorted(set(tag.get('evidence_ids', [])) | set(ids))
    return {'technology_tags': sorted(tags.values(), key=lambda tag: tag['id']), 'unmapped_indexes': unmapped}


def analysis_profile(analysis, meta, rules, evidence):
    if analysis.get('demo_assumption') is not True:
        raise ValueError('v2 analysis must keep demo_assumption=True')
    normalized = normalize_analysis_tags(analysis, rules, evidence)
    return {'task_id': f"{meta['repo']}#{meta['issue_number']}", 'repo': meta['repo'],
            'issue_number': meta['issue_number'], 'title': meta['title'], 'url': meta['url'],
            'source_commit': meta['source_commit'], 'goal': analysis['task_goal']['text'],
            'task_scope': analysis['task_scope'], 'suitable_background': analysis['suitable_background'],
            'newcomer_stage': analysis['newcomer_stage'], 'getting_started': analysis['getting_started'],
            'acceptance_criteria': [claim['text'] for claim in analysis['acceptance_criteria']],
            'unknowns': analysis['unknowns'], 'technology_tags': normalized['technology_tags'],
            'original_requirements': analysis['technology_stack'],
            'unmapped_requirement_indexes': normalized['unmapped_indexes'],
            'evidence': [{'id': item['id'], 'url': item['url']} for item in evidence],
            'analysis_origin': 'real_model_analysis_v2', 'is_ground_truth': False, 'demo_assumption': True,
            'effective_state': 'open', 'effective_resolution': 'unresolved',
            'issue_content_sha256': meta.get('issue_content_sha256', '')}
