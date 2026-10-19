"""LangGraph research workflow; no product cache writes or portal integration."""
import copy
import json

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from typing_extensions import TypedDict

from gfi_prototype.analysis_v2 import SYSTEM_PROMPT_V2, TaskAnalysisV2, validate_analysis_v2
from gfi_prototype.research.client import BudgetExceeded
from gfi_prototype.research.evidence import audit_analysis, gate_analysis
from gfi_prototype.research.retrieval import fit_packet

PIPELINE_VERSION = 'evidence_adaptive_langgraph_v1'
VARIANTS = {'A': 'issue_only', 'B': 'fixed_retrieval', 'C': 'support_gate', 'D': 'adaptive_support_gate'}


def analyze(packet, session):
    schema = json.dumps(TaskAnalysisV2.model_json_schema(), ensure_ascii=False)
    system = SYSTEM_PROMPT_V2.replace('{schema}', schema)
    system += ('\n本研究只解释固定快照中的任务，统一按开放、待解决处理。不要推断实时状态。'
               '同一规则适用于所有实验条件：技术栈最多6项，保留必要要求而非列出整个项目依赖。'
               '证据不足应明确未知；没有源码证据时related_files/related_tests必须为空，不能编造路径。'
               '新人阶段表示完成任务所需最低能力，依据知识、修改范围和验证难度判断，不是个人画像；允许证据充分的推断。'
               '验收条件除Issue明确要求外，可列入基于Issue行为的建议验证项，basis=inferred并附证据引用；必须与明确要求分开标记，不能伪称Issue要求。'
               '优先给出最小、可执行、可验证的建议；若材料确实不足，再写入unknowns。')
    messages = [{'role': 'system', 'content': system}, {'role': 'user',
                'content': 'Evidence packet:\n' + json.dumps(packet, ensure_ascii=False)}]
    return session.request(messages, lambda value: validate_analysis_v2(value, packet).model_dump(), 'analysis')


class WorkflowState(TypedDict, total=False):
    """Only case data enters the graph; credentials and clients stay in closures."""
    packet: dict
    variant: str
    max_rounds: int
    round_index: int
    analysis: dict
    audit: dict
    rounds: list[dict]
    errors: list[dict]
    queries: list[str]
    stop_reason: str
    next_node: str
    node_trace: list[str]
    result: dict


def build_workflow(retriever, session):
    """Compile explicit nodes and conditional edges, also usable for visualization."""
    def trace(state, node):
        session._emit_progress(f"工作流节点：{node}，分析轮次={state['round_index'] + 1}。")
        return {'node_trace': [*state['node_trace'], node]}

    def failure(state, exc, retrieval=False):
        reason = ('retrieval_error' if retrieval else 'budget_exhausted'
                  if isinstance(exc, BudgetExceeded) else 'model_or_validation_error')
        return {'next_node': 'finalize', 'stop_reason': reason,
                'errors': [*state['errors'], {'round': state['round_index'],
                    'error_type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None),
                    **({} if retrieval else {'budget_reason': str(exc) if isinstance(exc, BudgetExceeded) else None})}]}

    def commit_round(state, analysis, audit=None, gated=None):
        return [*state['rounds'], {'round': state['round_index'], 'packet': copy.deepcopy(state['packet']),
            'analysis': analysis, 'audit': audit, 'gated': gated, 'retrieval': None}]

    def analyze_node(state):
        update = trace(state, 'analyze')
        try:
            analysis = analyze(state['packet'], session)
            update.update(analysis=analysis, next_node='audit')
            if state['variant'] in {'A', 'B'}:
                update.update(rounds=commit_round(state, analysis), next_node='finalize')
        except Exception as exc:
            update.update(failure(state, exc))
        return update

    def audit_node(state):
        update = trace(state, 'audit')
        try:
            update.update(audit=audit_analysis(state['analysis'], state['packet'], session), next_node='gate')
        except Exception as exc:
            update.update(failure(state, exc))
        return update

    def gate_node(state):
        update = trace(state, 'gate')
        try:
            gated = gate_analysis(state['analysis'], state['audit'])
        except Exception as exc:
            return {**update, **failure(state, exc)}
        update.update(rounds=commit_round(state, state['analysis'], state['audit'], gated), next_node='finalize')
        if state['variant'] != 'D':
            return update
        insufficient = [item for item in state['audit']['reviews']
                        if item['status'] not in {'sufficient', 'reasoned_inference'}]
        if not insufficient:
            return {**update, 'stop_reason': 'all_claims_supported'}
        if state['round_index'] >= state['max_rounds']:
            return {**update, 'stop_reason': 'supplement_round_limit'}
        queries = list(dict.fromkeys(item['retrieval_query'] for item in insufficient if item['retrieval_query'].strip()))
        if not queries:
            return {**update, 'stop_reason': 'no_targeted_query'}
        if len(session.records) + 2 > session.max_calls:
            return {**update, 'stop_reason': 'budget_exhausted'}
        return {**update, 'queries': queries, 'next_node': 'supplement'}

    def supplement_node(state):
        update = trace(state, 'supplement')
        try:
            enriched, added = retriever.supplement(copy.deepcopy(state['packet']), state['queries'], limit=4)
            rounds = copy.deepcopy(state['rounds'])
            rounds[-1]['retrieval'] = {'queries': state['queries'][:4], 'added_evidence_ids': added}
            update['rounds'] = rounds
            if not added:
                return {**update, 'stop_reason': 'no_new_evidence', 'next_node': 'finalize'}
            update.update(packet=enriched, round_index=state['round_index'] + 1, next_node='analyze')
        except Exception as exc:
            update.update(failure(state, exc, retrieval=True))
        return update

    def finalize_node(state):
        update = trace(state, 'finalize')
        last = state['rounds'][-1] if state['rounds'] else {}
        update['result'] = {'variant': state['variant'], 'condition': VARIANTS[state['variant']],
            'pipeline_version': PIPELINE_VERSION, 'status': 'complete' if state['rounds'] else 'failed',
            'stop_reason': state['stop_reason'], 'analysis': last.get('analysis'), 'audit': last.get('audit'),
            'gated': last.get('gated'), 'packet': last.get('packet', state['packet']),
            'rounds': state['rounds'], 'errors': state['errors'], 'resources': session.resources(),
            'semantic_review_status': 'pending_independent_human_review',
            'workflow': {'engine': 'langgraph', 'node_trace': update['node_trace']}}
        return update

    graph = StateGraph(WorkflowState)
    for name, node in [('analyze', analyze_node), ('audit', audit_node), ('gate', gate_node),
                       ('supplement', supplement_node), ('finalize', finalize_node)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'analyze')
    for name, targets in [('analyze', ['audit', 'finalize']), ('audit', ['gate', 'finalize']),
                          ('gate', ['supplement', 'finalize']), ('supplement', ['analyze', 'finalize'])]:
        graph.add_conditional_edges(name, lambda state: state['next_node'], {target: target for target in targets})
    graph.add_edge('finalize', END)
    return graph.compile()


def run_variant(packet, retriever, session, variant, max_rounds=2):
    """Keep the existing runner interface while executing an actual StateGraph."""
    if variant not in VARIANTS or type(max_rounds) is not int or not 0 <= max_rounds <= 2:
        raise ValueError('Invalid experimental condition or supplement rounds')
    packet = copy.deepcopy(packet)
    packet['evidence'] = [item for item in packet['evidence'] if item.get('kind') != 'comment']
    packet.pop('state_at_snapshot', None)
    if variant == 'A':
        packet['evidence'] = [item for item in packet['evidence'] if item.get('kind') == 'issue']
    initial = {'packet': fit_packet(packet), 'variant': variant, 'max_rounds': max_rounds,
               'round_index': 0, 'rounds': [], 'errors': [], 'node_trace': [], 'stop_reason': 'single_pass'}
    # No cloud tracing or checkpoint serialization of credentials or evidence.
    with tracing_context(enabled=False):
        state = build_workflow(retriever, session).invoke(initial, config={'recursion_limit': 24, 'callbacks': []})
    return state['result']
