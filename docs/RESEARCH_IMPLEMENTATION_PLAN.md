# 科研方法改进实施方案

> **状态：历史实现计划。** 原计划要求人工 Gold 和双评审作为效果结论门槛；当前参赛方案改为模型辅助的探索性评测，不进行人工核验。当前样本划分、评审方法和结果以 [docs/RESEARCH_METHOD.md](RESEARCH_METHOD.md) 与 `experiments/frozen_20261017/final_heldout_v3/` 为准。本文件保留原始实现决策，不代表当前评测结论。

用户已批准上一轮方案并明确要求前端功能不变。本轮只新增独立研究后端、CLI、数据冻结与评测工具，不实施之前待确认的旧文件清理/前端重构。

## 固定约束

- `web/*`、`serve.py`、`portal.py`、`portal_server.py` 内容哈希保持不变；现行967条中文说明、50条分析和推荐缓存不覆盖。
- 新的批量模型调用仅使用DeepSeek，凭据从现有.env加载，不进入输出、日志、命令参数。
- 新产物放入`experiments/`，不能当作现有产品缓存或人工标准答案。
- 评价的是采集时固定源码快照上的任务理解，不声称历史修复预测或真实学习进阶；所有条件排除评论，避免讨论中的解决线索不公平泄露。
- 人工评测与独立复核不能由模型代填；缺少标注的效果指标必须为空/待评审，不能记为0或生成虚假提升。

## 实施任务

1. 冻结45条未用于开发的具体任务：Manim、SymPy、Dask、Ray各10条，napari5条。保存随机种子、分层、排除的50个开发ID、原始材料/源码SHA、规则哈希和不可覆盖的协议；提供验证与人工标注模板。
2. 结论证据审查：抽取目标、技术要求、新手阶段、验收条件为claim ledger；DeepSeek逐项判定sufficient/partial/insufficient，给出依据和具体缺口。引用必须属于该claim原本引用的材料；评论不作为充分证据。检查器自身不是人工真值。
3. 有限补查：对缺口使用FTS5补查函数/文件/测试，补入经过快照验证的新片段并重新分析。最多2轮补查；无新证据、模型调用/上下文/观测token预算耗尽时停止。保留原分析、每轮packet、审查结果和停止原因；没有支持的要求保留为未知。
4. 方向性推荐：保留随机、原加权Jaccard基线；新增B侧技术覆盖、绝对新增技术负担/新模块负担、机器证据审查质量的透明分数组合。避免把两个互补比例当成两个独立贡献；缺审查记录不假设质量为1。相同项目、相同冻结候选池、排除自身/指引；输出共同/新增知识及逐项分数，不称成功率。
5. A–D消融运行器：A仅Issue；B固定检索+结构引用校验；C=B+结论证据审查；D=C+定向补查。共45×4个独立缓存运行。相同模型、分析提示/schema和上下文上限，记录实际调用次数/耗时/usage；C/D审查与补查的额外成本单独报告，不能宣称总预算相同。
6. 盲评与报告：提供独立gold标注模板、两评审盲评材料、私有方法映射与指标统计。报告要求准确性、证据支持率、需求覆盖率、阶段合理性、推荐合理性、评审一致性；缺标注仅报告技术运行情况。选择失败/拒答案例供人工查看，不编造结果。
7. 测试与集成：先红后绿验证证据ID/审查覆盖/不充分门控、无进展停止、预算、快照篡改、方向性、缺标注、断点缓存和前端哈希。运行现有全量测试、独立代码复核、真实DeepSeek试运行后开展冻结实验。

## 文件职责与接口

- `gfi_prototype/research/protocol.py`：`freeze_protocol(root, output_dir, seed=20261017) -> dict`、`verify_protocol(root, protocol) -> None`；协议包含`tasks`（task_id/repo/issue_number/title/stratum/issue_sha256/source_commit）和`candidate_pool`（task_id列表）、`source_snapshots`、`rules_sha256`。
- `gfi_prototype/research/directional.py`：`rank_directional(source, candidates, quality_by_task, top_k=3, weights=None) -> list`、`rank_random(source,candidates,top_k=3,seed=20261017) -> list`；输入沿用现行technology profile。quality_by_task值0..1来自审查，未知缺失不排名。固定默认权重reuse=.5,burden=.3,evidence=.2；新增负担使用绝对权重/固定容忍量6与新增模块，不能只用1-reuse。
- `gfi_prototype/research/evidence.py`：`extract_claims(analysis) -> list`、`audit_analysis(analysis, packet, session) -> dict`、`gate_analysis(analysis, audit) -> dict`、`quality_score(audit) -> float|None`。Claim{id,kind,text,evidence_ids,basis}；Audit{reviews:[{claim_id,status,reason,evidence_ids,missing_information,retrieval_query}],...}；Gated{goal,requirements,stage,acceptance,unknowns,withheld_claim_ids}。
- `gfi_prototype/research/retrieval.py`：`SnapshotRetriever(root,repo)`、`initial(issue_number,issue_only=False)`、`supplement(packet,queries,limit=4)`。包中的证据ID保持唯一，排除评论并核验源码快照。
- `gfi_prototype/research/client.py`：DeepSeek transport与预算session；`session.request(messages, validator, purpose) -> parsed`。每次真实调用独立计数、记录usage与错误类型，失败不泄露原响应或凭据。
- `gfi_prototype/research/pipeline.py`：`run_variant(packet,retriever,session,variant,max_rounds=2) -> dict`，输出`analysis`、`audit`、`gated`、`rounds`、`stop_reason`与资源记录。
- `gfi_prototype/research/evaluation.py`：`build_review_bundle(root,protocol,results,output_dir)`与`score_reviews(...)`。结果列表每项{task_id,variant,analysis,gated,audit,resources,status,run_id}；推荐结果单独记录method/source_task_id/recommendations。盲评材料不包含variant/方法分数/机器审查标签；映射私有。
- `research.py`：freeze/verify/run/recommend/review/report命令。需要真实网络的命令显式执行，其余均离线。

## 当前实施记录

已实现冻结协议、A–D管线、逐结论证据审查、两轮定向补查、方向性推荐、随机/Jaccard基线、资源记录、盲评材料与分组报告。Manim #2379已完成真实DeepSeek试运行，Manim的10个固定候选已完成证据审查。独立Gold与双人评审仍为空，科研效果结论保持pending。当前状态见`experiments/frozen_20261017/STATUS.md`。

独立复核发现的目标引用丢失、模型缓存身份、盲评包过期、条件泄露、未知模块负担、空推荐、Gold缺失及失败调用成本八项问题均已修复并加入回归测试。本轮实现未修改前端或产品服务文件，也未把研究输出发布到门户。
