# Demo：按GFI技术栈匹配同项目后续任务

## 演示规则

按用户要求，全部已采集GFI在演示中统一设为开放、未解决。关闭、指派、认领和PR信息均不参与过滤，也不再要求联网补查状态。原始Issue和清洗结果保留真实快照字段，演示状态单独记录：

```json
{
  "demo_assumption": true,
  "effective_state": "open",
  "effective_resolution": "unresolved",
  "status_used_for_filtering": false
}
```

没有个人画像或用户能力评分。完成A后看B，仅表示A、B的需求技术有交集，不声称已验证知识掌握或难度递进。

## 执行

```powershell
python recommend_gfi.py --repo ManimCommunity/manim --issue 4983 --top-k 3
python recommend_gfi.py --repo dask/dask --issue 3406 --top-k 3
```

Python 3.10+标准库即可，不调用模型或GitHub，不需要Token。默认起点是Manim #4983。起点必须有已生成的任务分析；没有分析时明确报错，不补造技能。

## 输入与匹配

- 五项目issues.jsonl及processed/triage.jsonl：967条原始记录，945个具体任务、22条指引/汇总；全部生成演示状态视图。
- 五项目annotations/task_analysis_drafts.jsonl：50条已有助手分析草稿；各项目analysis_cache.json中的真实模型分析经统一适配器并入同一候选池，同一任务模型档案优先。
- source_snapshot.json：核对草稿源码版本。
- technology_rules.json：将每项原始技能名称与理由映射到统一技术/知识标签。每个标签保留原技能位置和证据编号，不读取个人信息，也不从不相关Issue文字猜标签。

技术归一化当前采用38条透明规则，既包含语言、库、框架、工具，也包含面向任务的知识概念。权重：通用Python/测试/pytest/环境为1，知识概念为2，专用库/框架/工具为3。权重属于演示参数，尚未通过独立实验调优。未匹配的原始技能通过unmapped_requirement_indexes保留，不隐藏覆盖不足。

只比较同项目、不同编号的具体任务；按加权Jaccard排序：

```text
相似度 = 共同技术标签的权重之和 / 两任务全部技术标签并集的权重之和
```

排序使用精确分数，相同分数再比较共同专业标签数量、任务ID，保证可复现。只有通用标签重合时标记generic_only；没有交集则不推荐，不为凑三个结果补任务。未经过状态过滤。

## 输出

每次创建data/demo_runs/<UTC时间_随机ID>/，不覆盖此前运行：

| 文件 | 内容 |
| --- | --- |
| demo_catalog.jsonl | 967条记录的演示状态和原始状态，明确区分具体任务和指引 |
| task_technology_profiles.jsonl | 50条任务的标签、原始要求、适合背景和证据 |
| recommendations.json | 指定A及其同项目B候选、相似度、共同技术、新增技术和双侧证据 |
| all_task_matches.jsonl | 50条已分析任务各自最多3个匹配结果，方便切换演示起点 |
| DEMO_REPORT.md | 指定A的可阅读演示报告 |
| run_manifest.json | 最后写入的成功标记、规则版本、输入/代码哈希、范围与调用计数 |

每个推荐保留A、B各自的证据URL，不能把同名I1证据跨任务混用。新增技术只表示规则发现B有而A没有的标签，不能据此保证没有其他知识差异。数值相似度不是成功率。

## 当前边界

分析起点来自既有助手草稿和已保存的真实模型分析；草稿与模型结果通过`analysis_origin`区分，报告与页面都带来源徽标。另外895个具体任务尚无归一化分析，不参加匹配。旧的v1 Manim真实模型结果保持原样，没有把旧草稿映射冒称模型输出。

真实模型分析采用schema v2（技术栈、适合背景、新手阶段、开始步骤），由gfi_prototype/unified.py适配到匹配层，匹配结果按18.5设计使用三级缓存。自主补查仍可后续补齐。实时状态/PR核验、全历史贡献数据和复杂评测不是本轮Demo的前置任务。
