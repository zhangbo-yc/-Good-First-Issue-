# 第②步任务分析与复核规范

本批样本为SymPy、napari、Dask、Manim和Ray各10条开放具体任务，共50条。样本是为了覆盖不同任务和证据状态而选择的开发/试标样本，不是随机样本，也不是最终留出的测试集。原四项目40条草稿保留；Ray新增草稿单独维护在annotation_drafts_ray.json，Dask/Manim草稿仍在annotation_drafts_additional.json。

## 当前草稿的身份

分析由助手阅读Issue、评论、源码和测试后编写；不是独立人工研究数据。所有记录默认 `human_review_status=pending`、`is_ground_truth=false`。技能要求通常是根据工作内容推断，需逐项复核；不能把“出现某依赖”自动当作必须精通该依赖。

`acceptance_draft` 是待维护者/评审确认的验收建议，不能冒充作者已经认可的验收标准。`source_observation` 仅描述固定commit的静态证据；没有执行项目测试，也没有验证当前任务认领和PR状态。

## 证据

- I1：Issue原文中的任务要求；保留URL、快照时间、原始文件哈希和摘录。
- C1等：所选关键评论；保留作者、关联角色、评论链接和摘录。评论观点仍需结合后续讨论判断。
- S1/S2等：固定commit的源码或项目文档；保留路径、行号、内容和文件SHA256。
- T1等：关联测试文件。它只说明有可参考的测试，不表示该测试能完整复现或覆盖本任务。

证据URL必须固定源码commit。旧Issue路径与源码路径不一致时，记录迁移；找不到内容时明确未知，不猜测路径。PR引用不代表解决PR，更不代表已合并。

## 独立人工复核

使用每仓库 `annotations/review_form.csv`。评审填写 reviewer_id、技能要求判断、代码关联判断、目标与验收判断、阻碍与未知项判断、总体建议和备注。

建议两位评审独立复核至少一部分共同样本，先保存各自原始意见，再讨论分歧。评审可将每一维填为 `agree`、`partly_agree`、`disagree` 或 `uncertain`。代码位置能打开不等于语义结论正确。

当前导出脚本不会自动把人工表格中的文字转成最终标准答案，也不会把助手草稿标为“人工已通过”。需要人工确认每项证据是否支持结论、哪些技能是必要条件、哪些只是可补充学习的知识。

## 第一批样本覆盖

- SymPy：文档与绘图、矩阵索引、Autolev代码生成、lambdify命名、Fortran打印、集合与丢番图、离散概率、缓存测试。
- napari：Points API、Shapes插值、标签笔刷、文件堆叠、设置迁移、图层空间变换、测试数据缓存、颜色状态、欢迎界面API、点投影。
- Dask：HDF5写入、零维索引、分块展平、日期归约、合并索引、远程CSV、测试排序、QR接口、表达式可视化、字符串dtype。
- Manim：Table接口、TikZ/TeX、单位文档、缩放场景、箭头透明度、表格布局、曲线边界、Notebook场景配置、对象排列、继承异常。
- Ray：CLI停止范围、GCP节点批量终止、S3运行环境端点、ActorPool返回值、Arrow迭代输入、入门计时示例、资源小数显示、State API认证选项、Tune主题显示和asyncio批处理。

关闭具体任务保存在historical_tasks.jsonl；原始关闭的汇总/指引仍保存在non_task_records.jsonl，因此原始关闭数量不一定等于历史具体任务数量。关闭不等于成功修复，精确数量见当前cleaning_summary.json。该批50条草稿全部从原始开放具体任务中选择；Demo另行统一覆盖所有状态。

后续评测应另选未用于提示词和规则调整的任务；目前覆盖五个以Python为主要接口的仓库，Ray也包含C++和前端代码，结果仍不能外推到所有GitHub项目。
