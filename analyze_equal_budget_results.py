"""Offline final analysis of frozen old-v1 complete-case scores. No model calls."""
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean
import zipfile

import run_equal_budget_experiment as base
from gfi_prototype.common import atomic_json, sha256_file, sha256_text, utc_now
from gfi_prototype.research.equal_budget import paid_attempts
from gfi_prototype.research.equal_budget_report import DIMENSIONS, LABELS, table, number
from gfi_prototype.retrieval import TEXT_SUFFIXES

ROOT=Path(__file__).resolve().parent
EXPERIMENT=ROOT/'experiments/frozen_20261017/equal_calls_v1'
STEM=ROOT/'output/研究改进_第2步_结果分析与PPT结论'
METHODS=('R','S','D')


def paired(scores,baseline,tasks,dimension='actionability'):
    deltas=[scores[t]['D'][dimension]-scores[t][baseline][dimension] for t in tasks
            if scores[t]['D'][dimension] is not None and scores[t][baseline][dimension] is not None]
    return {'n':len(deltas),'mean_difference':mean(deltas) if deltas else None,
            'difference_sum':sum(deltas),'wins':sum(d>0 for d in deltas),
            'ties':sum(d==0 for d in deltas),'losses':sum(d<0 for d in deltas),
            'missing_pairs':len(tasks)-len(deltas)}


def select_cases(scores):
    rows=[(t,g['D']['actionability']-g['S']['actionability'],g['D']['actionability']-g['R']['actionability'])
          for t,g in scores.items() if all(g[m]['actionability'] is not None for m in METHODS)]
    wins=sorted((r for r in rows if r[1]>0),key=lambda r:(-r[1],-r[2],r[0]))
    losses=sorted((r for r in rows if r[1]<0),key=lambda r:(r[1],r[2],r[0]))
    return {'win':wins[0][0] if wins else None,'loss':losses[0][0] if losses else None}


def aggregate_tokens(records):
    totals={m:sum(g[m]['resources']['total_tokens'] for g in records.values()) for m in METHODS}
    return {'totals':totals, **{f'D_over_{m}':totals['D']/totals[m] if totals[m] else None for m in ('R','S')}}


def frontend_scope():
    folder=ROOT/'data/ray-project__ray'
    snapshot=base.read(folder/'source_snapshot.json')
    with zipfile.ZipFile(folder/snapshot['archive']) as archive:
        files=[n for n in archive.namelist() if '/dashboard/client/' in n
               and Path(n).suffix in {'.ts','.tsx','.js','.jsx'}]
    return {'commit':snapshot['commit_sha'],'frontend_js_ts_files':len(files),
            'indexed_suffixes':sorted(TEXT_SUFFIXES),
            'frontend_suffixes_excluded':not bool({'.ts','.tsx','.js','.jsx'} & TEXT_SUFFIXES)}


def analyze():
    folder=EXPERIMENT/'evaluation_completed_v1'
    scores_file,cohort_file,mapping_file=folder/'scores.json',folder/'cohort.json',folder/'blind/private/mapping.json'
    saved,cohort,config=base.read(scores_file),base.read(cohort_file),base.read(EXPERIMENT/'design.json')
    if (not saved.get('complete') or saved.get('expected_predictions')!=102 or len(saved['rows'])!=102
            or saved['cohort_sha256']!=cohort['config_sha256']):
        raise ValueError('Expected complete matching 34-task/102-answer evaluation')
    mapping={r['prediction_id']:r for r in base.read(mapping_file)}
    if len(mapping)!=102 or len({r['prediction_id'] for r in saved['rows']})!=102:
        raise ValueError('Duplicate/missing blind identities')
    results=base.load_complete(EXPERIMENT,config)
    if sha256_text(json.dumps(results,sort_keys=True,ensure_ascii=False))!=cohort['results_sha256']:
        raise ValueError('Frozen answers changed')
    scores,judgments=defaultdict(dict),defaultdict(dict)
    for row in saved['rows']:
        identity=mapping[row['prediction_id']]
        if identity['task_id']!=row['task_id']:
            raise ValueError('Score/task mismatch')
        task,method=row['task_id'],identity['variant']
        value=row['judgment']['scores']
        if set(value)!=set(DIMENSIONS) or any(v is not None and (type(v) is not int or not 0<=v<=4) for v in value.values()):
            raise ValueError('Invalid score scale')
        scores[task][method]=value
        judgments[task][method]=row['judgment']
    tasks=sorted(cohort['included_task_ids'])
    if sorted(scores)!=tasks or len(tasks)!=34 or any(set(g)!=set(METHODS) for g in scores.values()):
        raise ValueError('Expected all complete three-way pairs')
    records=defaultdict(dict)
    for row in results:
        records[row['task_id']][row['variant']]=row
    strict=[]
    for task,g in records.items():
        resources=[g[m]['resources'] for m in METHODS]
        if len({r['model_calls'] for r in resources})!=1:
            raise ValueError('Logical calls are not matched')
        cost=paid_attempts(EXPERIMENT/'requests'/task.replace('/','__').replace('#','__'))
        if all(r['usage_complete'] for r in resources) and not cost['failed_calls']:
            strict.append(task)
    one=[t for t in tasks if len(records[t]['D']['rounds'])==1]
    multi=[t for t in tasks if len(records[t]['D']['rounds'])>1]
    if any(any(scores[t][m]!=scores[t]['D'] for m in ('R','S')) for t in one):
        raise ValueError('Identical one-round answer received different grades')
    source_files=[scores_file,cohort_file,mapping_file,EXPERIMENT/'design.json', ROOT/'gfi_prototype/retrieval.py',Path(__file__)]
    source_files += [base.result_path(EXPERIMENT,t,m) for t in tasks for m in METHODS]
    source_files += list((EXPERIMENT/'requests').glob('**/attempts/*.json'))
    source_files += [ROOT/'output/演示PPT_13页叙述优化版.pptx']
    hashes={str(p.relative_to(ROOT)):sha256_file(p) for p in source_files}
    averages={m:{dim:mean([scores[t][m][dim] for t in tasks if scores[t][m][dim] is not None])
                if any(scores[t][m][dim] is not None for t in tasks) else None for dim in DIMENSIONS} for m in METHODS}
    subsets={'all':tasks,'no_failed_attempts':sorted(strict),'one_round':one,'additional_rounds':multi}
    comparisons={name:{m:paired(scores,m,ids) for m in ('R','S')} for name,ids in subsets.items()}
    projects={repo:{'n':len(ids), **{m:paired(scores,m,ids) for m in ('R','S')}}
              for repo in sorted({t.rsplit('#',1)[0] for t in tasks})
              for ids in [[t for t in tasks if t.rsplit('#',1)[0]==repo]]}
    cases=select_cases(scores)
    example_data={}
    for role,task in cases.items():
        if task is None:
            continue
        example_data[role]={'task_id':task,'scores':scores[task],
            'judge_summaries':{m:judgments[task][m]['summary'] for m in METHODS},
            'methods':{m:{'rounds':len(records[task][m]['rounds']),
                'stop_reason':records[task][m]['stop_reason'],
                'retained_technologies':[r['name'] for r in records[task][m]['gated']['requirements']],
                'acceptance':records[task][m]['gated']['acceptance'],
                'withheld':records[task][m]['gated']['withheld_claim_ids']} for m in METHODS},
            'D_trace':[{'round':r['round'],'retrieval':r['retrieval'],
                        'evidence':[{k:e.get(k) for k in ('id','path','start_line','end_line')}
                                    for e in r['packet']['evidence'] if e.get('path')]}
                        for r in records[task]['D']['rounds']]}
    payload={'created_at':utc_now(),'scope':cohort['scope'],'planned_tasks':39,'complete_tasks':34,
        'answer_count':102,'excluded_tasks':cohort['excluded_tasks'], 'averages':averages,
        'comparisons':comparisons,'subsets':subsets,'project_comparisons':projects,
        'tokens':aggregate_tokens(records),'actual_generation_attempts':paid_attempts(EXPERIMENT/'requests'),
        'review_resources':saved['resources'],'round_distribution':dict(Counter(len(records[t]['D']['rounds']) for t in tasks)),
        'case_rule':'strongest_D_minus_S; tie_D_minus_R_then_task_id; posthoc_illustrative_not_independent_validation',
        'cases':example_data,'frontend_scope':frontend_scope(),
        'per_task':{t:{'scores':scores[t],'rounds':len(records[t]['D']['rounds']),
                      'tokens':{m:records[t][m]['resources']['total_tokens'] for m in METHODS}} for t in tasks},
        'source_sha256':hashes}
    return payload


def write_markdown(data,path):
    a=data['averages']; comparisons=data['comparisons']; tok=data['tokens']
    lines=['# 第2步结果分析与PPT结论','',
        '## 1. 最直白的结论','',
        '**在34道完整任务上，D的主答案可操作性均分最高；相比固定证据反复修订更有优势，相比固定查询扩展只有小幅优势。**',
        'D并非全面领先：完整性和不确定性表达与R持平，多数任务的可操作性也与对照持平。'
        '这一结果为补检提供方向性支持，但不能声称自适应策略显著或普遍优越。','',
        '## 2. 全部评分与资源','',
        table(['方法',*LABELS],[[m,*[number(a[m][k]) for k in DIMENSIONS]] for m in METHODS]),'',
        table(['对比','可操作性均差','胜／平／负','D已记录Token增加'],[
            [f'D−{m}',number(comparisons['all'][m]['mean_difference']),
             '/'.join(str(comparisons['all'][m][k]) for k in ('wins','ties','losses')),
             f'{(tok["D_over_"+m]-1)*100:.1f}%'] for m in ('R','S')]),'',
        '上述均差先按题计算再平均，不用已四舍五入的组均分相减。Token比例是34题按方法归属用量总和之比，不是逐题比值的平均。',
        table(['方法','34题已记录Token'],[[m,f'{tok["totals"][m]:,}'] for m in METHODS]),'',
        '各方法都计入共同首轮的逻辑资源；将三组Token相加不代表真实总账单。实际成本回执包括失败和续跑。'
        '评审用量不完整，不能把已记录评审Token当成完整消耗或精确费用。',
        f'全部生成实际请求回执：{data["actual_generation_attempts"]["model_calls"]}次；'
        f'已记录Token {data["actual_generation_attempts"]["recorded_tokens"]:,}；'
        f'失败请求{data["actual_generation_attempts"]["failed_calls"]}次；用量完整={data["actual_generation_attempts"]["usage_complete"]}。',
        f'评审已记录请求{data["review_resources"]["model_calls"]}次；'
        f'Token {data["review_resources"]["total_tokens"]:,}；用量完整={data["review_resources"]["usage_complete"]}。','',
        '## 3. 稳健性、停止行为和项目差异','',
        table(['样本范围','题数','D−R可操作性','D−S可操作性'],[
            [label,len(data['subsets'][key]),*[number(comparisons[key][m]['mean_difference']) for m in ('R','S')]]
            for key,label in [('all','全部完整任务'),('no_failed_attempts','无失败且用量完整'),
                              ('one_round','共同首轮即停止'),('additional_rounds','发生后续轮次')]]),'',
        '30题无失败子集中差值方向仍为正，但D−S从+0.12缩小至+0.07。它仍是调用匹配子集，不是严格等Token实验。',
        '12题首轮停止，三组共享同一答案且评分相同；另外22题发生后续轮次。22题分析仅作探索性解释，'
        '其划分由D停止行为决定，不能取代全部34题报告，也不能因分数更高而只报告该子集。','',
        table(['项目','题数','D−R可操作性','D−S可操作性'],[
            [repo,g['n'],number(g['R']['mean_difference']),number(g['S']['mean_difference'])]
            for repo,g in data['project_comparisons'].items()]),'',
        '**D−S的净增分全部来自Manim：该项目7题合计净增4分，其余四项目净差均为0。**'
        '其余项目净差为0不代表每题都持平；有的任务增分与失分相互抵消。因此不能声称跨项目稳定优于固定查询。','',
        '## 4. 正例：Manim #1369','',
        '任务是移除弃用的TexMobject/TextMobject兼容层。该例按D−S最大正差选择，若并列再比较D−R，最后按任务ID；'
        '这是看过评分后的解释性选例，不是独立验证。','',
        '- **评分事实：**R=2、S=2、D=4，三组各3轮、6次逻辑调用。',
        '- **过程事实：**D首轮核查认为替代类回归验证依据不足，生成包含Tex/MathTex与测试路径的查询；'
        '补检取得`tests/module/mobject/text/test_texmobject.py`（E9/E10）和`tests/opengl/test_texmobject_opengl.py`（E11/E12）。',
        '- **输出事实：**D最终保留了TeX替代类体系及相关回归建议；S最终技术要求为空，R仅保留一条门控后验收项。',
        '- **评审意见：**D的知识要求和验收建议更具体；S技术要求缺失，R验证覆盖不足。该意见来自模型，不是人工判定。',
        '- **解释：**本例与“缺口补检取得专项测试材料，使验证建议更具体”的机制一致，但没有隔离出单条证据的因果贡献。',
        '- **限制：**D仍未定位旧类的确切定义位置，保留多项未知，因补检轮数上限停止；未真实运行测试。'
        '4分不代表任务已解决，也不代表输出毫无遗漏。','',
        '## 5. 反例：Ray #49011','',
        '任务是让Dashboard展示数据最后加载时间，并可能提供手动刷新。按D−S最大负差选择，若并列优先D−R更低的任务，再按任务ID。','',
        '- **评分事实：**R=2、S=2、D=1，三组各3轮、6次逻辑调用。',
        '- **过程事实：**D两次补检后仍缺前端数据获取和展示代码，检索结果混有后端测试、Job接口与不相关数据集代码。',
        '- **输出事实：**D暂缓了能力阶段判断，保留的验收项没有单独列出可选手动刷新；R/S将它保留为非强制期望。',
        '- **评审意见：**D的主答案较薄，缺少前端实现位置、刷新机制和视图范围等关键内容。',
        '- **代码核对发现：**当前索引后缀只含`.py/.md/.rst/.toml/.yaml/.yml/.ini/.txt`，未包含`.js/.jsx/.ts/.tsx`。'
        f'固定Ray快照的`dashboard/client/`路径下有{data["frontend_scope"]["frontend_js_ts_files"]}个这些类型的文件；'
        '该数量按ZIP路径与扩展名统计，包含public目录脚本，不等同于214个业务源文件。',
        '- **解释：**检索范围限制使缺口查询无法取到这些前端文件，能解释为何反复补检仍无前端证据；'
        '但不能据此断言全部失分都由这一个限制造成。',
        '- **处理：**如实报告为适用范围限制。本轮不扩展索引、不改规则、不重跑；跨语言源码覆盖作为后续改进方向。','',
        '## 6. 原Ray #3269流程案例怎样使用','',
        '现有PPT案例Ray #3269的可操作性为R=2、S=3、D=3。D与S持平，所以继续将它用于说明'
        '“核查—补检—更新建议”的流程，而不是展示D优于固定查询的证据。保留代码中文解释；'
        '实际配置已启用viewcode，但未验证全部API覆盖范围。','',
        '## 7. 可直接放入PPT的结论','',
        '- **相同调用轮数下，D的主答案可操作性均分最高：2.68，对照R为2.44、S为2.56。**',
        '- **D相对R提升0.24分，相对S提升0.12分；按方法归属的已记录Token分别增加约6.9%和2.8%。**',
        '- **30题无失败子集仍为正向差异，但相对S仅提升0.07分，说明优势有限。**',
        '- **相对S的净增分集中于Manim，其余四项目净差均为0，跨项目优势尚不充分。**',
        '- **补检效果取决于是否取得关键材料，索引覆盖不到所需文件时，多次调用也无法补齐证据。**','',
        '推荐的一句话：**在本样本与模型评分下，缺口驱动补检相较固定证据修订更有优势，相较固定查询扩展收益较小，且依赖检索覆盖。**','',
        '不写“显著领先”“全面优于”“成本完全相同”“5题由R/S失败”“已证明适用于全部科研软件”。'
        '没有开展统计显著性检验或独立人工核验；同系列模型评分可能存在共同偏差。','',
        '## 8. 失败、样本与创新表达边界','',
        '39题中34题形成完整三组，完成率87.2%；5题在D阶段中断，R/S未启动。'
        '完整样本可能存在选择偏差，不能当作全部39题的质量结果。所有任务是此前分析过的任务，不是新的未见测试集。',
        '可以将方法贡献表述为“构建面向GFI的证据核查、结论门控与缺口补检闭环，并通过模块消融和调用匹配对照分析收益与限制”。'
        '相关工作比较尚需另行核对；本次实验不能证明该机制为首次提出。','',
        '## 9. 全部逐题可操作性分数','',
        table(['任务','R','S','D','D−R','D−S','轮数'],[
            [task,*[g['scores'][m]['actionability'] for m in METHODS],
             g['scores']['D']['actionability']-g['scores']['R']['actionability'],
             g['scores']['D']['actionability']-g['scores']['S']['actionability'],g['rounds']]
             for task,g in data['per_task'].items()]),'',
        '## 10. 追溯与复现','',
        '离线运行 `analyze_equal_budget_results.py` 即可重新生成本文件及同名JSON，不调用模型。'
        'JSON保存原始均值、子集、配对差值、案例摘要和源文件SHA-256。',
        '主要来源：旧版`evaluation_completed_v1/scores.json`、`cohort.json`、匿名映射和各组完整结果。'
        '案例分析同时核对逐轮检索与门控；检索覆盖结论核对`gfi_prototype/retrieval.py`和固定Ray源码ZIP。','']
    path.write_text('\n'.join(lines),encoding='utf-8')


def main():
    data=analyze()
    for file,digest in data['source_sha256'].items():
        if sha256_file(ROOT/file)!=digest:
            raise ValueError('Source changed during analysis')
    atomic_json(STEM.with_suffix('.json'),data)
    write_markdown(data,STEM.with_suffix('.md'))
    print(f'离线分析完成：{STEM.with_suffix(".md")}')
    print('输入保持只读；无模型请求。')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
