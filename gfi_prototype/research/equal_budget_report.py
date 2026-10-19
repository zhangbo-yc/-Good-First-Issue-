"""Offline Markdown reporting for the matched-call follow-up experiment."""
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean

from gfi_prototype.research.equal_budget import METHODS, paid_attempts

DIMENSIONS=('correctness','completeness','actionability','calibration','guidance_actionability')
LABELS=('正确性','完整性','主答案可操作性','不确定性表达','原始配套指导（另计）')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(str(x) for x in row)+' |' for row in rows]])


def number(value):
    return '—' if value is None else f'{value:.2f}'


def write_report(root,output,config,results,path):
    root,output,path=Path(root),Path(output),Path(path)
    by_task=defaultdict(dict)
    for result in results:
        by_task[result['task_id']][result['variant']]=result
    score_path=output/'evaluation/scores.json'
    scores=read(score_path) if score_path.exists() else {'rows':[],'complete':False}
    judged=defaultdict(dict)
    mapping_path=output/'evaluation/blind/private/mapping.json'
    if mapping_path.exists():
        mapping={r['prediction_id']:r for r in read(mapping_path)}
        for row in scores['rows']:
            identity=mapping[row['prediction_id']]
            judged[row['task_id']][identity['variant']]=row['judgment']['scores']
    complete_scores={k:v for k,v in judged.items() if set(v)==set(METHODS)}
    unique_cost=paid_attempts(output/'requests')
    strict=[]
    for task_id,group in by_task.items():
        resources=[group[m]['resources'] for m in METHODS]
        spent=paid_attempts(output/'requests'/task_id.replace('/','__').replace('#','__'))
        if (len({r['model_calls'] for r in resources})==1 and all(r['usage_complete'] for r in resources)
                and not spent['failed_calls']):
            strict.append(task_id)
    lines=['# 研究改进第2步：等调用预算对照实验', '',
        '**状态：设计与脚本已就绪；真实实验结果以运行后的本文件为准。**', '',
        f'- 已完成三组分析：{len(by_task)}/39题；已完成三组评分：{len(complete_scores)}/39题。',
        '- 当前没有评分时不预置D组获胜结论。未自动进入创新表述修改或前端接入。', '',
        '## 1. 本步要回答的问题', '',
        'D组改善任务理解，是因为按证据缺口寻找材料，还是因为用了更多模型调用？'
        '本轮让对照组拥有与D相同的分析／核查轮次，区分反复修改、固定扩展检索与缺口驱动补检。', '',
        '## 2. 三组具体怎样做', '',
        table(['条件','已有证据如何处理','额外模型调用如何使用'],[
            ['R：固定证据反复修改','保持初始证据包不变','依据上轮分析和核查反馈修订，再核查与门控'],
            ['S：固定查询扩展','只用Issue标题和正文形成固定排名，按顺序补入材料','与D相同提示词重新分析，再核查与门控'],
            ['D：按缺口补检','使用当前核查提出的缺口查询','保持原LangGraph自适应流程，更新证据后重新分析和核查']]), '',
        '每题先重新运行D；它的第一轮分析、核查、门控结果作为三组完全相同的共同起点。'
        'R/S随后完成与D相同数量的完整轮：D为1轮则R/S直接共享同一初轮输出；D为2或3轮则R/S也为2或3轮。'
        '不根据评审分数选择轮次，也不从多份答案中挑最高分；统一使用最后完整轮。', '',
        'R额外得到上一轮审查反馈，是有意设置的较强“多调用”对照；反馈按共同上下文上限截断，记录截断情况。'
        'S与D使用完全相同的分析提示词，不额外输入上一轮反馈；主要区别是固定Issue查询还是缺口驱动查询。'
        'D沿用原LangGraph流程，不改变停止规则。', '',
        '## 3. “等预算”具体等什么', '',
        '- **匹配项：**同一模型、同一初始证据、相同分析结构与核查门控规则、相同完整轮数及逻辑调用次数。',
        '- 每轮1次分析＋1次核查；最多3轮，即每方法最多6次逻辑调用。每请求输出上限5,000 Token，上下文上限60,000字符。',
        '- 共同Token停止阈值250,000，依据服务商返回用量在后续请求前检查，并非严格的单次调用前硬限。SDK自动重试关闭，节点不自动重试。',
        '- **不声称严格等Token实耗：**证据和输出长短不同，输入与输出Token可能不同。实际Token及比值独立报告。',
        '- 公共初轮只真实付费一次，但其原始资源记录归属到每种方法，计算可比的每方法逻辑资源。跨方法累计账单按真实请求回执去重统计。',
        '- R/S恢复缓存时仍计入当时的逻辑调用与用量；恢复本身不再次调用模型。失败尝试单独留档，不能从实际成本中抹掉。',
        '- 严格匹配子集要求三组逻辑调用相等、用量完整、没有失败尝试；同时报告全部已评分配对，避免仅报干净子集。',
        '- 采用D实际轮次配对，不能证明这是最优预算分配，也不能完全替代严格等实耗Token的实验。', '',
        '## 4. 样本、版本与评分', '',
        '- 使用原39题的全部任务，不按Top-2发现的胜负挑题；新结果、初轮与两组控制均重新生成，不复用旧D答案。',
        '- 已查看过这些任务的旧结果，所以本轮称“既有39题上的追加控制实验”，不称新未见留出集。',
        '- 新设计在请求前冻结，包括任务ID、模型、参数、代码哈希与依赖版本；变更需新建equal_calls_*目录。',
        f'- 分析模型：`{config["model"]}`；外部评审：`{config["judge_model"]}`。不开展异系列交叉评审。',
        '- 沿用共同证据、匿名组别及0—4级五项评分；主答案与原始配套指导分开。模型辅助评价不是人工Gold或准确率。',
        '- 完全相同的匿名评价输入只评分一次并复用结果，避免对同一内容随机打出不同分；各方法映射仍完整保存。',
        '- 只执行主评分，不额外运行能力门槛专项复核。', '',
        '## 5. 如何在PyCharm运行', '',
        '脚本：`run_equal_budget_experiment.py`。使用项目`.venv`解释器，`.env`内已有DeepSeek密钥即可。', '',
        '**直接点击运行：**默认all，依次执行39题分析、统一盲评、生成本报告，会真实调用DeepSeek。', '',
        '```powershell', '.\\.venv\\Scripts\\python.exe run_equal_budget_experiment.py', '```', '',
        '只检查设计，不调用模型：', '```powershell',
        '.\\.venv\\Scripts\\python.exe run_equal_budget_experiment.py --phase prepare', '```', '',
        '先跑1题查看进度（只分析，不启动全量盲评）：', '```powershell',
        '.\\.venv\\Scripts\\python.exe run_equal_budget_experiment.py --phase analyze --limit 1', '```', '',
        '之后直接运行默认命令，会复用这1题并继续其他任务。中断后重跑同一命令，已完成节点和答案会恢复。', '',
        '只分析、只评审或只重新汇总，分别使用`--phase analyze`、`--phase review`、`--phase report`。'
        '完整盲评要求39题的三组结果全部完成；不要在只跑1题后直接请求全量评分。', '',
        '运行会显示任务序号、方法、节点、等待心跳、Token和缓存命中。每15秒打印模型等待进度。'
        '最多需要39×14＝546次新的生成请求（共享初轮，未含失败重试），通常因D提前停止而少于此数；'
        '另有模型参考与盲评请求。运行可能较长，本轮不预估精确时长。', '',
        '## 6. 当前结果', '']
    if not complete_scores:
        lines += ['**待运行：尚无完整R/S/D配对评分，不报告有效性提升。**', '']
    else:
        lines += [table(['方法','配对题数',*LABELS],[[m,len(complete_scores),*[number(mean(
            g[m][k] for g in complete_scores.values() if g[m][k] is not None))
            if any(g[m][k] is not None for g in complete_scores.values()) else '—' for k in DIMENSIONS]] for m in METHODS]),'']
        for baseline in ('R','S'):
            for label,ids in [('全部完整评分配对',list(complete_scores)),('严格调用匹配且用量完整子集',[t for t in strict if t in complete_scores])]:
                deltas=[complete_scores[t]['D']['actionability']-complete_scores[t][baseline]['actionability'] for t in ids
                        if complete_scores[t]['D']['actionability'] is not None and complete_scores[t][baseline]['actionability'] is not None]
                lines += [f'- D−{baseline}，{label}：n={len(deltas)}，可操作性均差'
                    f'{number(mean(deltas)) if deltas else "—"}；'
                    f'胜／平／负={sum(d>0 for d in deltas)}/{sum(d==0 for d in deltas)}/{sum(d<0 for d in deltas)}。']
        if len(complete_scores)<39:
            lines += ['', '**评分尚未覆盖全部39题，以上为进度性汇总，不作为最终结论。**']
    lines += ['', '## 7. 逻辑预算与实际成本', '',
        table(['任务','R/S/D逻辑调用','R/S/D已记录Token','D/R Token比','D/S Token比','严格匹配'],[
            [task,'/'.join(str(g[m]['resources']['model_calls']) for m in METHODS),
             '/'.join(str(g[m]['resources']['total_tokens']) for m in METHODS),
             number(g['D']['resources']['total_tokens']/g['R']['resources']['total_tokens']) if g['R']['resources']['total_tokens'] else '—',
             number(g['D']['resources']['total_tokens']/g['S']['resources']['total_tokens']) if g['S']['resources']['total_tokens'] else '—',
             '是' if task in strict else '否'] for task,g in sorted(by_task.items())]), '',
        f'跨方法实际生成请求（不重复计公共前缀）：{unique_cost["model_calls"]}次；'
        f'已记录Token：{unique_cost["recorded_tokens"]}；失败尝试：{unique_cost["failed_calls"]}；'
        f'用量完整：{unique_cost["usage_complete"]}。评审成本见evaluation/scores.json，另行计算。', '',
        '## 8. 文件位置与PPT使用方式', '',
        f'- 独立实验目录：`{output.as_posix()}`。历史156份答案和前端缓存不修改。',
        '- design.json：冻结配置；requests/：节点结果与真实尝试回执；results/：R/S/D完整答案。',
        '- evaluation/blind/private/mapping.json：匿名方法映射；evaluation/scores.json：评分与引用。',
        '- last_error.json：最近一次中断类型；不写API密钥和原始服务商报错。',
        '- **设计页可以先写：**“在相同初轮输出与相同分析/核查调用次数下，对比固定证据修订、固定查询扩展与缺口驱动补检。”',
        '- **必须附注：**“等调用次数，不是严格等Token实耗；实际资源另报。既有39题上的追加控制实验。”',
        '- **结果页暂不新增胜负结论。**等全部评分生成，再根据D−R、D−S与实际Token解释方法收益。', '',
        '**下一步：用户运行脚本并提供结果后再分析；不自动进入Top-3、Top-5或改写PPT结论。**', '']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join(lines),encoding='utf-8')
    return path
