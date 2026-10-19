# 贡献指南

## 本地开发

使用Python 3.11，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m gfi_prototype
```

编辑安装提供`gfi-portal`命令；在未激活虚拟环境时，Windows可运行`.venv\Scripts\gfi-portal.exe`。不需要编辑安装也能使用原有`python serve.py`。

## 修改边界

- 产品代码位于`gfi_prototype/`，研究实现位于其`research/`子包，网页资源位于`web/`。
- 研究CLI与配置保留原路径，原因见[scripts说明](scripts/README.md)。不要为整理目录直接改写冻结路径、提示词或缓存身份。
- 更改研究方法时使用独立版本与输出目录，保留原实验答案和评分。
- 界面不展示原始密钥或模型服务异常，不将模型评分表述为人工准确率。

## 检查

```powershell
python -m unittest tests.test_package_metadata tests.test_cli tests.test_script_launcher tests.test_artifacts tests.test_storage tests.test_origins tests.test_settings tests.test_research_pipeline
node tests/test_portal_frontend.mjs
```

上述检查不依赖外部数据包，不调用模型。GitHub Actions运行同一组检查。完整验证需要本地数据包：

```powershell
python -m unittest discover -s tests
```

提交PR时说明问题、改动及实际验证结果。涉及模型的验证需自行配置密钥并明确成本；不要在自动检查中调用真实服务。

## 数据与发布

数据、上游源码快照、实验缓存及API密钥不随Python安装包发布。分发方式见[数据说明](docs/DATA_AND_REPRODUCIBILITY.md)。项目尚未声明开源许可证；提交公开仓库前由权利人确认许可证，上游材料继续遵守各自许可。
