# DeepSeek Pro 自动评审

在项目根目录的 PowerShell 执行：

```powershell
.\.venv\Scripts\python.exe judge_experiments.py
```

读取项目现有 `.env` 中的 `DEEPSEEK_API_KEY`，默认评审模型为 `deepseek-v4-pro`，开启思考模式。
不会改动 `.env` 的生成模型配置。只评审已经完成的实验输出，不会自动运行45个任务的A/B/C/D分析。
当前只有一个任务的四组输出，因此首次预计6次成功请求：1份共同参考要点、4份匿名答案评审、
1份针对任务所需能力门槛的补充复核（四份答案合并发送）。
每个请求格式校验失败时最多再尝试一次；实际费用与思考长度有关。

仅评审指定任务：

```powershell
.\.venv\Scripts\python.exe judge_experiments.py --task-id 'ManimCommunity/manim#2379'
```

输出在 `experiments/frozen_20261017/model_judge/<输入指纹>/`：

- `model_evaluation.md`：中文报告、A/B/C/D等级分及逐条理由。
- `model_evaluation.json`：机器可读结果，明确标记模型评审。
- `stage_review.md/json`：任务所需能力门槛的补充复核；避免将stage理解为个人背景或任务生命周期。
  补充复核不重算综合评分，主报告保留原分数并明确标注其限制。
- `references/`：模型参考要点；先于答案评审生成，不看待评答案。
- `judgments/`：匿名评审响应、实际返回模型名、Token及耗时。
- 各缓存目录中的 `attempts/`：每次请求的资源记录，包含失败尝试。
- `blind/private/`：匿名编号与组别映射，不发送给评审模型。
- `last_error.json`：最近失败类型及HTTP状态，避免输出凭据和服务商响应正文。

相同命令可断点续跑，已成功的相同输入不会重复付费。更换模型、实验输出或提示词会使用新缓存。
每次正常写出结果时刷新中文报告；发生失败后保留已有文件。失败后新增一次attempt不代表成功缓存。

评分是0—4的模型主观等级，材料不足可给null；它不是准确率。正确性、完整性、主答案可操作性、
不确定性表达分开报告，并核查每条结论自身的引用。C/D使用最终筛选后的实际交付结论。
v2另列原始入手指导可操作性评分，补齐getting_started、知识背景、相关文件及测试建议。
C/D原始指导未经过门控，显式标记为辅助分析，不作为已经核查保留的最终结论。
能力阶段专指新人经验水平，不能混同任务生命周期。每份答案记录实际可见证据编号，
将相对于共同材料的客观遗漏与忽略实际收到的证据分开判断；不得指责A组未利用未提供的源码。
共同证据只发送一份，避免原版在source和prediction中重复发送。

仅补充复核一个已有v2目录的能力门槛（其他评分不会重跑）：

```powershell
.\.venv\Scripts\python.exe judge_experiments.py --stage-review-only experiments/frozen_20261017/model_judge/<输入指纹>
```
所有组使用同一任务的共同证据，匿名编号与随机顺序，避免把组名或原审查结果当作质量信号。
原始材料仍可能透露回答风格，不能声称完全消除了偏差。

参考要点由同一个Pro模型根据现有证据生成，不是独立Gold。评审模型只看提供的材料，
不会读取整个仓库或自动联网检查。无法据此证明全仓库需求召回率；同系列模型存在共同偏差。
这些结果单独保存，不填写、覆盖人工Gold、双评审表或正式人工效果报告。

初次交付未调用API；后续根据用户授权增加流式接收、离线测试及v2重评。
模型响应通过SSE流式接收；不保存思考原文；用量中的completion_tokens含模型思考消耗。
v2使用独立输入指纹目录，保留v1报告；缓存按完整提示词和输入识别，原报告不覆盖。
模型接口依据：https://api-docs.deepseek.com/api/create-chat-completion/

## 五项目开发样例

先运行完整的 A/B/C/D 开发样例：

```powershell
.\run_dev_five.ps1
```

固定任务为 Manim #52、SymPy #5839、Dask #8878、Ray #15501、napari #6251。
Manim #2379 用于开发新规则，不计入这五个新样例。结果写到
`experiments/frozen_20261017/dev_five_v2/results/`；中途停止后再次运行会复用已完成的条件。
当前工作区中有一次被停止的试运行，已完成 Manim #52 的 A/B/C/D 和 SymPy #5839 的 A/B；
脚本将缓存这些结果并继续完成其余条件，不覆盖它们。

A/B/C/D都完成后，再运行 DeepSeek Pro 盲评：

```powershell
.\review_dev_five.ps1
```

如果答案评审已完成、能力门槛复核遇到网络错误，只重试该专项：

```powershell
.\review_dev_five.ps1 -StageOnly
```

盲评报告另存在 `experiments/frozen_20261017/dev_five_v2/model_judge/`。
将该目录下最新输入指纹文件夹里的 `model_evaluation.md`、`stage_review.md` 和各任务结果
交给 Codex，即可分析五项目的开发样例。评审脚本按实际已完成的不同任务数作为报告分母；
若五个任务没有全部完成，组间分数不可直接比较。
