"""Versioned task analysis schema v2: technology stack, background and newcomer stage.

Reuses the v1 building blocks (StrictModel/Claim/RelatedFile and the repaired
prompt|model|validate chain) and only extends what the model is asked to produce.
Identity and version fields stay outside the model output on purpose: the runner
writes them into the run manifest and the analysis cache.
"""
import json
from typing import Literal

from pydantic import Field

from gfi_prototype.analysis import SYSTEM_PROMPT, Claim, RelatedFile, StrictModel, run_analyze_chain
from gfi_prototype.common import safe_text


SCHEMA_VERSION=2
PROMPT_VERSION='analysis_v2_2026-10-16'


class Technology(StrictModel):
    technology_id:str=Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    name:str=Field(min_length=2)
    category:str=Field(min_length=1)
    role_in_task:str=Field(min_length=3)
    evidence_ids:list[str]=Field(min_length=1)
    basis:Literal['explicit','inferred']


class NewcomerStage(StrictModel):
    stage:Literal['基础入门','具备开发基础','已有一定项目经验','信息不足']
    entry_conditions:list[str]=Field(min_length=1,max_length=6)
    rationale:str=Field(min_length=3)
    evidence_ids:list[str]=Field(min_length=1)


class TaskAnalysisV2(StrictModel):
    schema_version:Literal[2]
    task_goal:Claim
    task_scope:str=Field(min_length=3)
    technology_stack:list[Technology]=Field(min_length=1,max_length=12)
    required_knowledge:list[str]=Field(max_length=8)
    related_files:list[RelatedFile]=Field(max_length=12)
    related_tests:list[RelatedFile]=Field(max_length=12)
    suitable_background:str=Field(min_length=3)
    newcomer_stage:NewcomerStage
    getting_started:list[str]=Field(min_length=1,max_length=8)
    acceptance_criteria:list[Claim]=Field(max_length=6)
    unknowns:list[str]=Field(min_length=1,max_length=12)
    evidence:list[str]=Field(min_length=1)
    demo_assumption:Literal[True]


# v2 appends to the pinned v1 rules instead of restating them, so the shared rules
# cannot drift between prompt versions.
V2_RULES='''
Additional rules for schema version 2:
- technology_stack: one entry per technology the task actually needs, and every entry must state role_in_task as the concrete work it is used for
  (for example "used to compare array indexing behaviour"), never just the library name. Use basis=explicit when the issue or source states it, basis=inferred when you deduced it.
- newcomer_stage: pick exactly one stage - 基础入门 (needs only basics), 具备开发基础 (can read and change code), 已有一定项目经验 (needs prior project experience),
  or 信息不足 (the evidence cannot support a judgement; this is the required choice whenever that is true). List the entry_conditions a contributor must already satisfy
  and justify the stage with evidence_ids.
- getting_started: give a short reading order over the cited evidence, then what to run or inspect to verify a change.
- required_knowledge lists knowledge areas, not steps; unknowns lists what the evidence cannot settle.
Keep every evidence id and path exactly as supplied, and satisfy every schema bound shown above.'''

SYSTEM_PROMPT_V2=SYSTEM_PROMPT+'\n'+V2_RULES


def validate_analysis_v2(value,packet):
    if hasattr(value,'content'):
        value=value.content
    if isinstance(value,str):
        value=json.loads(value)
    result=value if isinstance(value,TaskAnalysisV2) else TaskAnalysisV2.model_validate(value)
    evidence={item['id']:item for item in packet['evidence']}
    if len(evidence)!=len(packet['evidence']):
        raise ValueError('Duplicate evidence IDs in packet')
    cited=[result.task_goal,*result.technology_stack,result.newcomer_stage,
           *result.related_files,*result.related_tests,*result.acceptance_criteria]
    for item in cited:
        if not set(item.evidence_ids).issubset(evidence):
            raise ValueError('Analysis cites unknown evidence IDs')
    if not set(result.evidence).issubset(evidence):
        raise ValueError('Analysis evidence list cites unknown evidence IDs')
    for item in [*result.related_files,*result.related_tests]:
        if not all(evidence[eid].get('path')==item.path for eid in item.evidence_ids):
            raise ValueError('Related file does not match its cited source evidence')
    return result


def analyze_packet_v2(packet,model,max_attempts=2):
    result,attempts,failures=run_analyze_chain(packet,model,max_attempts,SYSTEM_PROMPT_V2,
                                               json.dumps(TaskAnalysisV2.model_json_schema(),ensure_ascii=False),
                                               validate_analysis_v2)
    return {'analysis':result.model_dump(),'schema_version':SCHEMA_VERSION,'prompt_version':PROMPT_VERSION,
            'attempts':attempts,'validation_failures':failures,
            'validation_scope':'schema_and_citation_references_only','semantic_review_status':'pending_human_review',
            'is_ground_truth':False,'current_availability_verified':False}


def write_report_v2(folder,packet,result):
    evidence={item['id']:item for item in packet['evidence']}
    analysis=result['analysis']
    def refs(ids):
        return ' '.join(f'[{eid}]({evidence[eid]["url"]})' for eid in ids)
    def claim(item):
        return safe_text(item['text'])+' '+refs(item['evidence_ids'])+('（推断/建议）' if item['basis']=='inferred' else '')
    def basis_label(item):
        return '明确' if item['basis']=='explicit' else '推断'
    lines=[f"# 单任务分析（schema v2）：{packet['repo']}#{packet['issue_number']}",'',
           '这是DeepSeek真实调用生成的待复核分析；自动核验仅覆盖结构/引用，语义待人工复核。',
           f"源码版本：{packet['source_commit']}",
           '当前认领/PR状态和运行时复现均未核验。',
           '演示假设：所有已采集GFI统一按开放、未解决处理；不刷新实时状态。','']
    lines+=['## 任务目标','',claim(analysis['task_goal']),'',
            '## 任务范围','',safe_text(analysis['task_scope']),'',
            '## 技术栈','']
    for item in analysis['technology_stack']:
        lines.append(f"- {safe_text(item['name'])}（{safe_text(item['category'])}，{basis_label(item)}）：{safe_text(item['role_in_task'])} {refs(item['evidence_ids'])}")
    lines+=['','## 所需知识','']+['- '+safe_text(item) for item in analysis['required_knowledge']]
    lines+=['','## 适合背景','',safe_text(analysis['suitable_background'])]
    stage=analysis['newcomer_stage']
    lines+=['','## 新手阶段','',f"阶段：{safe_text(stage['stage'])}",'','进入条件：']
    lines+=['- '+safe_text(item) for item in stage['entry_conditions']]
    lines+=['',f"依据：{safe_text(stage['rationale'])} {refs(stage['evidence_ids'])}"]
    lines+=['','## 开始与验证','']
    lines+=[f"{index}. {safe_text(item)}" for index,item in enumerate(analysis['getting_started'],1)]
    lines+=['','## 相关文件','']+['- '+safe_text(item['path'])+' '+refs(item['evidence_ids']) for item in analysis['related_files']]
    lines+=['','## 相关测试','']+['- '+safe_text(item['path'])+' '+refs(item['evidence_ids']) for item in analysis['related_tests']]
    lines+=['','## 验收条件','']+['- '+claim(item) for item in analysis['acceptance_criteria']]
    lines+=['','## 未知项','']+['- '+safe_text(item) for item in analysis['unknowns']]
    lines+=['','## 证据位置','']
    for item in packet['evidence']:
        label=f"{item['path']}:{item['start_line']}-{item['end_line']}" if 'path' in item else item['kind']
        lines.append(f'- [{item["id"]}]({item["url"]}) '+safe_text(label))
    (folder/'ANALYSIS_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
