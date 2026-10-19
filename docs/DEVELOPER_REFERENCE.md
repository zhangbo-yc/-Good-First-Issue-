# 项目与 GFI 证据分析原型

> **历史开发参考。** 本文保留原 README 中的数据采集、缓存和旧原型说明，部分研究计划与实验状态已过时。参赛项目定位和当前实验结果请以 [README.md](../README.md) 与 [RESEARCH_METHOD.md](RESEARCH_METHOD.md) 为准；旧产品说明不代表当前科研方法主线。

完整技术文档见 [docs/PROJECT_TECHNICAL_DOCUMENT.md](docs/PROJECT_TECHNICAL_DOCUMENT.md)，涵盖数据输入与使用、技术选型、架构、检索与模型分析、技术匹配算法、字段、安装运行、故障处理、验证及后续实施。

目标：输入项目和GFI，解释任务目标、技术栈、适合的背景/新手阶段，并给出同项目后续GFI的衔接建议。不采集个人画像，也不进行个人能力评分或用户匹配。

现有数据为SymPy、napari、Dask、Manim、Ray五项目967条Issue；在新人界面中展示945个任务和22份贡献指引，统一按开放、未解决处理。已通过DeepSeek真实生成全部967条中文标题与说明，并将50个示例升级为带源码证据的schema v2分析，重新计算同项目技术推荐。旧助手草稿保留为历史资料，本轮新增批量内容只使用DeepSeek。

## 新人项目门户（当前推荐入口）

```powershell
.\.venv\Scripts\python.exe serve.py
```

打开 `http://127.0.0.1:8503/`。独立HTML/CSS/JavaScript界面包含五项目卡片、真实任务预览、全量任务/贡献指引列表、中文搜索与筛选、任务导读、相关任务及流式助手。页面不展示原始JSON、评论、关闭/认领状态或内部证据编号。模型配置从被Git忽略的`.env`读取；浏览任务使用缓存，发送问题时才调用DeepSeek。

```powershell
# 全量中文说明：已完成任务会命中缓存，失败可断点重试
.\.venv\Scripts\python.exe prepare_newcomer.py
# 对现有50个示例生成源码级详细导读，随后刷新技术匹配
.\.venv\Scripts\python.exe prepare_deep_guides.py
.\.venv\Scripts\python.exe recommend_gfi.py --repo ManimCommunity/manim --issue 4983 --top-k 3
```

使用与覆盖说明见 [docs/NEWCOMER_PORTAL.md](docs/NEWCOMER_PORTAL.md)。

## 科研方法与消融实验

证据审查、最多两轮的定向补查、方向性任务衔接、45条冻结评测集以及A–D消融实验已作为独立研究管线实现，不修改现有前端或产品缓存。方法、公式、盲评要求和运行命令见 [docs/RESEARCH_METHOD.md](docs/RESEARCH_METHOD.md)。主入口为 `research.py`；人工Gold和双评审完成前，报告不会生成效果提升结论。

**当前Demo假设全部GFI开放、未解决，不用关闭/指派/PR状态过滤，也无需补查实时状态；原始快照状态保持原样。**

技术路线见 [docs/TECHNICAL_ARCHITECTURE.md](docs/TECHNICAL_ARCHITECTURE.md)，数据缺口见 [data/DATA_AUDIT.md](data/DATA_AUDIT.md)。用户画像/匹配支线已经移除，清理记录见 [docs/SCOPE_CLEANUP.md](docs/SCOPE_CLEANUP.md)。

采集、清洗、草稿导出和本地审计使用Python 3.10+标准库；模型原型另装依赖，已验证Python 3.11.8。

## 数据采集

SymPy/napari使用 `collect_gfi.py`。Dask/Manim使用 [DASK_MANIM_COLLECTION.md](DASK_MANIM_COLLECTION.md) 中的 `python collect_dask_manim.py --prompt-tokens 1`，默认包含开放及已关闭的GFI，并在完成后生成标签核验报告。

Ray使用独立入口 `python collect_ray.py --prompt-tokens 1`，默认采集当前仍带GFI标签的全部状态Issue，复用同一采集及标签审计，不重采其他项目。源码、Issue、清洗、10条任务草稿和检索均集中在 `data/ray-project__ray/`。详见 [RAY_COLLECTION.md](RAY_COLLECTION.md)。

## 运行（PowerShell）

```powershell
cd D:\haikesong-------bisai
python collect_gfi.py --prompt-tokens 1
```

在隐藏提示中粘贴有效 GitHub Token 并回车；凭据不会写入文件、日志或源码。确有两个不同 Token 时使用 `--prompt-tokens 2`。也支持进程环境变量 `GITHUB_TOKEN_1`、`GITHUB_TOKEN_2`（以及 `GITHUB_TOKEN` / `GH_TOKEN`），设置后直接运行 `python collect_gfi.py`。不要将真实凭据写进脚本或提交到仓库。

重复 Token 自动去重并共享节流。即使两个 Token 不同，同一 GitHub 用户的 Token 也可能共享配额，不应依靠切换 Token 绕过限流。API 的分页、限流等待和有限次数重试均已处理。公开仓库只需要相应的读取权限；401/403 请核实凭据有效性、仓库授权及读取权限。

```powershell
# 包含当前仍带这些标签的已关闭 Issue
python collect_gfi.py --state all --prompt-tokens 1 --output data-all

# 重新下载任务证据；源码保持原先已固定的 commit
python collect_gfi.py --refresh --prompt-tokens 1

# 小规模联调，每个仓库 1 条，不下载源码
python collect_gfi.py --limit 1 --no-source --prompt-tokens 1 --output .smoke-data

# 无凭据仅用于公开数据联调，额度可能不足以完成全部采集
python collect_gfi.py --anonymous --max-rate-wait 0 --limit 1 --no-source --output .smoke-data
```

`collect_gfi.py`默认 `--state open`，只负责SymPy/napari；Dask/Manim和Ray由独立入口负责。SymPy 对 `Easy to Fix` 和 `good first issue` 取并集，napari 使用 `contribute:good first issue`；Ray实际标签为 `good-first-issue`。排除 Issues API 返回的 PR，按 Issue 编号去重。`--state all` 仍只能发现**当前保留目标标签**的任务，不保证恢复曾经带标签但后来移除的任务。

## 输出

```text
data/
  collection_summary.json
  sympy__sympy/
    manifest.json              # 范围、数量、错误、complete/partial 状态
    repository.json            # GitHub 仓库元信息
    raw/label-*.json            # 标签查询的完整分页结果
    issues/12345.json          # 原始 Issue + 全部分页评论 + timeline + 关联 PR 摘要
    issues.jsonl               # 本次范围内采集成功的任务，每行一条
    issues.csv                 # 便于 Excel 浏览的摘要
    source_snapshot.json       # commit SHA、下载时间、ZIP 的 SHA256
    source-<commit>.zip         # 固定版本的源码归档
    source/                    # 解压后的项目源码，含项目文档与测试
  napari__napari/
    ...
```

Issue 保留标题、正文、标签、作者、指派人、状态、时间戳等 API 原始字段。timeline保留API返回的事件；采集器只从cross-referenced事件提取关联PR摘要。当前967条记录的PR摘要均为空，不能解释为没有关联PR；本轮Demo不使用这些状态，因此不要求补采。**目前没有PR diff、完整review、附件二进制或贡献者历史。** 仓库源码以commit固定的完整工作树保存，**不是包含全部分支和历史的Git镜像**；子模块内容及Git LFS实体不额外下载。不会执行下载项目的代码。

完整开放标签列表可能包含指引帖或汇总帖，原样保留供后续清洗。开放且未指派不保证无人认领，需要结合评论和关联 PR 再判断。

## 断点续采与结果检查

重复运行同一命令即可续采：重新拉取标签列表，仅复用原始 Issue 数据仍相同且完整落盘的任务。失败任务会重试；`--refresh` 强制重拉评论和 timeline（包括正文未变但关联 PR 有更新的情况）。已不在本次范围内的旧单条文件不会删除，但不会进入本次 JSONL/CSV 汇总。

`manifest.json` 和 `collection_summary.json` 中 `status=complete` 表示本次所选范围成功；`limited_run=true` 表示只是 `--limit` 联调，不能当全量结果。失败返回退出码 1 并记录 stage/Issue 编号；正常完成返回 0。

各 API 请求发生在不同时间，所以采集是时间窗口快照，不是 GitHub 的事务一致快照，也不是任务创建时的历史代码快照。后续做历史回放实验需要额外恢复当时版本，避免答案泄露。

每个输出目录只允许运行一个采集进程。崩溃后如果 `.collector.lock` 残留，请先确认没有采集进程，再手动删除该锁文件。想获取新的源码版本时使用新的输出目录，保留原版本便于实验复现。

## 第①步：离线清洗

```powershell
python clean_gfi.py
```

仅处理本地已采集数据，原始文件保留。每项目 `processed/` 输出所有记录的 `triage.jsonl`、`triage.csv`，以及具体任务候选 `tasks.jsonl`、非具体任务 `non_task_records.jsonl`。全局结果见 `data/CLEANING_REPORT.md` 与 `data/cleaning_summary.json`。

记录类型、任务类型和可用状态均为初步清洗结果，不是独立人工标注的标准答案。历史认领、部分修复、关闭提问和条件性关闭建议均保留证据；当前Demo不按这些状态过滤。

清洗器会处理本地 `data/` 下所有项目。开放具体任务保存到 `tasks.jsonl`，关闭具体任务保存到 `historical_tasks.jsonl`，汇总帖和指引保存到 `non_task_records.jsonl`，不将关闭状态解释为成功修复。

## 第②步：五仓库任务分析草稿

```powershell
python prepare_annotation_drafts.py
```

使用 `annotation_drafts.json` 中SymPy/napari草稿、`annotation_drafts_additional.json` 中Dask/Manim草稿，以及 `annotation_drafts_ray.json` 中Ray草稿，五仓库各10条开放任务。导出前核验Issue摘录、源码锚点和技能证据引用；已有人工复核表不会被覆盖。结果见各项目 `annotations/` 和 `data/ANNOTATION_REPORT.md`，复核口径见 `docs/ANNOTATION_GUIDE.md`。草稿均需独立人工复核，不自动成为标准答案。

## 单任务LangChain原型

已在项目 `.venv` 接入DeepSeek Flash并完成一次真实调用。运行：

```powershell
.\.venv\Scripts\python.exe analyze_issue.py --mode retrieve
.\.venv\Scripts\python.exe analyze_issue.py --mode analyze --prompt-key
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认样本为Manim #4983；Key由隐藏输入或 `DEEPSEEK_API_KEY` 读取，不放入命令行或文件。安装步骤、范围和限制见 [docs/PROTOTYPE_GUIDE.md](docs/PROTOTYPE_GUIDE.md)，本次结果见 `data/PROTOTYPE_REPORT.md`。模型分析仍需人工语义复核。

## 批量真实分析

```powershell
# 不花钱：只看12条任务的缓存命中/缺失情况
python batch_analyze.py --dry-run

# 真实调用DeepSeek：隐藏输入Key，逐条调用并记录结果与失败
python batch_analyze.py --prompt-key
```

固定任务清单见 `batch_tasks.json`（五项目各若干条开放具体任务）。输入未变的完整运行会缓存复用，不重复调用模型；结果写入各项目 `prototype_runs/`，收据写入 `analysis_cache.json`，批次汇总写入 `data/batch_runs/`。模型输出使用schema v2（技术栈、适合背景、新手阶段、开始步骤），语义仍需人工复核。

## Demo：技术栈匹配同项目GFI

```powershell
python recommend_gfi.py --repo ManimCommunity/manim --issue 4983 --top-k 3
```

将967条原始记录生成演示状态视图，用已有50条任务分析归一化技术标签，输出共同技术、新增技术、相似度与双侧证据；同项目内匹配，不涉及用户画像。详见 [docs/DEMO_MATCHING_GUIDE.md](docs/DEMO_MATCHING_GUIDE.md)。本流程只用标准库，模型调用0次。

## 当前界面入口

旧Streamlit界面已在2026-10-19维护重构中退役，统一使用 `python serve.py` 启动HTML门户。网页资源位于 `web/`，数据适配、已保存产物读取、对话和HTTP服务分别位于 `portal.py`、`artifacts.py`、`chat.py`、`portal_server.py`。详见 [当前代码结构](CODE_STRUCTURE.md)。

## 本地数据审计

```powershell
python audit_gfi_data.py
```

核验源码归档哈希，统计Issue/评论/PR摘要、项目资料、草稿复核和真实模型运行；输出 `data/data_audit.json`、`data/DATA_AUDIT.md`。只读项目数据，不调用网络或模型。

## 标准库流程验证

```powershell
python -m unittest discover -s tests -v
python collect_gfi.py --help
```
