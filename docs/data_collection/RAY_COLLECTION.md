# Ray GFI采集与五项目处理对齐

## 采集入口

```powershell
.\.venv\Scripts\python.exe collect_ray.py --prompt-tokens 1
```

在隐藏提示中输入可读取公开仓库的GitHub Token。脚本不写入或打印Token。也可使用已有进程环境变量GITHUB_TOKEN_1/GITHUB_TOKEN_2/GITHUB_TOKEN/GH_TOKEN。相同Token会去重，同账号额度不因Token数增加而保证增长。

默认项目仅为ray-project/ray，默认state=all，采集当前仍带good-first-issue等价标签的开放和关闭Issue，排除PR；不会重新采集原四项目。

```powershell
# 续采：重拉标签列表，复用完整且原Issue未变的bundle
.\.venv\Scripts\python.exe collect_ray.py --prompt-tokens 1

# 强制刷新评论和timeline；保留原固定源码版本
.\.venv\Scripts\python.exe collect_ray.py --refresh --prompt-tokens 1
```

## 项目目录

全部Ray原始与派生数据位于data/ray-project__ray/：

```text
ray-project__ray/
  repository.json
  manifest.json
  raw/label-*.json
  raw/label_verification_snapshot.json
  issues/<number>.json
  issues.jsonl
  issues.csv
  source_snapshot.json
  source-<commit>.zip
  source/
  label_audit.json
  label_audit.csv
  LABEL_AUDIT.md
  processed/
  annotations/
  retrieval/source_index.sqlite3
  prototype_runs/
```

原始bundle字段与其他项目一致：schema_version、repo、fetched_at、complete、issue、comments、timeline和related_pull_requests。全局Ray采集摘要单独保存为data/collection_summary_ray.json，避免覆盖原四项目摘要。

## 源码快照与缺失链接

源码固定在commit `3235a8e1e84de7c7fde217553c83e3c38d8560fe`，归档大小202,737,102字节，SHA256为`d54e618958a7857900e0f524e49d87fa902a1b791a40ad3779530ea32c4c12ea`。

源码为固定版本工作树，不是完整Git历史镜像，也不额外拉取子模块或LFS实体。Ray包含Python、C++、前端和文档材料，归档保留原有内容。

该归档存在一条缺失目标的内部链接：release/workspace_templates → ../doc/source/templates/。Ray入口显式允许把这种内部缺失链接保存为原始链接文本，不创建符号链接、不访问仓库外路径，也不伪造缺失目标内容。收据的missing_internal_link_handling与preserved_missing_internal_links记录这一差异；其他项目默认严格策略不变。

仓库外路径与循环链接仍拒绝。检索器跳过这种保留的链接文件，不把目标路径文本当作源码。完整性核验采用收据中相同策略。

## 标签核验

采集结束后重新分页查询同一标签，逐条核对：

- 汇总与每个单条记录是否仍保存GFI标签。
- 是否混入PR、重复编号、错误仓库或不完整bundle。
- 单条JSON与汇总是否一致。
- 导出与结束时标签查询集合是否有缺项或额外项。

标签检查结果见本项目LABEL_AUDIT.md与label_audit.json。通过仅表示采集窗口内保存标签和集合一致，不能恢复过去被移除标签的全部历史任务。

## 后处理对齐

```powershell
.\.venv\Scripts\python.exe clean_gfi.py
.\.venv\Scripts\python.exe prepare_annotation_drafts.py
.\.venv\Scripts\python.exe audit_gfi_data.py
.\.venv\Scripts\python.exe recommend_gfi.py --repo ray-project/ray --issue 38061 --top-k 3
```

在采集完整后执行。clean_gfi统一使用schema 3，保留真实原始状态、区分具体任务与汇总/指引；triage_overrides记录9条有摘录依据的Ray汇总帖，其中2条原始已关闭，不能把它们当作关闭具体任务。

annotation_drafts_ray.json新增10条助手草稿；五项目各10条，总计50条。每条有3项技能、适合背景、拟议验收、阻碍、未知项，以及Issue/源码/测试证据，格式与原四项目一致。旧40条草稿和已有人工复核表不改写；新增草稿不是模型调用结果或评测标准答案。

Ray样本覆盖：#11509、#26239、#30601、#38061、#39737、#40653、#42491、#50256、#52656、#57729。30个源码/测试锚点均按固定源码核对；部分原帖只有外部链接、截图或旧版本行为，未知项明确保留。

## 检索与Demo

```powershell
# 只检索，不调用模型
.\.venv\Scripts\python.exe analyze_issue.py --repo ray-project/ray --issue 38061 --mode retrieve
```

已建立与其他项目同格式的V2 SQLite索引：6167个文本文件、29716个片段。索引仍使用现有Python/文档后缀与1MB上限，不声称覆盖Ray全部C++、前端、Notebook或模板内容；完整源码仍在归档与source目录中。草稿可直接引用其他文本格式，但这些格式不因此自动进入检索索引。

技术规则版本为demo_technology_v2_ray，包含38个标签：新增Ray、Actor/ObjectRef、自动扩缩容、S3、asyncio、Jupyter显示、Click选项和Ray Tune。Ray/actor匹配使用词边界，避免array/factor等子串误命中；fsspec必须明确出现，不再由worker推断库依赖。

Demo继续统一假设全部GFI开放、未解决，不按关闭/指派/PR过滤；仅匹配同项目、非自身的具体任务。新增Ray分析不会与其他项目跨仓库匹配，也不引入个人画像。

新五项目Demo另建运行目录，旧四项目Demo及唯一Manim真实模型运行保留历史范围。本次Ray对齐不调用DeepSeek或其他模型，不执行下载仓库的代码或测试。

## 验收结果

最终数量、标签状态、清洗分类、评论数、时间线数、匹配结果和测试数量，以data/RAY_COLLECTION_REPORT.md及当前data/data_audit.json为准。该指南说明固定入口与格式，不用运行中进度代替完成结果。
