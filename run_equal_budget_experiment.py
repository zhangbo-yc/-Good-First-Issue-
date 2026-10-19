"""PyCharm entry: fresh D versus matched-round R/S controls, with resumable stages.

Default all runs DeepSeek analysis, then a separate Pro blind review and report.
Use --phase prepare to freeze/inspect without any API request.
"""
import argparse
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import sys

from gfi_prototype.common import atomic_json, sha256_file, sha256_text, utc_now
from gfi_prototype.research.client import DeepSeekClient
from gfi_prototype.research.equal_budget import (VERSION, METHODS, CachedSession, FixedQueryRetriever,
                                                run_control, normalized_judge_payload, paid_attempts)
from gfi_prototype.research.pipeline import run_variant
from gfi_prototype.research.protocol import verify_protocol
from gfi_prototype.settings import load_project_env

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT/'experiments/frozen_20261017/equal_calls_v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def freeze_configuration(path, payload):
    fingerprint = sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    expected = {**payload, 'config_sha256': fingerprint}
    path = Path(path)
    if path.exists():
        old = read(path)
        if {k:v for k,v in old.items() if k!='created_at'} != expected:
            raise ValueError('Frozen configuration changed; use a new equal_calls_* output directory. 配置不兼容。')
        return old
    result = {**expected, 'created_at':utc_now()}
    atomic_json(path, result)
    return result


def safe_output(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.parent != root/'experiments/frozen_20261017' or not output.name.startswith('equal_calls_'):
        raise ValueError('Output must be a separate experiments/frozen_20261017/equal_calls_* directory')
    return output


def prepare(output, model, judge_model):
    print('[准备] 核对原冻结协议与39题清单；不读取旧评分、不按得分筛题。', flush=True)
    protocol_path = ROOT/'experiments/frozen_20261017/protocol.json'
    manifest_path = ROOT/'experiments/frozen_20261017/final_heldout_v3/holdout_manifest.json'
    protocol, manifest = read(protocol_path), read(manifest_path)
    verify_protocol(ROOT, protocol)
    ids = sorted(manifest['task_ids'])
    if len(ids)!=39 or manifest['protocol_sha256']!=protocol['protocol_sha256']:
        raise ValueError('Expected the existing frozen 39-task manifest')
    if not model.startswith('deepseek-') or not judge_model.startswith('deepseek-'):
        raise ValueError('Only DeepSeek models are configured for this experiment')
    files = ['run_equal_budget_experiment.py','gfi_prototype/research/equal_budget.py',
             'gfi_prototype/research/equal_budget_report.py','gfi_prototype/research/pipeline.py',
             'gfi_prototype/research/client.py','gfi_prototype/research/evidence.py',
             'gfi_prototype/research/retrieval.py','gfi_prototype/retrieval.py',
             'gfi_prototype/analysis.py','gfi_prototype/analysis_v2.py','judge_experiments.py',
             'gfi_prototype/research/evaluation.py']
    config = freeze_configuration(output/'design.json', {
        'version':VERSION, 'model':model, 'judge_model':judge_model,
        'protocol_sha256':protocol['protocol_sha256'], 'holdout_manifest_sha256':sha256_file(manifest_path),
        'task_ids':ids, 'methods':METHODS, 'analysis_calls_per_complete_round':2,
        'max_rounds':3, 'max_calls':6, 'max_output_tokens':5000, 'max_context_chars':60000,
        'reported_token_stop':250000, 'attempts_per_stage':1,
        'schedule':'fresh_D_yoked_round_count_shared_first_round', 'equal_realized_tokens':False,
        'feedback':'R_gets_bounded_previous_analysis_audit; S_uses_same_analysis_prompt_as_D',
        'static_retrieval':'Issue_title_body_only_BM25_top16_continuation_up_to4_new_chunks_per_round',
        'sample_scope':'posthoc_followup_on_previously_evaluated_39_tasks_not_new_untouched_holdout',
        'code_sha256':{file:sha256_file(ROOT/file) for file in files},
        'runtime':{name:importlib.metadata.version(name) for name in ['langgraph','langchain-core','langchain-deepseek']},
        'python_version':f'{sys.version_info.major}.{sys.version_info.minor}',
    })
    local_protocol = output/'protocol.json'
    if local_protocol.exists() and read(local_protocol)!=protocol:
        raise ValueError('Output protocol differs; refusing to overwrite')
    if not local_protocol.exists():
        atomic_json(local_protocol, protocol)
    return protocol, config


def identity(config, task_id, method):
    return sha256_text(json.dumps([config['config_sha256'],task_id,method],ensure_ascii=False))


def result_path(output, task_id, method):
    return output/'results'/task_id.replace('/','__').replace('#','__')/f'{method}.json'


def cached_result(output, config, task_id, method):
    path = result_path(output,task_id,method)
    if not path.exists():
        return None
    value = read(path)
    if (value.get('cache_key')!=identity(config,task_id,method) or value.get('task_id')!=task_id
            or value.get('variant')!=method):
        raise ValueError('Incompatible result cache; refusing to replace it')
    return value if value.get('status')=='complete' and not value.get('errors') else None


def store_result(output, config, task, method, value, session):
    value = {**value,'task_id':task['task_id'],'repo':task['repo'],'issue_number':task['issue_number'],
             'model':config['model'],'experiment_version':VERSION,'config_sha256':config['config_sha256'],
             'protocol_sha256':config['protocol_sha256'], 'cache_key':identity(config,task['task_id'],method),
             'run_id':config['config_sha256'][:16], 'created_at':utc_now(),
             'new_model_calls_this_session':session.new_calls,'stage_cache_hits_this_session':session.cache_hits}
    if value.get('errors'):
        value['raw_pipeline_status'] = value['status']
        value['status'] = 'failed'
    atomic_json(result_path(output,task['task_id'],method),value)
    if value['status']!='complete':
        raise RuntimeError('Condition incomplete; resume from validated stage receipts')
    return value


def run_analysis(output, protocol, config, key, task_ids):
    tasks = {row['task_id']:row for row in protocol['tasks']}
    retrievers = {}
    # The integration is stateless; reusing this client does not share chat history.
    client = DeepSeekClient(key,config['model'],max_output_tokens=5000)
    for index, task_id in enumerate(task_ids,1):
        task = tasks[task_id]
        print(f'\n[{index}/{len(task_ids)}] {task_id}',flush=True)
        retriever = retrievers.get(task['repo'])
        if retriever is None:
            retriever = retrievers[task['repo']] = FixedQueryRetriever(ROOT,task['repo'])
        adaptive = None
        for method in ('D','R','S'):
            cached = cached_result(output,config,task_id,method)
            if cached is not None:
                print(f'  {method} {METHODS[method]}：复用完整结果。',flush=True)
                if method=='D':
                    adaptive = cached
                continue
            print(f'  开始{method}：{METHODS[method]}',flush=True)
            folder = output/'requests'/task_id.replace('/','__').replace('#','__')/method
            session = CachedSession(client,folder,max_calls=6,
                on_progress=lambda text, m=method: print(f'    {m}：{text}',flush=True))
            if method=='D':
                packet = retriever.initial(task['issue_number'])
                value = run_variant(packet,retriever,session,'D',max_rounds=2)
            else:
                if adaptive is None:
                    raise RuntimeError('D must complete before its matched controls')
                value = run_control(adaptive,retriever,session,method)
            saved = store_result(output,config,task,method,value,session)
            if method=='D':
                adaptive = saved
            print(f'  完成{method}：{len(saved["rounds"])}轮，逻辑调用{saved["resources"]["model_calls"]}次，'
                  f'记录Token {saved["resources"]["total_tokens"]}；本次新增调用{session.new_calls}次。',flush=True)
        atomic_json(output/'progress.json', {'completed_task_id':task_id,'selected_tasks':len(task_ids),
                    'updated_at':utc_now(),'config_sha256':config['config_sha256']})


def load_complete(output, config):
    result = []
    for task_id in config['task_ids']:
        group = [cached_result(output,config,task_id,method) for method in ('R','S','D')]
        if all(row is not None for row in group):
            result.extend(group)
    return result


def run_review(output, protocol, config, key):
    import judge_experiments as judge
    from gfi_prototype.research.evaluation import build_review_bundle
    results = load_complete(output,config)
    if len(results)!=39*3:
        raise ValueError('先完成全部39题的R/S/D结果，再统一盲评，避免中途改变共同证据和匿名编号。')
    inventory = sha256_text(json.dumps(results,sort_keys=True,ensure_ascii=False))
    folder = output/'evaluation'
    frozen = freeze_configuration(folder/'inventory.json',{'results_sha256':inventory,
        'judge_model':config['judge_model'],'config_sha256':config['config_sha256']})
    bundle = folder/'blind'
    if not bundle.exists():
        build_review_bundle(ROOT,protocol,results,bundle)
    mapping = {row['prediction_id']:row for row in read(bundle/'private/mapping.json')}
    sources = {row['task_id']:row for row in read(bundle/'assessor/sources.json')}
    originals = {(r['task_id'],r['variant']):r for r in results}
    predictions = [judge.enrich_prediction(row, originals[(row['task_id'],mapping[row['prediction_id']]['variant'])])
                   for row in read(bundle/'assessor/predictions.json')]
    client = judge.ProClient(key,config['judge_model'])
    rows = []
    for index,prediction in enumerate(predictions,1):
        print(f'[盲评 {index}/{len(predictions)}] {prediction["task_id"]} / {prediction["prediction_id"]}',flush=True)
        ids = {row['id'] for row in prediction['evidence']}
        source = {**sources[prediction['task_id']], 'evidence':prediction['evidence']}
        reference = judge.request_cached(folder/'references',client,judge.REFERENCE_PROMPT,source,
                                        lambda value:judge.validate_reference(value,ids))
        payload = normalized_judge_payload(judge.judge_payload(source,reference['result'],prediction))
        # Only the blind prediction identifier is omitted. Identical semantic
        # inputs share a judgment; evidence visibility and all answer fields stay.
        judgment = judge.request_cached(folder/'judgments',client,judge.JUDGE_PROMPT,payload,
                                        lambda value:judge.validate_judgment(value,prediction,ids))
        rows.append({'prediction_id':prediction['prediction_id'],'task_id':prediction['task_id'],
                     'judgment':judgment['result'],'judgment_receipt':judgment['request_sha256']})
        atomic_json(folder/'scores.json',{'scope':'model_assisted_matched_logical_calls_not_equal_tokens',
            'inventory_sha256':frozen['config_sha256'],'rows':rows,'resources':judge.resource_totals(folder),
            'complete':len(rows)==len(predictions)})
    print('主答案盲评完成；不自动执行额外能力门槛专项复核。',flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['prepare','analyze','review','report','all'],default='all')
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--model',default='deepseek-flash')
    parser.add_argument('--judge-model',default='deepseek-v4-pro')
    parser.add_argument('--limit',type=int,help='Only analyze first N frozen tasks; full review still requires all39')
    args = parser.parse_args(argv)
    if args.limit is not None and not 1<=args.limit<=39:
        parser.error('--limit must be 1..39')
    output = safe_output(ROOT,ROOT/args.output)
    protocol,config = prepare(output,args.model,args.judge_model)
    from gfi_prototype.research.equal_budget_report import write_report
    report_path = ROOT/'output/研究改进_第2步_等调用预算对照.md'
    if output != DEFAULT_OUTPUT:
        report_path = ROOT/'output'/f'研究改进_第2步_{output.name}.md'
    write_report(ROOT,output,config,load_complete(output,config),report_path)
    if args.phase in {'prepare','report'}:
        print(f'准备／汇总完成，未调用模型。说明：{report_path}',flush=True)
        return 0
    load_project_env(ROOT)
    key = os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:
        raise RuntimeError('请先在项目根目录.env配置DEEPSEEK_API_KEY。')
    try:
        if args.phase in {'analyze','all'}:
            task_ids = config['task_ids'][:args.limit] if args.limit else config['task_ids']
            run_analysis(output,protocol,config,key,task_ids)
        if args.phase in {'review','all'}:
            if args.phase=='all' and args.limit and len(load_complete(output,config))<117:
                print('限量分析完成；统一盲评等待39题全部完成后启动。',flush=True)
            else:
                run_review(output,protocol,config,key)
    except Exception as exc:
        atomic_json(output/'last_error.json',{'type':type(exc).__name__,'phase':args.phase,
                    'http_status':getattr(exc,'status_code',None),'created_at':utc_now()})
        print(f'执行中断：{type(exc).__name__}；已保存完成节点。再次运行相同命令可续跑。',flush=True)
        write_report(ROOT,output,config,load_complete(output,config),report_path)
        return 1
    write_report(ROOT,output,config,load_complete(output,config),report_path)
    print(f'本阶段完成。说明和当前结果：{report_path}',flush=True)
    print('实际Token与逻辑调用匹配情况已独立列出；不自动进入Top-3或修改PPT。',flush=True)
    return 0


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
