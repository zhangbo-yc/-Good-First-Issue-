# 环境安装与运行说明

本文只讲两件事：装环境、跑起来。原有入口仍为`serve.py`，也支持`python -m gfi_prototype`。

## 1. 环境要求

- 操作系统：Windows（已在 Windows 上验证，其他系统命令略有差异）
- Python：3.11.8（推荐，已验证）；3.10+ 理论上可用
- 网络：运行本项目不需要联网；只有使用 DeepSeek 相关功能时才需要网络和 API Key
- 数据：项目根目录下的 `data/` 文件夹必须和代码一起提供。它被 `.gitignore` 忽略，不会随 Git 自动分发，请直接从共享位置整体拷贝

## 2. 安装环境

在项目根目录打开 PowerShell，依次执行：

```powershell
cd D:\haikesong-------bisai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.in` 记录7项直接依赖，`requirements.txt` 固定44项运行依赖（包含传递依赖）。旧Streamlit界面已移除，不再需要安装Streamlit。

本地已有 `.env` 会由 `serve.py` 自动读取。新环境请参考 `.env.example` 配置自己的 `DEEPSEEK_API_KEY`；离线浏览和资料助手不需要Key。公开提交与分发时不要包含 `.env`。

## 3. 运行

```powershell
.\.venv\Scripts\python.exe serve.py
```

启动后浏览器打开：http://127.0.0.1:8503

停止服务：在终端按 `Ctrl+C`。

端口被占用时指定其他端口：

```powershell
.\.venv\Scripts\python.exe serve.py --port 8504
```

## 4. 常见问题

- `Activate.ps1` 被禁止执行：先运行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，或直接用完整路径 `.\.venv\Scripts\python.exe` 执行后续命令。
- 页面打开是空白：确认 `data/` 目录已随项目拷贝，且位于项目根目录下。
- 8503 端口打不开：检查防火墙提示，或用 `--port` 换一个端口。

## 5. 开发安装

先安装requirements.txt，再执行`python -m pip install --no-deps -e .`，可使用`gfi-portal`命令。数据目录独立分发，详见[数据与复现](docs/DATA_AND_REPRODUCIBILITY.md)。原来的PyCharm运行serve.py方式不变。
