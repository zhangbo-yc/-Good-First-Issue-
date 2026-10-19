#!/usr/bin/env python3
"""Retrieve one local issue's evidence, optionally analyze it with DeepSeek Flash."""
import argparse
import importlib.metadata
import json
import os
import sys
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

from gfi_prototype.common import atomic_json, read_token, safe_text, utc_now
from gfi_prototype.retrieval import EvidenceStore
from gfi_prototype.settings import load_project_env

REPOSITORIES=('sympy/sympy','napari/napari','dask/dask','ManimCommunity/manim','ray-project/ray')
BASE_URL='https://api.deepseek.com'
def write_report(folder,packet,result):
    evidence={item['id']:item for item in packet['evidence']}
    lines=[f"# 单任务分析：{packet['repo']}#{packet['issue_number']}",'',
           '这是DeepSeek真实调用生成的待复核分析；自动核验仅覆盖结构、证据ID、路径和源码摘录，不证明结论语义正确。',
           f"源码版本：{packet['source_commit']}", '当前认领/PR状态和运行时复现均未核验。','']
    analysis=result['analysis']
    def refs(ids):
        return ' '.join(f'[{eid}]({evidence[eid]["url"]})' for eid in ids)
    def claim(item):
        return safe_text(item['text'])+' '+refs(item['evidence_ids'])+('（推断/建议）' if item['basis']=='inferred' else '')
    lines+=['## 任务目标','',claim(analysis['task_goal']),'','## 所需能力','']
    for skill in analysis['required_skills']:
        lines.append('- '+safe_text(skill['name'])+'：'+safe_text(skill['rationale'])+' '+refs(skill['evidence_ids'])+('（推断）' if skill['basis']=='inferred' else ''))
    lines+=['','## 相关文件','']
    lines += ['- '+safe_text(item['path'])+' '+refs(item['evidence_ids']) for item in analysis['related_files']]
    for title,items in [('验收条件草稿（待确认）',analysis['acceptance_criteria']),('阻碍',analysis['blockers'])]:
        lines+=['',f'## {title}','']+['- '+claim(item) for item in items]
    lines+=['','## 未知项','']+['- '+safe_text(item) for item in analysis['unknowns']]
    lines+=['','## 证据位置','']
    for item in packet['evidence']:
        label=f"{item['path']}:{item['start_line']}-{item['end_line']}" if 'path' in item else item['kind']
        lines.append(f'- [{item["id"]}]({item["url"]}) '+safe_text(label))
    (folder/'ANALYSIS_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def _select_analysis_impl(schema_version):
    from gfi_prototype import analysis
    if schema_version=='v1':
        return analysis.analyze_packet,write_report
    if schema_version!='v2':
        raise ValueError("schema_version must be 'v1' or 'v2'")
    from gfi_prototype import analysis_v2
    return analysis_v2.analyze_packet_v2,analysis_v2.write_report_v2


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--repo',choices=REPOSITORIES,default='ManimCommunity/manim')
    parser.add_argument('--issue',type=int,default=4983)
    parser.add_argument('--mode',choices=['retrieve','analyze'],default='retrieve')
    parser.add_argument('--schema-version',choices=['v1','v2'],default='v2')
    parser.add_argument('--model',default='deepseek-flash')
    parser.add_argument('--prompt-key',action='store_true')
    parser.add_argument('--max-sources',type=int,default=8)
    parser.add_argument('--max-attempts',type=int,choices=[1,2],default=2)
    args=parser.parse_args(argv)
    load_project_env(args.root.resolve())
    project=args.root.resolve()/'data'/args.repo.replace('/','__')
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+str(args.issue)+'_'+args.mode+'_'+uuid.uuid4().hex[:6]
    output=project/'prototype_runs'/run_id
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    manifest={'started_at':utc_now(),'repo':args.repo,'issue_number':args.issue,'mode':args.mode,
              'status':'running','live_model_calls_started':0,'gold_annotations_used':False,
              'current_availability_verified':False,'runtime_reproduction':'not_run','output_directory':str(output)}
    atomic_json(output/'run_manifest.json',manifest)
    key=None
    try:
        store=EvidenceStore(project)
        print('Building/checking source index...',flush=True)
        index=store.build_index()
        packet=store.evidence_bundle(args.issue,args.max_sources)
        atomic_json(output/'evidence_bundle.json',packet)
        manifest.update(source_commit=store.sha,index=index,evidence_count=len(packet['evidence']),
                        source_evidence_count=packet['source_evidence_found'])
        if args.mode=='retrieve':
            manifest['status']='evidence_ready_no_model_call'
        else:
            from langchain_deepseek import ChatDeepSeek
            from langchain_core.runnables import RunnableLambda
            from pydantic import SecretStr
            analyze_impl,report_impl=_select_analysis_impl(args.schema_version)
            key=read_token('DeepSeek API key (hidden): ') if args.prompt_key else os.environ.get('DEEPSEEK_API_KEY','').strip()
            if not key:
                raise ValueError('Use --prompt-key or set DEEPSEEK_API_KEY')
            if any(ord(char)<33 or ord(char)>126 for char in key):
                raise ValueError('API key contains invalid characters')
            if key in json.dumps(packet):
                raise ValueError('Credential detected in evidence packet; refusing model call')
            usage=[]
            model=ChatDeepSeek(model=args.model,api_key=SecretStr(key),base_url=BASE_URL,
                              temperature=0,max_tokens=4096,max_retries=0,timeout=90,
                              extra_body={'thinking':{'type':'disabled'}}).bind(response_format={'type':'json_object'})
            def call_model(prompt):
                manifest['live_model_calls_started']+=1
                response=model.invoke(prompt)
                usage.append({'usage':response.usage_metadata,'returned_model':response.response_metadata.get('model_name'),
                              'finish_reason':response.response_metadata.get('finish_reason')})
                return response
            print(f'Calling DeepSeek model {args.model}; bounded attempts={args.max_attempts}',flush=True)
            result=analyze_impl(packet,RunnableLambda(call_model),args.max_attempts)
            atomic_json(output/'analysis.json',result)
            report_impl(output,packet,result)
            manifest.update(status='analysis_ready_pending_human_review',model=args.model,provider='DeepSeek',
                            base_url=BASE_URL,usage=usage,validation_scope=result['validation_scope'],
                            schema_version=2 if args.schema_version=='v2' else 1,
                            semantic_review_status='pending_human_review',is_ground_truth=False)
            if args.schema_version=='v2':
                manifest['prompt_version']=result.get('prompt_version')
        manifest.update(finished_at=utc_now(),elapsed_seconds=round(time.monotonic()-started,3),
                        packages={name:importlib.metadata.version(name) for name in ['langchain-core','langchain-deepseek','pydantic']})
        atomic_json(output/'run_manifest.json',manifest)
        print(json.dumps({'status':manifest['status'],'model_calls':manifest['live_model_calls_started'],'output':str(output)},ensure_ascii=True),flush=True)
        return 0
    except Exception as exc:
        # Persist error type/status only: SDK messages may contain credentials or request details.
        manifest.update(status='failed',finished_at=utc_now(),error_type=type(exc).__name__,
                        http_status=getattr(exc,'status_code',None),elapsed_seconds=round(time.monotonic()-started,3))
        atomic_json(output/'run_manifest.json',manifest)
        print(json.dumps({'status':'failed','error_type':manifest['error_type'],'http_status':manifest['http_status'],
                          'output':str(output)},ensure_ascii=True),flush=True)
        return 1


if __name__=='__main__':
    sys.exit(main())
