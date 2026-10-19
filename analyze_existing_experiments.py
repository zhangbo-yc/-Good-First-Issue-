"""Read-only analysis of the saved 39-task experiment. No model or network calls."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent
DIMENSIONS = ('correctness', 'completeness', 'actionability', 'calibration', 'guidance_actionability')
MAIN = DIMENSIONS[:4]
LABELS = {'correctness': '正确性', 'completeness': '完整性', 'actionability': '主答案可操作性',
          'calibration': '不确定性表达', 'guidance_actionability': '原始配套指导性'}
TYPES = {'needs_review': '待复核', 'testing': '测试', 'maintenance': '维护', 'bugfix': '缺陷修复',
         'refactor': '重构', 'feature': '功能', 'documentation': '文档', 'performance': '性能'}
PAIRS = [('B', 'A'), ('C', 'B'), ('D', 'C'), ('D', 'B')]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_records(records):
    if not records:
        raise ValueError('No experiment records')
    identities = [row['task_id'] for row in records]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate task IDs')
    for row in records:
        if set(row['scores']) != set('ABCD'):
            raise ValueError('Each task must contain A/B/C/D')
        for scores in row['scores'].values():
            if set(scores) != set(DIMENSIONS):
                raise ValueError('Unexpected score dimensions')
            for value in scores.values():
                if value is not None and (type(value) is not int or not 0 <= value <= 4):
                    raise ValueError('Scores must be integers 0..4 or null')


def group_means(records):
    result = {}
    for variant in 'ABCD':
        result[variant] = {}
        for dimension in DIMENSIONS:
            values = [row['scores'][variant][dimension] for row in records
                      if row['scores'][variant][dimension] is not None]
            result[variant][dimension] = {'n': len(values), 'mean': mean(values) if values else None}
    return result


def compare_conditions(records, candidate, baseline, dimension):
    pairs = [(row['task_id'], row['scores'][candidate][dimension] - row['scores'][baseline][dimension])
             for row in records if row['scores'][candidate][dimension] is not None
             and row['scores'][baseline][dimension] is not None]
    return {'n': len(pairs), 'mean_delta': mean(delta for _, delta in pairs) if pairs else None,
            'wins': sum(delta > 0 for _, delta in pairs), 'ties': sum(delta == 0 for _, delta in pairs),
            'losses': sum(delta < 0 for _, delta in pairs),
            'win_task_ids': sorted(task for task, delta in pairs if delta > 0),
            'loss_task_ids': sorted(task for task, delta in pairs if delta < 0)}


def analyze_records(records):
    validate_records(records)
    result = {'n_tasks': len(records), 'overall': group_means(records),
              'comparisons': {f'{a}-{b}': {key: compare_conditions(records, a, b, key) for key in DIMENSIONS}
                              for a, b in PAIRS}}
    for name, field in [('by_project', 'repo'), ('by_type', 'stratum')]:
        groups = defaultdict(list)
        for row in records:
            groups[row[field]].append(row)
        result[name] = {key: {'n': len(values), 'means': group_means(values),
                             'd_minus_b': {dim: compare_conditions(values, 'D', 'B', dim) for dim in DIMENSIONS}}
                        for key, values in sorted(groups.items())}
    result['decline_task_ids'] = result['comparisons']['D-B']['actionability']['loss_task_ids']
    return result


def load_dataset(root, experiment, judge):
    protocol_path = experiment.parent / 'protocol.json'
    manifest_path = experiment / 'holdout_manifest.json'
    evaluation_path = judge / 'model_evaluation.json'
    mapping_path = judge / 'blind/private/mapping.json'
    inputs = [protocol_path, manifest_path, evaluation_path, mapping_path]
    protocol, manifest, evaluation, mapping_rows = map(read, inputs)
    if manifest['protocol_sha256'] != protocol['protocol_sha256']:
        raise ValueError('Holdout and frozen protocol identities differ')
    expected = set(manifest['task_ids'])
    if len(expected) != 39 or manifest['holdout_count'] != 39:
        raise ValueError('Expected the frozen 39-task holdout')
    tasks = {row['task_id']: row for row in protocol['tasks']}
    mapping = {}
    for row in mapping_rows:
        if row['prediction_id'] in mapping:
            raise ValueError('Duplicate blinded prediction mapping')
        mapping[row['prediction_id']] = row
    records = {task: {'task_id': task, 'repo': tasks[task]['repo'], 'title': tasks[task]['title'],
                     'stratum': tasks[task]['stratum'], 'scores': {}, 'judgments': {},
                     'prediction_ids': {}, 'results': {}} for task in sorted(expected)}
    seen = set()
    for row in evaluation['rows']:
        identity = mapping[row['prediction_id']]
        task, variant = row['task_id'], identity['variant']
        if task != identity['task_id'] or task not in expected or variant not in 'ABCD':
            raise ValueError('Evaluation identity does not match held-out mapping')
        if (task, variant) in seen:
            raise ValueError('Duplicate task/condition judgment')
        seen.add((task, variant))
        record = records[task]
        record['scores'][variant] = row['judgment']['scores']
        record['judgments'][variant] = row['judgment']
        record['prediction_ids'][variant] = row['prediction_id']
        result_path = experiment / 'results' / task.replace('/', '__').replace('#', '__') / f'{variant}.json'
        inputs.append(result_path)
        saved = read(result_path)
        if (saved.get('task_id') != task or saved.get('variant') != variant or saved.get('status') != 'complete'
                or saved.get('protocol_sha256') != protocol['protocol_sha256']
                or saved['packet']['source_commit'] != tasks[task]['source_commit']):
            raise ValueError(f'Incompatible saved answer: {task}/{variant}')
        record['results'][variant] = saved
    if seen != {(task, variant) for task in expected for variant in 'ABCD'}:
        raise ValueError('The 39 x 4 judgment set is incomplete')
    versions = {saved.get('pipeline_version') for row in records.values() for saved in row['results'].values()}
    if versions != {'evidence_adaptive_v2'}:
        raise ValueError('This report is for historical Python-workflow answers; do not mix migrated outputs')
    values = list(records.values())
    validate_records(values)
    return values, {'paths': inputs, 'judge_model': evaluation['requested_model'],
                    'judge_version': evaluation['judge_version'], 'protocol_sha256': protocol['protocol_sha256'],
                    'pipeline_version': 'evidence_adaptive_v2'}


def mechanism_statistics(records):
    by_round = defaultdict(list)
    error_cases = []
    for row in records:
        saved = row['results']['D']
        by_round[len(saved['rounds'])].append(row)
        if saved.get('errors'):
            error_cases.append({'task_id': row['task_id'], 'stop_reason': saved['stop_reason'],
                                'errors': saved['errors'], 'complete_rounds': len(saved['rounds'])})
    error_ids = {row['task_id'] for row in error_cases}
    without_errors = [row for row in records if row['task_id'] not in error_ids]
    return {'by_complete_rounds': {str(n): {'n': len(rows),
                'actionability': compare_conditions(rows, 'D', 'B', 'actionability'),
                'median_request_seconds': median(r['results']['D']['resources']['elapsed_seconds'] for r in rows)}
                for n, rows in sorted(by_round.items())},
            'stop_reasons': dict(Counter(row['results']['D']['stop_reason'] for row in records)),
            'execution_error_cases': error_cases,
            'sensitivity_without_execution_errors': {
                key: compare_conditions(without_errors, 'D', 'B', key) for key in MAIN},
            'costs': {variant: {'model_calls': sum(r['results'][variant]['resources']['model_calls'] for r in records),
                'recorded_tokens': sum(r['results'][variant]['resources']['total_tokens'] for r in records),
                'usage_complete_count': sum(bool(r['results'][variant]['resources']['usage_complete']) for r in records),
                'median_request_seconds': median(r['results'][variant]['resources']['elapsed_seconds'] for r in records)}
                for variant in 'ABCD'}}


CASE_NOTES = {
    'ManimCommunity/manim#1172': ('文档入口与条件性要求',
        '补检后所有当前核心结论获支持，但外部评审仍认为文档迁移入口不够具体，且把manim指令作为无条件要求略强。',
        '内部核查“获支持”不保证外部评分最高；应区分任务必要要求与仅在添加示例时需要的工具。'),
    'dask/dask#4278': ('核心答案与配套指导的评分边界',
        'D仅完成初轮，未补检、未暂缓结论；评审主要指出缺少全仓搜索旧marker用法的操作流程。B/D原始指导均为4分。',
        '此例不能归因为补检或门控删减；分别生成的答案和可操作性评分对步骤呈现的敏感性都可能影响差异。'),
    'ray-project/ray#19155': ('兼容迁移条件仍缺证据',
        '两次补检达到上限；最终暂缓5项核心判断，含3项验收。评审指出旧参数废弃、默认行为等验收覆盖不足。',
        '局部片段可能不足以支撑跨调用点的迁移要求；到达轮次上限应保留未解决清单，不把完整轮等同完整覆盖。'),
    'sympy/sympy#2806': ('已有文档状态与关系利用不足',
        '两次补检后仍暂缓2项；评审指出已有redirects与交叉引用关系利用不充分，也指出缺少构建命令。D原始指导为4分。',
        '不仅要补到相关文件，还要解释现有页面关系；评审对命令步骤的要求也可能反映核心答案与配套指导边界。'),
    'sympy/sympy#5090': ('测试要求未进入最终核心答案',
        '两次补检后，原分析中的pytest技术项和一项测试验收仍被门控暂缓；外部评审同时指出异常测试要求缺失。',
        '这里存在“原始分析有测试要求、最终核心答案未保留”的具体轨迹，提示测试用途与证据绑定需要重点检查。'),
    'sympy/sympy#6698': ('评分对具体步骤呈现敏感',
        'D仅初轮、没有补检或暂缓；正确性与完整性均与B相同为4分，但可操作性4降3。B/D原始指导都为4分。',
        '模型摘要称核心点完整但缺具体操作步骤；该下降不能作为“补检失败”的证据，也不应仅凭此认定方法语义错误。'),
}


def fmt(value, signed=False):
    if value is None:
        return '—'
    rounded = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f'{rounded:+.2f}' if signed else f'{rounded:.2f}'


def safe(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', '')


def md_table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(safe(v) for v in row) + ' |' for row in rows]])


def file_link(root, path):
    resolved = (root / path).resolve() if not Path(path).is_absolute() else Path(path)
    return f'[{safe(resolved.name)}](<{resolved.as_posix()}>)'


def render_report(root, experiment, judge, records, summary, metadata):
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M（北京时间）')
    by_id = {row['task_id']: row for row in records}
    mech = summary['mechanism']
    db = summary['comparisons']['D-B']
    losses = len(summary['decline_task_ids'])
    ray = summary['by_project'].get('ray-project/ray')
    sympy = summary['by_project'].get('sympy/sympy')
    types = summary['by_type']
    total_action_gain = sum(r['scores']['D']['actionability']-r['scores']['B']['actionability'] for r in records)
    ray_action_gain = sum(r['scores']['D']['actionability']-r['scores']['B']['actionability']
                          for r in records if r['repo']=='ray-project/ray')
    lines = ['# 研究改进第1步：已有39题实验结果细分与失败分析', '',
        f'生成时间：{now}', '', '**本步状态：完成。对应执行顺序2→1→3→5中的第一项，即原Top-2。**', '',
        '本步只读取已保存答案、冻结样本与已有评分，未调用大模型、未重新评分、未修改历史实验文件。'
        '本步完成后暂停，不自动开展等预算实验、改写创新结论或接入前端。', '',
        '## 1. 这一步做了什么', '',
        '1. 核对39个独立任务、每题A/B/C/D四份答案与156条评分的身份对应。',
        '2. 按项目、冻结任务类型及D组完整分析轮数细分结果。',
        '3. 以同题D−B的主答案可操作性为主要观察量，同时保留D−C及其他指标。',
        f'4. 对{losses}个可操作性下降任务逐项对照既有模型评审与D组执行轨迹，区分缺证、门控暂缓和评分边界。',
        '5. 输出逐题得分与差值、原始轨迹定位和可直接用于PPT的结论。', '',
        '### 本步主要发现（PPT可直接摘用）', '',
        f'- **收益分布不均匀。** Ray样本的可操作性D−B为{fmt(ray["d_minus_b"]["actionability"]["mean_delta"],True)}分，'
        f'7胜／1平／1负；SymPy为{fmt(sympy["d_minus_b"]["actionability"]["mean_delta"],True)}分，3胜／3平／3负。',
        f'- **总体增分有集中来源。** Ray贡献可操作性净增{ray_action_gain}分，占39题总净增{total_action_gain}分的'
        f'{ray_action_gain/total_action_gain:.0%}；不代表每个项目同等获益。',
        f'- **类型层面有正负边界。** 测试类{fmt(types["testing"]["d_minus_b"]["actionability"]["mean_delta"],True)}分（4题），'
        f'功能类{fmt(types["feature"]["d_minus_b"]["actionability"]["mean_delta"],True)}分（12题）；'
        f'文档类{fmt(types["documentation"]["d_minus_b"]["actionability"]["mean_delta"],True)}分（7题），'
        f'重构类{fmt(types["refactor"]["d_minus_b"]["actionability"]["mean_delta"],True)}分（3题）。类型粗略、样本小，仅作当前观察。',
        '- **下降不等于补检失效。** 六个下降案例中，2个未发生补检；3个最终无暂缓项，需要结合输出范围与评审理由解释。',
        '- **计分应先算再舍入。** B−A可操作性实际为+0.38，D−C实际为+0.49；D−B仍为+0.31。详见第3节口径纠正。', '',
        '## 2. 数据与计算口径', '',
        f'- 样本：39个任务 × 4个条件＝156份答案；历史执行版本为`{metadata["pipeline_version"]}`。',
        f'- 已有评审：`{metadata["judge_model"]}`，规则版本`{metadata["judge_version"]}`，0—4级模型辅助评分。',
        '- 当前LangGraph工程迁移不改变这些历史分数；本报告不是新框架重跑结果。',
        '- 平均分＝有效评分之和÷有效题数；缺失不计零。D−B先逐题相减，再对共同有效任务求均值。',
        '- 统计使用未舍入原始整数分；展示统一保留两位小数，使用十进制四舍五入（ROUND_HALF_UP）。',
        '- 胜／平／负＝同一道题D得分高于／等于／低于B的题数。整体均分按任务计算，不对五项目等权平均。',
        '- 四项主答案指标与原始配套指导分别报告；配套指导没有全部经过语义门控。',
        '- 类型沿用冻结协议的单一分层标签，来自已有分类，不是新增人工标注。小组样本少，不进行普遍性或显著性声明。',
        '- 项目与类型细分均为事后探索，未进行多重比较校正，不据此宣称某类任务必然更适合D组。',
        '- 失败案例分析是对既有模型评审和运行日志的事后解释，不是人工Gold，也不改写已有分数。', '',
        '## 3. 总体结果复核', '',
        md_table(['组别', *[LABELS[k] for k in MAIN], '原始配套指导（另计）'],
            [[variant, *[fmt(summary['overall'][variant][key]['mean']) for key in DIMENSIONS]] for variant in 'ABCD']), '',
        md_table(['配对比较', *[LABELS[k]+'差值' for k in MAIN]],
            [[f'{a}−{b}', *[fmt(summary['comparisons'][f'{a}-{b}'][k]['mean_delta'], True) for k in MAIN]]
             for a, b in PAIRS]), '',
        f'主要比较D−B：可操作性{fmt(db["actionability"]["mean_delta"], True)}分，'
        f'{db["actionability"]["wins"]}胜／{db["actionability"]["ties"]}平／{db["actionability"]["losses"]}负。'
        'D的可操作性收益大于正确性与完整性的均分变化；不能把所有任务描述为均有改善。', '',
        '**两处四舍五入口径纠正：**若直接用表内两位均分相减，会得到B−A=2.62−2.23=0.39、D−C=2.92−2.44=0.48。'
        '但原始逐题配对分别是15/39=0.384615…（显示+0.38）、19/39=0.487179…（显示+0.49）。'
        '主表均分与原评分没有改动；后续PPT展示差值应先用原始得分计算，再舍入。D−B=12/39=0.307692…仍显示+0.31。', '',
        '## 4. 按项目细分：哪些项目获益', '',
        md_table(['项目', '题数', '正确性Δ', '完整性Δ', '可操作性Δ', '不确定性Δ', '可操作性胜／平／负'],
            [[repo, group['n'], *[fmt(group['d_minus_b'][k]['mean_delta'], True) for k in MAIN],
              '/'.join(str(group['d_minus_b']['actionability'][k]) for k in ('wins','ties','losses'))]
             for repo, group in summary['by_project'].items()]), '',
        f'**解释：**当前样本中四项目可操作性均分上升、SymPy持平。Ray的9题净增{ray_action_gain}分，'
        f'占总净增{total_action_gain}分的{ray_action_gain/total_action_gain:.0%}；Manim和napari的正确性均分反而小幅下降，'
        '说明“可操作性更高”不等于所有质量维度一起提高。此表不将项目名称作为难度替代，也不从napari仅4题外推项目整体效果。', '',
        '## 5. 按冻结任务类型细分', '',
        md_table(['类型（原标签）', '题数', '正确性Δ', '完整性Δ', '可操作性Δ', '可操作性胜／平／负'],
            [[f'{TYPES.get(kind,kind)}（{kind}）', group['n'],
              *[fmt(group['d_minus_b'][k]['mean_delta'],True) for k in MAIN[:3]],
              '/'.join(str(group['d_minus_b']['actionability'][k]) for k in ('wins','ties','losses'))]
             for kind, group in summary['by_type'].items()]), '',
        '类型标签只用于保持原抽样分层。比如“pytest 4兼容”任务原标签为feature，说明这些标签并非完全可靠的研究真值；'
        '本步不根据结果重新标注或改分。性能类只有2题，待复核类也不能当成明确技术类型。', '',
        '## 6. 按D组完整轮数观察：补检是否与收益同时出现', '',
        md_table(['完整分析轮数', '题数', 'D−B可操作性', '胜／平／负', 'D请求耗时中位数（秒）'],
            [[n, group['n'], fmt(group['actionability']['mean_delta'],True),
              '/'.join(str(group['actionability'][k]) for k in ('wins','ties','losses')),
              fmt(group['median_request_seconds'])] for n, group in mech['by_complete_rounds'].items()]), '',
        '完整轮数只计算成功保存的分析／核查／门控轮；执行错误时可能还有未完成的请求。'
        '通常2轮意味着完成1次补检，3轮意味着完成2次补检，但错误与补检尝试须看逐轮记录。'
        '补检次数不是随机分配，因此不能把组间差异直接当成“多补检一轮导致提升”的因果证据。', '',
        md_table(['D停止原因', '任务数'], sorted(mech['stop_reasons'].items())), '',
        '“all_claims_supported”只表示当前生成的核心结论集合获内部核查接受，不保证原Issue缺口全部补足或外部评审高分。', '',
        '### 6.1 两个可解释的获益案例', '',
        '以下是事后选取的机制示例，不替代全量比较；它们也保留了原评审指出的未解决问题。', '',
        md_table(['任务', '主答案可操作性B→D', '完整性B→D', 'D完整轮数', '观察到的变化'],
            [[task,
              f'{by_id[task]["scores"]["B"]["actionability"]}→{by_id[task]["scores"]["D"]["actionability"]}',
              f'{by_id[task]["scores"]["B"]["completeness"]}→{by_id[task]["scores"]["D"]["completeness"]}',
              len(by_id[task]['results']['D']['rounds']), description]
             for task, description in [('ray-project/ray#8894','补入同类选项校验与异常测试模式'),
                                       ('sympy/sympy#12602','补入count_roots直接回归测试')]]), '',
        '- **Ray #8894：**初轮缺少与num_return_vals直接相关的范围/能力判断依据，补检两轮后获得同类num_returns选项校验测试。'
        '既有评审的主答案可操作性从2升3，原始指导从3升4；正确性和完整性均保持3。'
        '新旧参数的等价关系与返回数量不匹配场景仍未解决，不能描述为问题已修复。',
        '- **SymPy #12602：**补检获得test_count_roots直接测试，完整性2升3、主答案可操作性2升3，正确性保持4。'
        '不过原始配套指导从4降3，且性能阈值、优化方向与实现入口仍有缺口；这是“某些维度改善”，不是整份答案全面改善。', '',
        '## 7. 六个可操作性下降案例', '',
        '本步按统一规则纳入所有D主答案可操作性低于B的案例，不按解释是否好看来删减。'
        '这是事后分析，不是预注册的新实验。下表“观察”来自保存轨迹与已有评审，归因属于待验证解释。', '',
        md_table(['任务', 'B→D', '完整轮数', '最终暂缓项', '原始指导B→D', '分析重点'],
            [[task, f'{by_id[task]["scores"]["B"]["actionability"]}→{by_id[task]["scores"]["D"]["actionability"]}',
              len(by_id[task]['results']['D']['rounds']),
              len(by_id[task]['results']['D']['gated']['withheld_claim_ids']),
              f'{by_id[task]["scores"]["B"]["guidance_actionability"]}→{by_id[task]["scores"]["D"]["guidance_actionability"]}',
              CASE_NOTES.get(task, ('待补充解释',))[0]] for task in summary['decline_task_ids']]), '']
    for task in summary['decline_task_ids']:
        row = by_id[task]
        result = row['results']['D']
        topic, observation, interpretation = CASE_NOTES.get(task, ('待检查', '需读取原记录', '不作归因'))
        lines += [f'### {task}：{topic}', '', f'- 原标题：{row["title"]}',
            '- 已保存事实／评审观察：'+observation, '- 分析解释：'+interpretation,
            f'- 轨迹：{len(result["rounds"])}个完整轮；停止原因`{result["stop_reason"]}`；'
            f'最后暂缓`{", ".join(result["gated"]["withheld_claim_ids"]) or "无"}`。',
            f'- 既有评审摘要（B）：{row["judgments"]["B"]["summary"]}',
            f'- 既有评审摘要（D）：{row["judgments"]["D"]["summary"]}',
            '- D评审指出的遗漏：'+('；'.join(row['judgments']['D']['missing_points']) or '未列出明确遗漏'),
            '- 原始D答案：'+file_link(root, experiment/'results'/task.replace('/','__').replace('#','__')/'D.json'),
            f'- 评分定位：B=`{row["prediction_ids"]["B"]}`，D=`{row["prediction_ids"]["D"]}`，见评审JSON与盲评映射。', '']
    no_withheld = sum(not by_id[t]['results']['D']['gated']['withheld_claim_ids'] for t in summary['decline_task_ids'])
    no_supplement = sum(len(by_id[t]['results']['D']['rounds']) == 1 for t in summary['decline_task_ids'])
    lines += [f'六例中，{no_withheld}例最终没有暂缓项，{no_supplement}例仅初轮即停止。'
              '因此不能把六次下降统一解释为“补检失败”或“门控删掉了答案”。', '',
        '## 8. 执行异常与资源口径', '',
        md_table(['条件', '最终运行调用数', '已记录Token', '用量完整的任务', '请求耗时中位数（秒）'],
            [[v, x['model_calls'], x['recorded_tokens'], f'{x["usage_complete_count"]}/39',
              fmt(x['median_request_seconds'])] for v, x in mech['costs'].items()]), '',
        '成本读取每份答案的resources，属于生成该保存结果的最终运行请求记录，不混入cumulative_resources的历史重跑成本。'
        '请求耗时不是完整端到端检索时延；用量缺失时Token是已记录值，不能解释为完整账单。', '']
    for row in mech['execution_error_cases']:
        lines += [f'- `{row["task_id"]}`：停止`{row["stop_reason"]}`，保留{row["complete_rounds"]}个完整轮。'
                  '虽然文件status为complete，仍有后续执行错误；此处complete不表示补检流程毫无异常。']
    sensitivity = mech['sensitivity_without_execution_errors']['actionability']
    lines += ['', f'**执行错误敏感性检查：**保留原39题时D−B可操作性为{fmt(db["actionability"]["mean_delta"],True)}分；'
              f'仅作补充，排除有执行错误的任务后剩{sensitivity["n"]}题，差值为{fmt(sensitivity["mean_delta"],True)}分，'
              f'{sensitivity["wins"]}胜／{sensitivity["ties"]}平／{sensitivity["losses"]}负。'
              '正式样本和主评分仍保留原39题，不据此更改样本或重算已发布报告。']
    lines += ['', '## 9. 本步结论：可直接放入PPT', '',
        '### 建议新增PPT页A：D组收益分布', '',
        f'> 当前39题模型评分中，D组可操作性收益主要来自Ray样本（+0.67分、7胜1平1负）；'
        'SymPy均分持平。收益呈现项目差异，并非所有项目或所有质量维度同步改善。', '',
        '- 保留本报告第4节项目表，展示D−B的可操作性差值与胜平负。',
        '- 保留第5节任务类型表中题数与可操作性列；小样本类别标注题数。',
        '- 表述收益分布时限定为“当前39题样本与已有模型评分”，不表述为所有项目普遍有效。', '',
        '### 建议新增PPT页B：失败类型与方法边界', '',
        '- **证据覆盖不足**：跨调用点迁移、测试验收等要求，补检到两轮上限后仍可能缺证。',
        '- **已有材料利用不足**：找到相关文件后，还需要理解页面关系、修改入口与条件性要求。',
        '- **评分与输出范围敏感**：部分下降案例原始指导仍为4分，且未发生门控暂缓或补检，不能简单认定为检索机制失效。', '',
        '可在PPT用一行数字总结：**测试类+0.75（4题）、文档类0.00（7题）、重构类−0.33（3题）；'
        '均为原冻结标签上的事后观察。**', '',
        '### 当前可以采用的总体表述', '',
        '> 在39题模型辅助评测中，D组主答案可操作性相对B提高0.31分，18题提高、15题持平、6题下降。'
        '细分分析用于识别收益集中位置及失败边界；六个下降案例同时包含缺证、验收暂缓、材料利用与评分范围敏感等情形，'
        '尚不能把分差全部归因于单个模块。', '',
        '## 10. 对下一步的提示（本步不执行）', '',
        '下一项是等预算对照。应据本步分布覆盖已有受益、持平和下降任务，避免只挑D优势案例；'
        '控制模型、任务、输出结构、评审规则和资源口径，并比较固定更多检索或更多生成与缺口驱动补检。'
        'D1轮任务本身没有发生补检，应与真正补检的任务分开观察。尚未编写或启动下一步新实验。', '',
        '## 11. 逐题完整得分与D−B差值', '',
        md_table(['任务', '类型', '可操作性A/B/C/D', '正确性Δ', '完整性Δ', '可操作性Δ', '完整轮数'],
            [[r['task_id'], r['stratum'], '/'.join(str(r['scores'][v]['actionability']) for v in 'ABCD'),
              *[fmt(r['scores']['D'][k]-r['scores']['B'][k],True)
                if r['scores']['D'][k] is not None and r['scores']['B'][k] is not None else '—' for k in MAIN[:3]],
              len(r['results']['D']['rounds'])] for r in records]), '',
        '## 12. 来源、复现与完整性', '',
        '- 分析脚本：'+file_link(root, root/'analyze_existing_experiments.py'),
        '- 已有评分：'+file_link(root, judge/'model_evaluation.json'),
        '- 盲评条件映射：'+file_link(root, judge/'blind/private/mapping.json'),
        '- 冻结协议：'+file_link(root, experiment.parent/'protocol.json'),
        '- 正式留出清单：'+file_link(root, experiment/'holdout_manifest.json'), '',
        '在PyCharm直接运行`analyze_existing_experiments.py`，或在项目根目录执行：', '',
        '```powershell', '.\\.venv\\Scripts\\python.exe analyze_existing_experiments.py', '```', '',
        f'核对了{len(metadata["paths"])}个输入文件（156份答案＋4份评分/身份文件）的SHA256，'
        '运行结束前逐一复核未变更；校验清单和全部未四舍五入统计保存在同目录配套JSON。', '',
        '**这一步已完成；下一步等待用户确认后，再开展等预算对照。**', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, default=ROOT/'experiments/frozen_20261017/final_heldout_v3')
    parser.add_argument('--judge-id', default='8d4ee922f2e0f946')
    parser.add_argument('--output', type=Path, default=ROOT/'output/研究改进_第1步_39题结果分析.md')
    args = parser.parse_args(argv)
    experiment = args.experiment.resolve()
    judge = experiment/'model_judge'/args.judge_id
    print('[1/4] 读取39题、156份已有答案与评分；不调用模型。', flush=True)
    records, metadata = load_dataset(ROOT, experiment, judge)
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in metadata['paths']}
    print('[2/4] 计算整体、项目、任务类型与轮次统计。', flush=True)
    summary = analyze_records(records)
    summary['mechanism'] = mechanism_statistics(records)
    print('[3/4] 整理6个下降案例及既有评审定位。', flush=True)
    content = render_report(ROOT, experiment, judge, records, summary, metadata)
    if any(sha(ROOT/p) != digest for p, digest in hashes.items()):
        raise RuntimeError('An experiment input changed during analysis; no report published')
    output = args.output.resolve()
    if output in {p.resolve() for p in metadata['paths']} or output.with_suffix('.json') in {p.resolve() for p in metadata['paths']}:
        raise ValueError('Report output cannot overwrite an experiment input')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding='utf-8')
    companion = {'analysis_scope': 'posthoc_existing_model_judgments_not_new_experiment',
        'source_pipeline_version': metadata['pipeline_version'], 'judge_model': metadata['judge_model'],
        'source_files_unchanged': True, 'source_sha256': hashes, 'summary': summary,
        'per_task': [{'task_id': r['task_id'], 'repo': r['repo'], 'stratum': r['stratum'],
                     'scores': r['scores'], 'prediction_ids': r['prediction_ids']} for r in records]}
    output.with_suffix('.json').write_text(json.dumps(companion,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'[4/4] 完成。Markdown：{output}', flush=True)
    print(json.dumps({'tasks': len(records), 'answers': len(records)*4,
        'actionability_d_minus_b': summary['comparisons']['D-B']['actionability']['mean_delta'],
        'declines': summary['decline_task_ids'], 'source_files_unchanged': True},ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
