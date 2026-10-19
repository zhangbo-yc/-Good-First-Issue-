"""PyCharm entry: fresh legacy/frontend D runs for one known Ray case.

Default run calls DeepSeek; --phase prepare/report is offline. No automatic
judge and no edits to historical experiments. Results are diagnostic only.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

from gfi_prototype.common import atomic_json, sha256_file, utc_now
from gfi_prototype.research.client import DeepSeekClient
from gfi_prototype.research.pipeline import run_variant
from gfi_prototype.research.retrieval import SnapshotRetriever
from gfi_prototype.research.frontend_retrieval import FrontendSnapshotRetriever, FRONTEND_SUFFIXES
from gfi_prototype.research.equal_budget import paid_attempts
from gfi_prototype.settings import load_project_env
from run_equal_budget_experiment import freeze_configuration, read
from run_equal_budget_resilient import DiagnosticSession

ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/'experiments/frontend_case_ray49011_v1'
REPO='ray-project/ray'
ISSUE=49011
PROFILES={'legacy':'原源码范围','frontend':'新增JS/TS源码范围'}


def safe_output(path):
    value=Path(path).resolve()
    if value.parent!=ROOT/'experiments' or not value.name.startswith('frontend_case_'):
        raise ValueError('必须使用独立experiments/frontend_case_*目录，不允许覆盖旧实验。')
    return value


def prepare(output):
    print('[准备] 固定单案例配置与两份初始证据；不调用模型。',flush=True)
    retrievers={'legacy':SnapshotRetriever(ROOT,REPO),'frontend':FrontendSnapshotRetriever(ROOT,REPO)}
    old=retrievers['legacy'].store
    if not old.index.exists():
        raise ValueError('缺少原索引，本脚本不会自动创建或重建它。')
    old_hash=sha256_file(old.index)
    with closing(sqlite3.connect(old.index.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        meta=json.loads(db.execute('SELECT value FROM metadata WHERE key="index"').fetchone()[0])
    if (not isinstance(meta,dict) or meta.get('archive_sha256')!=old.snapshot['archive_sha256'] or meta.get('repo')!=REPO
            or meta.get('commit_sha')!=old.sha or meta.get('index_version')!=2):
        raise ValueError('原索引身份不匹配；拒绝重建或使用不明来源索引。')
    # Existing legacy index is read-only in this diagnostic; archive verification
    # in _ensure_index/verify_bundle remains active.
    retrievers['legacy']._indexed=True
    packets={}
    for profile,retriever in retrievers.items():
        packets[profile]=retriever.initial(ISSUE)
        # Keep profile provenance outside the model input. Only evidence differs.
        packets[profile].pop('retrieval_profile',None)
    if sha256_file(old.index)!=old_hash:
        raise ValueError('Legacy index changed')
    files=['run_frontend_case.py','run_equal_budget_resilient.py','gfi_prototype/research/frontend_retrieval.py',
           'gfi_prototype/research/equal_budget.py','gfi_prototype/research/pipeline.py',
           'gfi_prototype/research/evidence.py','gfi_prototype/research/client.py',
           'gfi_prototype/research/retrieval.py','gfi_prototype/retrieval.py',
           'gfi_prototype/analysis.py','gfi_prototype/analysis_v2.py']
    config=freeze_configuration(output/'design.json',{
        'version':'frontend_case_diagnostic_v1','task_id':f'{REPO}#{ISSUE}','model':'deepseek-flash',
        'scope':'posthoc_known_failure_case_not_new_holdout_or_34_task_result',
        'profiles':PROFILES,'max_complete_rounds':3,'max_calls_per_attempt':6,
        'max_output_tokens':5000,'max_context_chars':60000,'max_reported_tokens':250000,
        'max_case_attempts_per_run':3,'equal_actual_calls_or_tokens':False,
        'source_commit':old.sha,'archive_sha256':sha256_file(old.archive),
        'raw_issue_sha256':sha256_file(old.folder/'issues'/f'{ISSUE}.json'),
        'legacy_index_sha256':old_hash,'frontend_index_sha256':sha256_file(retrievers['frontend'].store.index),
        'code_sha256':{p:sha256_file(ROOT/p) for p in files},
        'runtime':{p:importlib.metadata.version(p) for p in ['langgraph','langchain-core','langchain-deepseek']},
        'initial_packets':packets})
    return config,packets,retrievers


def saved_result(output,config,profile):
    path=output/'results'/f'{profile}.json'
    if not path.exists():return None
    value=read(path)
    if value.get('config_sha256')!=config['config_sha256'] or value.get('retrieval_condition')!=profile:
        raise ValueError('单案例缓存身份不匹配。')
    return value if value.get('status')=='complete' and not value.get('errors') else None


def run_case(output,config,packets,retrievers,key,attempts=3):
    client=None; failed=[]
    for index,(profile,label) in enumerate(PROFILES.items(),1):
        print(f'\n[{index}/2] {label}',flush=True)
        if saved_result(output,config,profile):
            print('  复用已完成结果，不产生新调用。',flush=True)
            continue
        if client is None:client=DeepSeekClient(key,config['model'],max_output_tokens=5000)
        for attempt in range(1,attempts+1):
            print(f'  本轮续跑机会{attempt}/{attempts}；成功节点会复用。',flush=True)
            session=DiagnosticSession(client,output/'requests'/profile,max_calls=6,
                on_progress=lambda message:print('    '+message,flush=True))
            value=run_variant(packets[profile],retrievers[profile],session,'D',max_rounds=2)
            value={**value,'status':'failed' if value.get('errors') else value['status'],
                'config_sha256':config['config_sha256'],'retrieval_condition':profile,
                'task_id':f'{REPO}#{ISSUE}','created_at':utc_now(),
                'stage_cache_hits':session.cache_hits,'new_calls_this_attempt':session.new_calls}
            atomic_json(output/'results'/f'{profile}.json',value)
            if value['status']=='complete':
                print(f'  完成：{len(value["rounds"])}轮，{value["resources"]["model_calls"]}次逻辑调用，'
                      f'{value["resources"]["total_tokens"]}个已记录Token。',flush=True)
                break
        else:
            failed.append(profile)
            print('  暂存未完成条件，继续另一条件。',flush=True)
    return failed


def write_report(output,config):
    path=ROOT/'output/前端源码单案例验证_说明与结果.md'
    if output!=OUTPUT:path=ROOT/'output'/f'前端源码单案例验证_{output.name}.md'
    lines=['# 前端源码覆盖：单案例诊断','',
        '**任务：Ray #49011，Dashboard展示数据最后加载时间，并可能提供手动刷新。**','',
        '这是已经看过失败结果后的改进案例，不是新未见测试，不合入旧34题评分。'
        '两种配置均重新生成D流程答案，不用历史旧答案充当本轮基线。模型、提示词和校验相同，'
        '最多3轮、6次逻辑调用；实际自适应轮数和Token可能不同，不声称严格等成本。',
        '仅改变检索范围及新范围内的文件过滤。新索引加入JS/TS，同时会改变词法排序与测试片段选择，'
        '因此本轮比较的是整个检索配置，不能将变化完全归因于某一个文件扩展名。','',
        '## 运行方式','',
        'PyCharm直接运行`run_frontend_case.py`，或：','```powershell',
        '.\\.venv\\Scripts\\python.exe run_frontend_case.py','```','',
        '默认会调用DeepSeek生成两份答案，不自动调用Pro评审。每条件最多3次续跑机会，失败成本留档；'
        '再次运行会复用完成条件和成功节点。每份最多6次逻辑调用，失败后的新增尝试另计。',
        '只准备、不调用模型：`run_frontend_case.py --phase prepare`。'
        '只更新报告：`run_frontend_case.py --phase report`。','',
        f'结果目录：`{output.as_posix()}`。配置固定后，代码或输入变化需新建`experiments/frontend_case_*`目录。','',
        '## 当前结果','']
    for profile,label in PROFILES.items():
        lines += [f'### {label}','']
        result=saved_result(output,config,profile)
        if result is None:
            lines += ['尚未完成。本条件不作效果结论。',''];continue
        gated=result['gated']; analysis=result['analysis']; packet=result['packet']
        paths=list(dict.fromkeys(e['path'] for e in packet['evidence'] if e.get('path')))
        front=[p for p in paths if Path(p).suffix.lower() in FRONTEND_SUFFIXES]
        cost=paid_attempts(output/'requests'/profile)
        lines += [f'- 总轮数：{len(result["rounds"])}；逻辑调用：{result["resources"]["model_calls"]}；'
                  f'已记录Token：{result["resources"]["total_tokens"]}；停止原因：`{result["stop_reason"]}`。',
                  f'- 实际尝试：{cost["model_calls"]}；失败：{cost["failed_calls"]}；用量完整：{cost["usage_complete"]}。',
                  f'- 最终证据包不同文件{len(paths)}个，其中JS/TS文件{len(front)}个。','',
                  '**最终JS/TS证据路径：**','',*(['- `'+p+'`' for p in front] or ['- 无。']),'',
                  '**门控后技术要求：**','',
                  *(['- '+r['name']+'：'+r['role_in_task'] for r in gated['requirements']] or ['- 暂缓／未保留。']),'',
                  '**门控后验收项：**','',
                  *(['- '+r['text']+('（建议验证）' if r['basis']=='inferred' else '（明确要求）') for r in gated['acceptance']] or ['- 暂缓／未保留。']),'',
                  '**原始配套入手步骤（未全部经过核心结论门控）：**','',
                  *[f'{i}. {step}' for i,step in enumerate(analysis.get('getting_started',[]),1)],'',
                  '**仍然未知：**','',*['- '+v for v in gated['unknowns']],'']
    lines += ['## 结果应如何解释','',
        '先核对是否命中Dashboard前端数据获取／刷新相关代码，再判断最终技术要求、入口和验证建议是否更具体。'
        '文件数增加不等于内容更相关，步骤更长不等于指导更好。',
        '**本脚本不自动打质量分，不预设新版更好，也未执行浏览器功能修复或仓库测试。**'
        '运行后将本报告交给助手分析，结合逐轮记录比较得失。单案例不能证明总体提升或恢复旧实验失分。','']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines),encoding='utf-8')
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['prepare','run','report'],default='run')
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args(argv)
    output=safe_output(args.output)
    config,packets,retrievers=prepare(output)
    report=write_report(output,config)
    if args.phase!='run':
        print(f'离线准备／汇总完成：{report}；未调用模型。',flush=True);return 0
    load_project_env(ROOT)
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:raise RuntimeError('请先配置.env中的DEEPSEEK_API_KEY。')
    try:
        failed=run_case(output,config,packets,retrievers,key)
    except Exception as exc:
        atomic_json(output/'last_error.json',{'type':type(exc).__name__,'created_at':utc_now()})
        write_report(output,config)
        print(f'单案例暂停：{type(exc).__name__}；请保留缓存，重跑同一脚本。',flush=True);return 1
    report=write_report(output,config)
    print(f'本轮结束：{report}；未完成条件：{len(failed)}。不自动评审或改PPT。',flush=True)
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
