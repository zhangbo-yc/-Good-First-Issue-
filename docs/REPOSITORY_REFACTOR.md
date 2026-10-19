# 开源仓库结构重构说明

日期：2026-10-19。此次整理仓库结构、启动与分发方式，不改变网页内容、研究方法或既有结果。

## 修改结果

| 位置 | 作用 |
| --- | --- |
| pyproject.toml / MANIFEST.in | 标准Python包元数据、依赖、命令及分发边界 |
| gfi_prototype/cli.py | 统一启动参数、数据根目录与网页资源定位 |
| gfi_prototype/__main__.py | 支持python -m gfi_prototype |
| serve.py | 保留原有命令及PyCharm启动方式的薄入口 |
| scripts/run.py | 分类启动采集、分析、实验和报告脚本，不复制其实现 |
| .github/workflows/checks.yml | 不依赖外部数据、不调用模型的自动检查 |
| .github/ISSUE_TEMPLATE / pull_request_template.md | 问题与改动记录模板 |
| CONTRIBUTING.md / SECURITY.md | 开发、验证、凭据及本地服务说明 |
| docs/README.md | 文档导航 |
| docs/DATA_AND_REPRODUCIBILITY.md | 数据包、实验复现与源码分发说明 |
| docs/data_collection/ | 原根目录两份项目采集记录 |
| docs/design/original-requirements.md | 原report-function.md，内容原样归档 |

README、RUN_GUIDE和代码结构文档同步更新。根目录PPT、JSON配置、旧研究脚本保留原位。

## 为什么仍保留部分根目录脚本

本项目沿用标准flat package布局，运行源码集中在gfi_prototype。研究脚本互相导入，部分源码路径和哈希参与冻结身份。机械搬到src或scripts会破坏缓存兼容性，因此保留历史接口，并通过scripts提供分类导航和启动。新的包CLI不改写研究脚本。

## 启动与安装

```powershell
.\.venv\Scripts\python.exe serve.py
# 或
.\.venv\Scripts\python.exe -m gfi_prototype
```

可选开发安装：先安装requirements.txt，再执行`python -m pip install --no-deps -e .`，随后运行`gfi-portal`。支持`--root`指定其他数据目录。

构建的wheel包含Python包、网页资源及检索所需collect_gfi兼容模块；不包含密钥、数据或实验缓存。真实数据仍需独立提供。此次构建包保存在本地docs/maintenance/wheels，未上传、发布或创建远程仓库。

## 验证

- 重构前281项Python测试通过，重构后289项通过；新增包CLI、启动器状态恢复和包依赖覆盖测试。
- 前端6项SSE回归检查通过，网页文件内容未修改。
- GitHub Actions选用的27项无数据离线测试在本机通过。尚未在GitHub托管执行器上运行。
- wheel构建与隔离安装通过；验证源码旧入口、源码模块入口、wheel模块和命令入口，以及仅使用wheel内置网页资源的独立工作目录，共5种启动场景。
- 有数据场景实际访问首页、CSS/JS、任务目录和资料助手SSE；没有调用DeepSeek。
- 65个受保护源码、配置、网页及PPT的SHA256一致。
- 19,244份数据/实验文件的路径、大小和修改时间一致；没有对全部数据重新计算内容哈希。
- 项目列表、任务列表和50个任务详情的规范JSON签名一致。
- pip check通过。独立复核发现的外部数据目录资源回退问题已补测试修复；隔离安装发现的根模块依赖遗漏也已补齐并验证。

临时构建目录、egg-info及隔离安装测试环境已移入回收站；原有.venv没有修改。基线、日志及启动记录在本地docs/maintenance，该目录被Git忽略。

## 公开前仍需确认

项目尚未选定源码许可证，本次未擅自授予许可。公开仓库不自动意味着可任意复用。数据、上游源码、实验缓存与本机密钥的分发范围由对应材料与许可约束；.gitignore和MANIFEST已将它们与源码包分开。

原有34题汇总脚本依赖的历史PPT缺失问题仍按README披露，本次未伪造或更换历史追溯文件。
