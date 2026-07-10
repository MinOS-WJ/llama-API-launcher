# llama.cpp API 启动器

基于 FastAPI 的 `llama-server` 管理工具，通过浏览器网页完成模型加载、参数配置、服务启停与版本更新。支持本地与远程管理，无需命令行，适配服务器环境。

## 核心价值

- **轻依赖**：仅 `fastapi` + `uvicorn` 两个第三方库，`pip install -r requirements.txt` 即可
- **Web 管理**：浏览器界面，支持本地和远程服务器管理
- **服务端目录浏览**：远程环境下可视化选择目录与文件，无需手动输入路径
- **模块化设计**：核心逻辑（`core/`）与 UI（`web/`）完全分离
- **配置即数据**：所有参数集中在 JSON 文件，代码不内嵌硬编码

## 功能特性

### 服务启停管理
- 选择 `.gguf` 模型文件（支持一级子目录）
- 选择参数方案（`cpu` / `gpu` / `mix` 等）
- 启停合一按钮，一键启动 / 强制停止 `llama-server`
- 实时日志输出（SSE 推送，500 行上限自动裁剪）
- 状态自动同步（运行时每 3 秒轮询，停止后停止轮询）
- 进程异常退出自动检测（SSE 推送返回码，前端自动更新状态）

### 服务端目录浏览器
- `GET /api/browse` 端点列出服务器文件系统目录
- 前端弹窗可视化浏览：逐级进入、回退上级、刷新
- 支持选择目录（llama.cpp 目录、模型目录）或文件（方案文件）
- Windows 枚举盘符，Unix 以 `/` 为根
- 仅显示目录与相关文件（`.gguf` / `.json` / `llama-server*`），减少噪音
- 权限错误安全处理，不阻断浏览

### 参数方案系统
- 每个 JSON 文件可包含多套方案（`cpu` / `gpu` / `mix`）
- 支持切换 `configs/` 目录下不同方案集文件
- 方案参数覆盖 llama-server 所有常用选项

### 版本管理
- 从 GitHub（`ggml-org/llama.cpp` releases）拉取官方预编译包
- 按平台筛选（Windows / Linux / macOS / 其他），前端本地过滤避免重复 API 请求
- 下载并自动解压替换更新，进度条连续不回退（下载 0-80% + 安装 80-100%）
- 备份旧版本到 `_backup` 目录
- 更新前检测 llama-server 是否运行，避免覆盖被锁定的文件

### 配置持久化
- 配置自动保存到 `llama_launcher_config.json`（与 `main.py` 同目录）
- 记住上次选择的模型和方案
- llama-server 监听地址和端口独立配置

## 快速开始

### 运行方式

```bash
# 安装依赖（仅需 fastapi + uvicorn）
pip install -r requirements.txt

# 本地访问（默认 127.0.0.1:8686，自动打开浏览器）
python main.py

# 远程访问（允许局域网/外网连接，不打开浏览器）
python main.py --host 0.0.0.0 --port 8686 --no-browser

# 指定端口
python main.py --port 9090
```

> 注意区分两个端口：
> - **Web 管理端口**：`main.py` 的 `--port`，默认 `8686`，访问管理界面
> - **llama-server 端口**：界面中"监听地址/端口"设置，默认 `127.0.0.1:8080`，推理 API 服务端口

### 使用步骤

1. 启动后浏览器打开 `http://localhost:8686`
2. 在 **路径设置** 中点击"浏览…"选择 llama.cpp 目录（含 `llama-server`）与模型目录
3. 点击"方案集"从 `configs/` 选择适合本机的方案文件，或"选择…"浏览其他方案文件
4. 在 **运行配置** 中选择模型与方案，设置 llama-server 监听地址和端口
5. 点击顶栏 **▶ 启动** 开始推理服务，日志区实时显示输出

## 项目架构

### 目录结构

```
llama-API-launcher/
├── main.py                      # 入口：参数解析、端口检测、浏览器打开
├── requirements.txt             # Python 依赖（fastapi + uvicorn）
├── .gitignore                   # 忽略 __pycache__、运行时配置、备份
├── README.md
├── DEVELOPMENT.md               # 开发者文档（架构细节、陷阱、扩展指南）
├── LICENSE
├── llama_launcher_config.json   # 运行时配置（自动生成，已 gitignore）
├── assets/
│   └── icon.ico                 # 窗口图标
├── configs/                     # 参数方案集目录
│   └── default.json             # 默认方案集（cpu/gpu/mix）
├── core/                        # 核心逻辑模块（无 UI 依赖）
│   ├── __init__.py
│   ├── paths.py                 # 路径检测、模型枚举、目录浏览
│   ├── profiles.py              # JSON 方案的加载/保存/枚举
│   ├── launcher.py              # 命令构建 + 子进程封装（ServerRunner）
│   └── version_manager.py       # GitHub 发布解析、下载、解压替换
└── web/                         # Web 应用
    ├── __init__.py
    ├── app.py                   # FastAPI 应用（路由、SSE、静态托管）
    └── static/
        └── index.html           # 前端界面（单页应用）
```

### 模块职责

| 模块 | 职责 | 关键类/函数 |
|------|------|-------------|
| `core/paths.py` | 路径检测、模型枚举、目录浏览 | `detect_llamacpp()`, `list_model_files()`, `browse_directory()` |
| `core/profiles.py` | 参数方案管理 | `ProfileManager`, `load_json()`, `save_json()` |
| `core/launcher.py` | 命令构建与进程管理 | `build_command()`, `ServerRunner` |
| `core/version_manager.py` | GitHub 版本管理 | `fetch_releases()`, `download_file()`, `install_asset()` |
| `web/app.py` | HTTP API 服务（FastAPI） | `app`, `run_server()`, `init_app()` |
| `web/static/index.html` | 前端界面 | 单页应用，含所有 UI 逻辑 |

### ServerRunner 类

封装 `llama-server` 子进程的启停与日志读取：

- **启动** `start(cmd)` — 创建独立进程组（`CREATE_NO_WINDOW` + `CREATE_NEW_PROCESS_GROUP`），后台线程逐行读取输出
- **优雅停止** `stop()` — Windows 用 PowerShell `CloseMainWindow`，非 Windows 发送 `SIGTERM`
- **强制停止** `force_stop()` — Windows 用 `taskkill /T /F /PID` 杀进程树，非 Windows `proc.kill()`
- **状态查询** `running` 属性 — 通过 `proc.poll()` 判断
- **日志读取** `drain()` — 取出队列中的日志事件 `[(kind, data), ...]`，kind ∈ `out` / `rc`

### Web 架构

- **HTTP 服务器**：FastAPI + uvicorn（ASGI），阻塞型端点声明为普通 `def` 自动跑 anyio 线程池，SSE 长连接为唯一 `async def`，不阻塞其他请求
- **路由分发**：FastAPI 路由装饰器（`@app.get`/`@app.post`），未知 `/api/*` 由兜底路由返回 JSON 404
- **静态托管**：`StaticFiles` 挂载在 `/`（最后注册，API 路由优先匹配），`html=True` 自动服务 `index.html`
- **实时日志**：SSE（Server-Sent Events）经 `StreamingResponse` 推送，`queue.Queue` 收集日志，生成器 `await asyncio.sleep` 让出 CPU
- **状态同步**：前端运行时每 3 秒轮询 `/api/status`
- **优雅退出**：`_shutdown_event` 通知 SSE 生成器退出 + `server_instance.should_exit=True` 触发 uvicorn 关停；`lifespan` 统一清理子进程
- **连接容错**：SSE 生成器捕获 `asyncio.CancelledError`/`GeneratorExit`；客户端断开不产生 traceback

## API 接口

### 配置与目录

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/config` | GET | 获取当前配置（含 llama.cpp 检测状态） |
| `/api/config` | POST | 更新配置（llamacpp_dir / model_dir / host / port） |
| `/api/models` | GET | 列出模型文件 |
| `/api/profiles` | GET | 列出参数方案名 |
| `/api/profiles/path` | POST | 设置方案文件路径 |
| `/api/configs/list` | GET | 列出 configs/ 下方案集文件 |
| `/api/configs/load/{filename}` | POST | 加载指定方案集 |
| `/api/browse?path={path}` | GET | 浏览服务端目录（path 为空返回根） |

### 服务控制

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 获取服务状态（running / pid / command） |
| `/api/start` | POST | 启动 llama-server |
| `/api/stop` | POST | 停止服务（支持 `force` 参数） |
| `/api/logs` | GET | SSE 日志流 |
| `/api/shutdown` | POST | 关闭整个应用 |

### 版本管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/releases?os={os}` | GET | 获取 GitHub 发布列表（返回 `os_group` 字段） |
| `/api/update` | POST | 开始下载更新（需先停止 llama-server） |
| `/api/update/progress/{id}` | GET | 查询更新进度 |
| `/api/platform` | GET | 获取当前平台信息 |

## 参数方案格式

每个 JSON 文件顶层为方案名到方案字典的映射，常见方案名 `cpu` / `gpu` / `mix`。

### 完整字段列表

| 字段 | 类型 | 命令行 | 说明 |
|------|------|--------|------|
| `context_size` | int | `-c` | 上下文长度（0 或空则省略） |
| `parallel` | int | `-np` | 并行解码数 |
| `batch_size` | int | `-b` | prompt 批处理大小 |
| `ubatch_size` | int | `-ub` | 物理批处理大小 |
| `gpu_layers` | int | `-ngl` | 卸载到 GPU 的层数，`-1` 为全部 |
| `threads` | int | `-t` | CPU 线程数 |
| `pooling` | string | `--pooling` | 嵌入池化方式（如 `mean`） |
| `chat_template` | string | `--chat-template` | 聊天模板 |
| `draft_model` | string | `-md` | 草稿模型路径 |
| `grammar_file` | string | `--grammar-file` | GBNF 语法文件路径 |
| `extra_args` | string | （透传） | 额外命令行参数（shlex 拆分） |
| `flash_attn` | bool | `-fa on` | Flash Attention（显式传 `on`） |
| `cont_batching` | bool | `--cont-batching` | 连续批处理 |
| `mlock` | bool | `--mlock` | 锁定内存避免换页 |
| `no_mmap` | bool | `--no-mmap` | 不使用 mmap 加载模型 |
| `embedding` | bool | `--embedding` | 嵌入模式 |
| `reranking` | bool | `--reranking` | 重排序模式 |
| `jinja` | bool | `--jinja` | 使用 Jinja2 模板 |
| `verbose` | bool | `-v` | 详细日志 |

> 监听地址 `--host` 与端口 `--port` 不在方案文件中，由界面单独设置。

### 默认方案示例

```json
{
  "cpu": {
    "context_size": 0, "threads": 12, "gpu_layers": 0,
    "batch_size": 512, "ubatch_size": 512,
    "flash_attn": true, "cont_batching": true
  },
  "gpu": {
    "context_size": 0, "threads": 0, "gpu_layers": -1,
    "batch_size": 512, "ubatch_size": 512,
    "flash_attn": true, "cont_batching": true
  },
  "mix": {
    "context_size": 0, "threads": 6, "gpu_layers": 15,
    "batch_size": 512, "ubatch_size": 512,
    "flash_attn": true, "cont_batching": true
  }
}
```

## 技术实现细节

### 多线程与异步
FastAPI + uvicorn（ASGI）下，阻塞型端点（网络/磁盘/子进程/sleep）声明为普通 `def`，由 FastAPI 自动丢进 anyio 线程池（默认 40 线程）执行，不阻塞事件循环；SSE `/api/logs` 是唯一的 `async def`，用 `StreamingResponse` 推送。这样 SSE 长连接不会阻塞其他 API 请求——这是服务器环境下可用的关键。

### 连接容错
客户端中途断开连接（关闭页面、请求被取代、网络中断）在 SSE 推送阶段最常见。SSE 异步生成器捕获 `asyncio.CancelledError`/`GeneratorExit` 后正常 `return`，不产生 traceback；`await request.is_disconnected()` 检测到客户端干净断开时提前退出循环。

### Windows 进程管理
- `CREATE_NO_WINDOW` 隐藏子进程控制台窗口
- `CREATE_NEW_PROCESS_GROUP` 创建独立进程组，便于树形终止
- 强制停止用 `taskkill /T /F /PID` 确保杀死整个进程树（`proc.kill()` 无法终止子进程）

### GitHub API 访问
- `urllib.request`（标准库）发送请求，附带 `User-Agent` 头
- 遇 403 频率限制自动重试（最多 3 次，指数退避 1s / 2s）
- 错误消息分类处理：403 限流 / 超时 / 连接拒绝，分别返回友好中文提示
- 前端拉取全部发布后本地按 `os_group` 过滤，切换平台筛选不重复请求 API

### 版本更新流程
- 进度区间设计：下载 0-80% + 安装 80-100%，连续不回退
- 无 Content-Length 时缓慢推进到 70%，避免进度卡 0%
- 安装前检测 llama-server 是否运行，运行中拒绝更新
- 备份旧二进制（`llama-server*` / `.dll` / `.so` / `.dylib` / `.exe`）到 `_backup` 目录
- Linux/macOS 下为新可执行文件补 `+x` 权限

### 日志推送
- SSE 实现实时日志推送，后端 `queue.Queue` 收集，SSE 线程定时 drain
- 无数据时 `sleep(0.08)` 让出 CPU
- 前端日志面板 500 行硬上限，超出自动裁剪最旧行，防止 DOM 内存膨胀
- SSE 断线指数退避自动重连（1s→2s→…→15s 上限），仅运行时重连

### 前端资源优化
- 零外部资源：无 CDN、无 web 字体，系统字体栈，CSS/JS 全内联
- 白底黑字面板式极简设计：CSS 变量集中配色，无圆角/阴影/动画，渲染开销极低
- 响应式自适应：`max-width:640px` 断点下表单与分栏纵向堆叠，适配手机/平板/桌面
- 状态轮询仅运行时进行，停止后清除
- 单文件 HTML，首次加载仅 1 个请求

## 注意事项

- GitHub API 匿名访问限 60 次/小时，版本管理功能可能受限
- Windows 上停止服务需等待进程完全退出
- 模型文件较大，请确保模型目录有足够空间
- 远程访问时请确保防火墙允许对应端口
- llama-server 的 `-fa` 参数必须显式传值（`on`/`off`/`auto`），否则会吞掉后续参数
- 更新版本前必须先停止 llama-server，否则 `.exe` 文件被锁定无法覆盖

## 许可证

MIT License
