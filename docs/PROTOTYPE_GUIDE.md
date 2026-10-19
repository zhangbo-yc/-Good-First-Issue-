# 第④步：LangChain单任务证据分析原型

## 已实现范围

本地原始Issue/最近3条评论 → 固定commit源码ZIP → SQLite FTS5检索 → LangChain提示词与ChatDeepSeek调用 → Pydantic结构及引用校验 → JSON/Markdown输出。

统一CLI现支持SymPy、napari、Dask、Manim和ray-project/ray五项目。Ray同样可以使用retrieve模式，示例：`.\.venv\Scripts\python.exe analyze_issue.py --repo ray-project/ray --issue 38061 --mode retrieve`；它不会触发模型调用。

这是固定的单任务流水线，尚不包含自主补查循环。`--schema-version v2`（默认）输出新版技术栈/适合背景/新手阶段/开始步骤字段，v1仍可用以复现旧运行；批量入口见batch_analyze.py，同项目任务衔接与页面见recommend_gfi.py与serve.py。当前范围不需要用户画像；相关支线已清理。代码不读取第②步分析草稿和人工复核意见，也不执行下载项目中的代码。

## 环境与命令

当前依赖统一由根目录 `requirements.in` 和 `requirements.txt` 管理。当前研究工作流使用LangGraph，单任务原型入口保留以复现历史运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 只建立/复用索引并生成证据包，不调用模型
.\.venv\Scripts\python.exe analyze_issue.py --repo ManimCommunity/manim --issue 4983 --mode retrieve

# 真实调用：在隐藏提示中输入DeepSeek Key，不把Key放命令行
.\.venv\Scripts\python.exe analyze_issue.py --repo ManimCommunity/manim --issue 4983 --mode analyze --prompt-key

# 或使用进程环境变量DEEPSEEK_API_KEY，脚本不会自动读取.env
.\.venv\Scripts\python.exe analyze_issue.py --repo dask/dask --issue 9158 --mode analyze

.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

数据采集与清洗脚本仍可独立运行。未安装模型依赖时，标准库测试命令会跳过原型测试；当前验证结果见docs/SCOPE_CLEANUP.md，旧真实调用的运行记录保持原样。

## 模型和请求范围

按用户指定使用DeepSeek Flash。官方当前标识 `deepseek-flash`，地址 `https://api.deepseek.com`，通过 `langchain-deepseek` 接入。Key仅用于此服务鉴权，不进入模型提示词、源码和结果文件。未启用LangSmith追踪。

- JSON模式结合客户端Pydantic校验，不把JSON模式等同于服务端严格Schema保证。
- 每次输出最多4096 tokens；SDK自动重试为0，结构/引用失败最多额外尝试1次。
- 最多8条源码/文档/测试片段（可通过max-sources调整到1–16），Issue正文最多6500字符，最近3条评论各最多1200字符；总证据包字符数超过75000会拒绝调用。
- 思考模式关闭；没有绕过服务商限制、切换其他模型或发送到其他模型服务。

## 检索与证据

源码索引位于各项目 `retrieval/source_index.sqlite3`。索引从已验证SHA256的源码ZIP读取UTF-8文本，不使用可能被本地修改的工作树。每文件限1MB，跳过二进制、`.env`、AGENTS/CLAUDE及代理配置；因此不声称覆盖全部资产或全部语言形式。

当前Manim V2索引含452个文本文件、2190个片段；Ray V2索引含6167个文本文件、29716个片段。两者均跳过符号链接别名，只引用实际文件；Ray归档中的一条内部缺失目标链接以文本保留并记录于收据，该链接不进入索引。首次Manim真实调用的V1索引为453文件、2195片段，其选中证据均非别名，已用V2逻辑复核，未追加模型调用。检索采用80行窗口、60行步长和FTS5，不使用向量库。结果尽量保留测试，但词法相关不保证语义相关。首个模型结果仍缺少父类目标方法片段，后续可增加精确符号定位和补查。完整Ray归档也包含C++/前端/Notebook/模板，当前索引支持后缀不代表所有格式都已索引。

自动校验包括：归档哈希、引用片段与归档行号/文本/文件哈希一致、JSON结构合法、证据ID存在，以及模型所列路径与其引用的源码位置一致。它不证明引用真正支持语义结论，输出始终为待人工复核草稿。

## 输出

每次运行创建新目录，不覆盖已有分析：

```text
data/<owner>__<repo>/prototype_runs/<UTC时间>_<Issue>_<模式>_<随机后缀>/
  evidence_bundle.json
  analysis.json             # 仅真实分析成功后生成
  ANALYSIS_REPORT.md        # 仅真实分析成功后生成
  run_manifest.json
```

`evidence_ready_no_model_call`只代表证据已就绪。`analysis_ready_pending_human_review`代表真实模型输出已通过结构/引用检查。失败保留failed状态，不伪装成完整分析。`elapsed_seconds`为整个运行耗时，可能包含索引检查和等待隐藏输入，不是纯推理耗时。

结果包括任务目标、技能及其理由、相关文件、验收建议、阻碍、未知项和证据；推断与明确要求分别标记，语义仍需人工核查。最新认领、PR、维护者意图及测试运行结果未核验，不能直接判断可接手或已修复。

## 本次真实调用

Manim #4983，源码commit `622520d34190062f32d68af066c80f1c937fc8bf`。一次实际DeepSeek请求成功，无结构修复重试；输入9552、输出1117、合计10669 tokens。未估算费用，未运行项目代码。

官方参考：
- https://api-docs.deepseek.com/zh-cn/
- https://docs.langchain.com/oss/python/integrations/chat/deepseek
- https://docs.langchain.com/oss/python/langchain/structured-output
