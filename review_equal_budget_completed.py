"""PyCharm entry: review complete old-v1 triples, disclose incomplete tasks.

Never generates/revises R/S/D answers. --prepare makes no paid requests.
Default reviews only the frozen complete-case cohort with the original rubric.
"""
import argparse
import json
import os
from pathlib import Path
from statistics import mean
import sys

import run_equal_budget_experiment as base
from gfi_prototype.common import atomic_json, sha256_file, sha256_text, utc_now
from gfi_prototype.research.equal_budget import normalized_judge_payload, paid_attempts, METHODS
from gfi_prototype.research.equal_budget_report import DIMENSIONS, LABELS, table, number
from gfi_prototype.research.evaluation import build_review_bundle
from gfi_prototype.settings import load_project_env

REVIEW_FOLDER = 'evaluation_completed_v1'
REPORT = base.ROOT/'output/研究改进_第2步_旧版完整样本评审.md'


def prepare_cohort(output, config):
    results = base.load_complete(output,config)
    included = sorted({row['task_id'] for row in results})
    excluded=[]
    for task in config['task_ids']:
        if task in included:
            continue
        methods={}
        for method in METHODS:
            path=base.result_path(output,task,method)
            if not path.exists():
                methods[method]={'status':'not_generated','errors':[]}
            else:
                row=base.read(path)
                methods[method]={'status':row.get('status'),'errors':row.get('errors',[])}
        excluded.append({'task_id':task,'methods':methods})
    if not included:
        raise ValueError('没有完整的R/S/D三组答案可评审。')
    digest=sha256_text(json.dumps(results,sort_keys=True,ensure_ascii=False))
    cohort=base.freeze_configuration(output/REVIEW_FOLDER/'cohort.json',{
        'scope':'posthoc_complete_case_analysis_not_all_planned_tasks',
        'base_config_sha256':config['config_sha256'], 'results_sha256':digest,
        'planned_tasks':len(config['task_ids']), 'included_task_ids':included,
        'excluded_tasks':excluded,'completion_rate':len(included)/len(config['task_ids']),
        'judge_model':config['judge_model'],'review_code_sha256':sha256_file(Path(__file__)),
        'cohort_rule':'all_existing_clean_complete_R_S_D_triples_without_score_selection',
        'missing_score_policy':'not_imputed_not_assigned_zero; report_completion_and_selection_bias'})
    return results,cohort


def run_review(output, protocol, config, results, cohort, key):
    import judge_experiments as judge
    folder=output/REVIEW_FOLDER
    bundle=folder/'blind'
    if not bundle.exists():
        build_review_bundle(base.ROOT,protocol,results,bundle)
    mapping={row['prediction_id']:row for row in base.read(bundle/'private/mapping.json')}
    sources={row['task_id']:row for row in base.read(bundle/'assessor/sources.json')}
    originals={(row['task_id'],row['variant']):row for row in results}
    predictions=[judge.enrich_prediction(row,originals[(row['task_id'],mapping[row['prediction_id']]['variant'])])
                 for row in base.read(bundle/'assessor/predictions.json')]
    expected={(row['task_id'],row['variant']) for row in results}
    actual={(row['task_id'],row['variant']) for row in mapping.values()}
    if actual!=expected or len(predictions)!=len(results):
        raise ValueError('Blind review cohort mismatch')
    client=judge.ProClient(key,config['judge_model'])
    rows=[]
    for index,prediction in enumerate(predictions,1):
        print(f'[盲评 {index}/{len(predictions)}] {prediction["task_id"]} / {prediction["prediction_id"]}',flush=True)
        ids={row['id'] for row in prediction['evidence']}
        source={**sources[prediction['task_id']], 'evidence':prediction['evidence']}
        reference=judge.request_cached(folder/'references',client,judge.REFERENCE_PROMPT,source,
                                      lambda value:judge.validate_reference(value,ids))
        payload=normalized_judge_payload(judge.judge_payload(source,reference['result'],prediction))
        judgment=judge.request_cached(folder/'judgments',client,judge.JUDGE_PROMPT,payload,
                                     lambda value:judge.validate_judgment(value,prediction,ids))
        rows.append({'prediction_id':prediction['prediction_id'],'task_id':prediction['task_id'],
                     'judgment':judgment['result'],'judgment_receipt':judgment['request_sha256']})
        atomic_json(folder/'scores.json',{'scope':cohort['scope'],'cohort_sha256':cohort['config_sha256'],
            'rows':rows,'resources':judge.resource_totals(folder),'complete':len(rows)==len(predictions),
            'expected_predictions':len(predictions),'planned_tasks':cohort['planned_tasks'],
            'included_tasks':len(cohort['included_task_ids'])})


def write_report(output, results, cohort, path):
    folder=output/REVIEW_FOLDER
    score_path=folder/'scores.json'
    scores=base.read(score_path) if score_path.exists() else {'rows':[],'complete':False}
    if scores.get('cohort_sha256') not in (None,cohort['config_sha256']):
        raise ValueError('Score/cohort identity mismatch')
    mapping_path=folder/'blind/private/mapping.json'
    mapping={r['prediction_id']:r for r in base.read(mapping_path)} if mapping_path.exists() else {}
    groups={}
    for row in results:
        groups.setdefault(row['task_id'],{})[row['variant']]=row
    judged={}
    for row in scores['rows']:
        identity=mapping[row['prediction_id']]
        judged.setdefault(row['task_id'],{})[identity['variant']]=row['judgment']['scores']
    complete={task:g for task,g in judged.items() if set(g)==set(METHODS)}
    strict=[]
    for task,g in groups.items():
        cost=paid_attempts(output/'requests'/task.replace('/','__').replace('#','__'))
        if (len({row['resources']['model_calls'] for row in g.values()})==1
                and all(row['resources']['usage_complete'] for row in g.values())
                and not cost['failed_calls']):
            strict.append(task)
    lines=['# 第2步：旧版完整样本评审与失败披露','',
        f'原计划{cohort["planned_tasks"]}题；完成三组分析{len(groups)}/{cohort["planned_tasks"]}题'
        f'（{cohort["completion_rate"]:.1%}）；未完成{len(cohort["excluded_tasks"])}题。',
        f'已完成三组评分：{len(complete)}/{len(groups)}题。','',
        '## 1. 这份报告能说明什么','',
        '本报告只比较旧版中完整生成R/S/D答案的任务。样本按三组完成状态纳入，不按分数挑题。'
        '失败可能与任务难度、引用复杂度有关，因此完整样本存在选择偏差；不能推广为原计划全部任务的有效性结论。',
        '未生成的R/S对照不等于R/S方法失败；部分任务因D先失败而未启动对照。不能据此比较三种方法的失败率。',
        '缺失评分不填零。完成率与完整样本质量分开呈现。旧版输出、提示词、验证器均不修改。',
        '三组匹配逻辑调用次数，并非严格等Token；失败及重跑的实际消耗单独保留。', '',
        '## 2. 三组结果','']
    if not complete:
        lines+=['尚未完成评分，不预设D组更优。','']
    else:
        def average(method,key):
            values=[g[method][key] for g in complete.values() if g[method][key] is not None]
            return number(mean(values)) if values else '—'
        lines += [table(['方法','配对题数',*LABELS],[[m,len(complete),*[average(m,k) for k in DIMENSIONS]]
                  for m in METHODS]),'']
        for baseline in ('R','S'):
            for label,tasks in [('全部已评分完整样本',list(complete)),
                                ('无失败且用量完整的调用匹配子集',[t for t in strict if t in complete])]:
                deltas=[complete[t]['D']['actionability']-complete[t][baseline]['actionability'] for t in tasks
                        if complete[t]['D']['actionability'] is not None and complete[t][baseline]['actionability'] is not None]
                lines += [f'- D−{baseline}，{label}：n={len(deltas)}，可操作性均差'
                    f'{number(mean(deltas)) if deltas else "—"}；胜／平／负'
                    f'={sum(d>0 for d in deltas)}/{sum(d==0 for d in deltas)}/{sum(d<0 for d in deltas)}。']
        lines+=['','以上为模型辅助评分，不能表述为人工验证准确率。'
                +('完整样本评分已结束，结论范围仍限于完整样本。' if scores['complete'] else '评分尚未完成，仅为进度汇总。'),'']
    lines += ['## 3. 未完成任务（不得隐去）','',table(['任务','R','S','D'],[
        [row['task_id'],*[row['methods'][m]['status'] for m in METHODS]] for row in cohort['excluded_tasks']]),'',
        '`not_generated`表示未生成；`failed`表示该条件中断，具体校验原因如下。','']
    for row in cohort['excluded_tasks']:
        lines += [f'### {row["task_id"]}','']
        for method in METHODS:
            diagnostics=output/'requests'/row['task_id'].replace('/','__').replace('#','__')/method/'diagnostics'
            reasons=set()
            for p in diagnostics.glob('*.json'):
                reasons.update(detail.get('message',detail.get('type','unknown')) for detail in base.read(p).get('detail',[]))
            if reasons:
                lines += [f'- {method}：'+'；'.join(sorted(reasons))]
        lines+=['']
    cost=paid_attempts(output/'requests')
    lines += ['## 4. 调用与Token','',table(['任务','R/S/D调用','R/S/D已记录Token','无失败严格匹配'],[
        [task,'/'.join(str(g[m]['resources']['model_calls']) for m in METHODS),
         '/'.join(str(g[m]['resources']['total_tokens']) for m in METHODS),'是' if task in strict else '否']
        for task,g in sorted(groups.items())]),'',
        f'旧版全部实际生成请求（含未完成题和失败尝试）：{cost["model_calls"]}次；'
        f'已记录Token {cost["recorded_tokens"]}；失败请求{cost["failed_calls"]}次；用量完整={cost["usage_complete"]}。',
        f'评审成本另见 `{REVIEW_FOLDER}/scores.json`，不计作生成阶段的匹配预算。','',
        '## 5. 运行与文件','',
        '运行 `review_equal_budget_completed.py`：仅评审现有完整答案，不重新生成答案。中断后再次运行会复用评审缓存。',
        '`--prepare`仅冻结名单和生成待评审报告，不调用模型；`--report`仅更新报告。',
        f'名单与答案指纹：`{REVIEW_FOLDER}/cohort.json`。开始评审后若原答案或纳入名单改变，将拒绝混用缓存。',
        '下一步：用户运行评审后再解释差异，不提前宣称D组有效。','']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines),encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--report',action='store_true')
    args=parser.parse_args(argv)
    output=base.DEFAULT_OUTPUT
    frozen=base.read(output/'design.json')
    protocol,config=base.prepare(output,frozen['model'],frozen['judge_model'])
    results,cohort=prepare_cohort(output,config)
    write_report(output,results,cohort,REPORT)
    print(f'旧版完整样本：{len(cohort["included_task_ids"])}/{cohort["planned_tasks"]}题；'
          f'待评审{len(results)}份答案；未完成任务单独披露。',flush=True)
    if args.prepare or args.report:
        print(f'报告：{REPORT}；未调用模型。',flush=True)
        return 0
    load_project_env(base.ROOT)
    key=os.environ.get('DEEPSEEK_API_KEY','').strip()
    if not key:
        raise RuntimeError('请在.env中配置DEEPSEEK_API_KEY。')
    try:
        run_review(output,protocol,config,results,cohort,key)
    except Exception as exc:
        atomic_json(output/REVIEW_FOLDER/'last_error.json',{'type':type(exc).__name__,
                    'http_status':getattr(exc,'status_code',None),'created_at':utc_now()})
        write_report(output,results,cohort,REPORT)
        print(f'评审暂停：{type(exc).__name__}。重新运行本脚本恢复；不会重新生成答案。',flush=True)
        return 1
    write_report(output,results,cohort,REPORT)
    print(f'完整样本评审完成：{REPORT}',flush=True)
    return 0


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
