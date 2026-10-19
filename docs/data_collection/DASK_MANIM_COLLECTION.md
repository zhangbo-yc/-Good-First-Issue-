# Dask / Manim 采集与标签核验

当前标注流程暂停。本入口只采集 `dask/dask` 和 `ManimCommunity/manim`。

```powershell
cd D:\haikesong-------bisai
python collect_dask_manim.py --prompt-tokens 1
```

使用上下文中已提供的Token时，由隐藏终端输入进入内存，不写入脚本或输出。以后也可以设置进程环境变量 `GITHUB_TOKEN_1` 后直接运行。重复Token只算一个凭据。

默认 `--state all`：收集当前仍带GFI标签的开放与已关闭Issue。脚本先分页读取仓库真实标签，精确匹配 `good first issue` 和 `good-first-issue`（忽略大小写），保存实际标签名称。不会将help wanted、easy或仅包含相似词的标签当作GFI，也不能恢复历史上被移除的标签。

两个项目并行运行，每项目4个任务详情工作线程，重复凭据共享节流与限流等待。所有分页均读取；正文、评论、时间线与关联PR摘要沿用现有采集器。源码下载默认分支固定commit的完整工作树，保留ZIP和校验记录；不是包含所有分支和提交历史的Git镜像，不额外下载LFS实体/子模块。不会执行项目代码。

```text
data/
  collection_summary_dask_manim.json
  dask__dask/
    repository.json
    manifest.json
    raw/
    issues/*.json
    issues.jsonl
    issues.csv
    source_snapshot.json
    source-<sha>.zip
    source/
    label_audit.json
    label_audit.csv
    LABEL_AUDIT.md
  ManimCommunity__manim/
    ...同上...
```

结束后重新分页查询GFI Issue列表，并检查每个保存记录、所有单条文件、PR混入、重复、遗漏、标签及本次集合一致性。JSON和CSV都有逐条/汇总核验结果；标签变化、缺页、请求失败等会标记partial并返回非零退出码。

Windows源码解压会将仓库内部符号链接安全复制为普通文件或目录（例如Manim的行为准则链接、Dask的配置目录别名），无需管理员权限。仓库外目标、失效目标和循环链接会被拒绝；处理方式记录在源码快照清单中。

重复运行可以断点续采。原SymPy/napari的目录和汇总文件不会被该入口替换。只需开放任务时可显式传 `--state open`；小规模联调可用 `--limit 1 --no-source --output .smoke-data-dask-manim`，此时会明确标记limited_run，不能当作全量数据。
