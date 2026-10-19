# 命令与兼容入口

`run.py`按用途提供统一入口，执行的仍是根目录原脚本。这样可以集中查找命令，同时保持冻结代码路径、哈希和原有PyCharm运行方式。

```powershell
python scripts/run.py --help
python scripts/run.py collect -- --help
python scripts/run.py analyze -- --help
python scripts/run.py research -- --help
```

| 类别 | 命令 |
| --- | --- |
| 采集/清洗/审计 | collect、collect-dask-manim、collect-ray、clean、audit |
| 准备产品资料 | prepare-annotations、prepare-content、prepare-guides |
| 分析/推荐 | analyze、batch、recommend |
| 实验/模型评审 | research、experiment、control、review、review-control |
| 报告/工作流导出 | report-ablation、report-control、export-workflow |

实际参数在命令后传入，可用`--`分隔。启动器始终以仓库根目录为工作目录，因此相对数据/输出路径也以仓库根目录为准。命令继承原脚本的网络调用行为；运行实验、内容生成或评审前确认API费用。仅查看支持的`--help`不会调用模型。

`report-control`仍受历史PPT追溯文件缺失的限制，见README复现说明。

## 为什么保留根目录脚本和配置？

根目录研究脚本互相导入，部分实验还记录它们与`technology_rules.json`的原始路径及哈希。将它们全部挪入scripts会使已有付费请求缓存与冻结协议失配。因此原脚本是兼容接口，不另复制一份实现，不替换成会改变源码哈希的占位文件。`scripts/`不随运行wheel安装；研究复现使用完整源码仓库与数据包。
