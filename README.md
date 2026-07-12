# llama.cpp API 启动器

基于 FastAPI 的 `llama-server` 管理工具，通过浏览器网页完成模型加载、参数配置、服务启停与版本更新。支持本地与远程管理，无需命令行，适配服务器环境。

## 核心价值

- **轻依赖**：仅 `fastapi` + `uvicorn` 两个第三方库，`pip install -r requirements.txt` 即可
- **Web 管理**：浏览器界面，支持本地和远程服务器管理
- **管理员登录认证**：PBKDF2 密码哈希 + 内存 session 管理，远程访问需登录后操作
- **原生文件选择器**：调用系统原生对话框选择路径，无需手动输入
- **模块化前端**：HTML / CSS / JS 分离，登录页 + 菜单式控制台多页面架构
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

### 启动前健康检查
- 启动前检测 llama-server 是否存在、模型是否为 `.gguf`、端口是否可绑定、方案是否有效
- 结构化返回 `errors`（阻断启动）与 `warnings`（允许继续），前端分区展示
- GPU 参数合理性提示（如 CPU 方案误设 `gpu_layers=-1`）

### 状态增强与 API 地址
- 状态接口返回当前模型、方案、host、port、启动时间、退出码、退出时间
- 顶栏 API 徽章一键复制推理服务地址（`http://host:port`）
- 进程运行但服务未就绪时可手动探测 `/health`

### 安全与访问认证
- **管理员登录认证**：PBKDF2-HMAC-SHA256（200,000 迭代）密码哈希，内存 session（TTL 12h），登录限流（5 次失败锁 5 分钟）
- **密码设置后所有人（含本机）都须登录**：受保护管理端点（启停 / 更新 / 配置 / 关停等）本机与远程均需凭证
- 三类凭证分流：`sk-` admin Key / `sess-` session / legacy `auth_token`
- 多 API Key 管理：可生成、命名、启停、回收、设置作用域（admin / proxy）的独立 Key
  - 明文 Key 仅创建时显示一次，之后只存 SHA-256 哈希 + 前缀
  - 旧 `auth_token` 作为主控凭证继续生效（向后兼容，过渡期保留）
- 独立登录页 + 首屏认证状态判断：未设密码时本机引导设置，远程提示需本机初始化
- UI 显示远程访问风险提示，引导设置管理员密码 / API Key
- CORS 默认同源，仅在显式配置 `allowed_origins` 时放开

### 参数方案编辑器
- UI 内新建 / 修改 / 删除 / 重命名参数方案，无需手编 JSON
- 字段归一化与校验：非法 int 重置、embedding 与 reranking 互斥校验
- 支持常用 int 字段、bool 开关、高级字段（`pooling` / `chat_template` / `draft_model` / `grammar_file` / `extra_args`）

### 版本管理与回滚
- 从 GitHub（`ggml-org/llama.cpp` releases）拉取官方预编译包
- 按平台筛选（Windows / Linux / macOS / 其他），前端本地过滤避免重复 API 请求
- 下载并自动解压替换更新，进度条连续不回退（下载 0-80% + 安装 80-100%）
- 备份旧版本到 `_backup` 目录
- 更新后可一键回滚到上个版本（运行中阻止回滚）
- 更新前检测 llama-server 是否运行，避免覆盖被锁定的文件

### OpenAI 兼容 API 工作台
- **完全兼容 OpenAI 官方 API**：启动器暴露 `/v1/*` 反向代理，透明转发至 llama-server，路径与 OpenAI 官方一致（`/v1/chat/completions`、`/v1/models`、`/v1/embeddings`、`/v1/rerank` 等），可直接用 OpenAI SDK 调用（`base_url="http://host:8686/v1"`、`api_key="sk-..."`）
- 一键复制 base_url 与各端点路径
- 交互式 Chat Completions 调试面板（系统提示、温度、max_tokens、流式开关，支持 SSE 逐字输出）
- Embedding / Reranking 专用测试面板
- 模型列表与 `/api/health` 就绪探测
- 自动生成 Python / JavaScript / cURL 客户端调用示例（含 `Authorization: Bearer sk-...` 头）
- Prompt 模板管理：保存、分类、快速应用到 Chat 面板（存储于 `configs/prompts.json`）

### 日志体验
- 日志级别过滤（全部 / 普通输出 / 错误 / 退出码 / 命令）
- 自动滚动开关
- 一键下载当前日志

### 服务端目录浏览器与原生文件选择器
- `GET /api/browse` 端点列出服务器文件系统目录（旧版 HTML 浏览器，仍可用）
- `GET /api/pick` 端点调起服务端原生 OS 文件/目录选择对话框（推荐）
  - Windows：PowerShell + `System.Windows.Forms`（FolderBrowserDialog / OpenFileDialog）
  - Linux：zenity 优先，kdialog 备选
  - macOS：osascript（choose folder / choose file）
  - 远程无头服务器无显示器时返回空串，前端回退到手动输入
- 路径配置面板点击「浏览…」调用原生选择器，返回服务端文件系统绝对路径
- 支持选择目录（llama.cpp 目录、模型目录）或文件（方案文件、草稿模型、语法文件）

### 参数方案系统
- 每个 JSON 文件可包含多套方案（`cpu` / `gpu` / `mix`）
- 支持切换 `configs/` 目录下不同方案集文件
- 方案参数覆盖 llama-server 所有常用选项

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

### Windows 终端中文乱码处理

项目所有文本文件（README、开发文档、HTML、源码）均以 **UTF-8 无 BOM** 编码保存，浏览器页面已声明 `<meta charset="UTF-8">`，正常情况下中文不会乱码。若在 **Windows PowerShell / cmd** 中用 `type`、`more` 或重定向输出查看含中文的文件时出现乱码，是终端代码页问题，与文件本身无关。解决方法任选其一：

```powershell
# 方法一：当前会话切换到 UTF-8 代码页（推荐）
chcp 65001

# 方法二：PowerShell 7+ 或 Windows Terminal 默认即为 UTF-8，直接使用即可
# 方法三：用支持 UTF-8 的编辑器（VS Code、Notepad++）打开，而非终端 cat/type
```

> 永久生效：在 PowerShell 配置文件（`$PROFILE`）中加入 `chcp 65001 > $null`，或设置系统区域为"使用 Unicode UTF-8 提供全球语言支持"（Beta）。

### 访问认证与安全配置

启动器要求**所有访问（含本机）在设置密码后都须登录**才能操作受保护的管理端点。首次使用时浏览器会引导设置管理员密码（仅本机可初始化），设密后每次打开需登录获取 session（TTL 12h，localStorage 持久化，无需频繁输入）。

1. **设置管理员密码（首次必做）**：
   - 首次使用时在本机浏览器打开 `http://127.0.0.1:8686`，登录页会引导设置管理员密码
   - 密码以 PBKDF2-HMAC-SHA256（200,000 迭代）加盐哈希存储，配置文件无明文
   - 登录后获得 `sess-` session（TTL 12h），前端自动注入到所有 `/api/*` 请求
   - 修改密码后所有 session 立即失效，需重新登录
   - 首次设置仅允许本机操作；远程访问时登录页提示需本机初始化
   - 忘记密码恢复：删除 `llama_launcher_config.json` 中的 `admin_password_hash` 字段后重启，即可重新走首次设置流程
2. **设置管理凭证（补充手段）**：
   - **API Key（多 Key 体系）**：在「安全设置 → API Key 管理」区点击"+ 新建 Key"，填写标签与作用域后创建。明文 Key 仅创建时显示一次，请立即保存。
   - **主控 Token（旧版兼容）**：直接编辑 `llama_launcher_config.json` 中的 `auth_token` 字段（过渡期保留）。
3. 设置凭证后，所有请求危险接口（`/api/start`、`/api/stop`、`/api/update`、`/api/shutdown`、`/api/config` POST、方案编辑、回滚等）——**无论本机还是远程**——都须携带 `Authorization: Bearer <session_or_key_or_token>` 头
4. CORS 默认同源；如需跨域调用管理 API，在配置中设置 `allowed_origins`（如 `["https://example.com"]`）

**API Key 作用域说明**：

| 作用域 | 可访问接口 | 适用场景 |
|--------|-----------|---------|
| `admin` | 全部受保护接口（含启停、更新、Key 管理本身） | 管理员运维 |
| `proxy` | 仅推理代理 `/v1/*`（chat / embeddings / rerank 等） | 分发给只做推理测试的调用方 |

> Key 明文格式为 `sk-<32位随机>`（与 OpenAI 官方 Key 格式一致），服务端只存 SHA-256 哈希与前缀，无法反查明文。停用 / 回收某 Key 后该 Key 立即失效，不影响其他调用方。旧 `auth_token` 与新 Key 体系相互独立、可并行使用。
>
> ⚠ 远程管理 Key 创建（明文经 HTTP 返回）务必走 HTTPS 或仅在本机操作 Key 创建，避免明文被嗅探。
>
> `/v1/*` 是 OpenAI 兼容反向代理，**始终要求 `sk-` API Key**（含本机访问），与 OpenAI 官方行为一致。前端工作台需在"API Key"输入框填入 `sk-` Key 后方可调用推理 API。

### 使用步骤

1. 启动后浏览器打开 `http://localhost:8686`
2. 首次使用：登录页引导设置管理员密码（本机操作）；后续访问输入密码登录
3. 登录后进入主控台，在 **路径配置** 面板点击「浏览…」调用系统文件选择器，选择 llama.cpp 目录与模型目录
4. 在 **参数方案** 面板从列表选择方案，或新建/编辑方案参数
5. 在 **仪表盘** 面板选择模型与方案，设置 llama-server 监听地址和端口
6. （可选）点击"启动前检查"预检启动条件
7. 点击顶栏 **▶ 启动** 开始推理服务，**实时日志** 面板实时显示输出
8. 启动后顶栏 API 徽章显示推理服务地址，点击复制；可在 **API 工作台** 调试 Chat / Embeddings / Reranking

## 项目架构

### 目录结构

```
llama-API-launcher/
├── main.py                      # 入口：参数解析、端口检测、浏览器打开
├── requirements.txt             # Python 依赖（fastapi + uvicorn）
├── .gitignore                   # 忽略 __pycache__、运行时配置、备份
├── README.md
├── PROJECT_GUIDE.md             # 开发指南（架构、约定、认证模型、路线图）
├── LICENSE
├── llama_launcher_config.json   # 运行时配置（自动生成，已 gitignore）
├── assets/
│   └── icon.ico                 # 窗口图标
├── configs/                     # 参数方案集与用户数据目录
│   ├── default.json             # 默认方案集（cpu/gpu/mix）
│   └── prompts.json             # API 工作台 Prompt 模板（运行时生成）
├── core/                        # 核心逻辑模块（无 UI 依赖）
│   ├── __init__.py
│   ├── paths.py                 # 路径检测、模型枚举、目录浏览、健康检查辅助
│   ├── profiles.py              # JSON 方案加载/保存/枚举/编辑/校验
│   ├── launcher.py              # 命令构建 + 子进程封装（ServerRunner）
│   ├── version_manager.py       # GitHub 发布解析、下载、解压替换、回滚
│   ├── api_client.py            # OpenAI 兼容 API 客户端（供示例生成与探活）
│   ├── api_keys.py              # API Key 生成/哈希/校验/CRUD（纯同步零 Web 依赖）
│   ├── admin_auth.py            # 管理员认证：PBKDF2 密码哈希 + 内存 session + 登录限流
│   ├── filepicker.py            # 原生 OS 文件选择器（Windows PowerShell / Linux zenity / macOS osascript）
│   └── proxy.py                 # /v1/* 流式反向代理（http.client + read1，零第三方依赖）
├── tests/                       # 单元测试（unittest，test_v1_api / test_admin_auth 需 httpx）
│   ├── __init__.py
│   ├── test_launcher.py         # build_command / quote_arg
│   ├── test_paths.py            # 路径检测、目录浏览、健康检查辅助
│   ├── test_profiles.py         # ProfileManager / normalize / validate
│   ├── test_version_manager.py  # 资产名解析 / 回滚 / 备份检测
│   ├── test_api_client.py       # API 客户端（mock urllib）
│   ├── test_api_keys.py         # API Key 生成/校验/CRUD（36 项）
│   ├── test_admin_auth.py       # 管理员认证：密码哈希 / session / 限流 / API 端点（48 项）
│   └── test_v1_api.py           # /v1/* 反向代理 + 认证中间件（20 项，需 httpx）
└── web/                         # Web 应用
    ├── __init__.py
    ├── app.py                   # FastAPI 应用（路由、SSE、认证、代理、静态托管）
    └── static/
        ├── index.html           # 登录页（首屏认证状态判断 → 登录 / 首次设置 / 远程提示）
        ├── app.html             # 主控台（侧边栏菜单 + 7 面板：仪表盘 / 路径 / 方案 / 工作台 / 安全 / 版本 / 日志）
        ├── css/
        │   ├── main.css         # 设计令牌 + 通用组件（表单、按钮、日志、Tab、弹窗、表格）
        │   └── app.css          # 侧边栏布局 + 登录页样式 + 响应式
        └── js/
            ├── api.js           # API 工具 + session 注入 + 403 auth_expired 处理 + v1 Key
            ├── auth.js          # 登录 / 登出 / 首次设置 / 修改密码 / 首屏守卫
            ├── app.js           # 主控台初始化 + 菜单切换 + 状态 / 启停 / SSE 日志
            ├── config.js        # 路径配置 + 原生文件选择器 + 健康检查
            ├── profiles.js      # 参数方案编辑器（列表 + 表单分栏面板）
            ├── workbench.js     # API 工作台（Chat / Embeddings / Rerank / 模型 / 示例 / Prompt）
            ├── security.js      # API Key 管理 + 管理员密码管理
            └── version.js       # 版本管理 + 回滚
```

### 模块职责

| 模块 | 职责 | 关键类/函数 |
|------|------|-------------|
| `core/paths.py` | 路径检测、模型枚举、目录浏览、健康检查辅助 | `detect_llamacpp()`, `list_model_files()`, `browse_directory()`, `check_port_bindable()`, `resolve_model_path()`, `model_exists()` |
| `core/profiles.py` | 参数方案管理（加载/保存/枚举/编辑/校验） | `ProfileManager`, `normalize_profile()`, `validate_profile()`, `load_json()`, `save_json()` |
| `core/launcher.py` | 命令构建与进程管理 | `build_command()`, `quote_arg()`, `ServerRunner` |
| `core/version_manager.py` | GitHub 版本管理与回滚 | `fetch_releases()`, `download_file()`, `install_asset()`, `has_backup()`, `rollback_asset()` |
| `core/api_client.py` | OpenAI 兼容 API 客户端（示例生成与探活） | `list_models()`, `chat_completions()`, `embeddings()`, `rerank()`, `gen_client_examples()`, `health()` |
| `core/api_keys.py` | API Key 生成/哈希/校验/CRUD（纯同步零 Web 依赖） | `generate_key()`, `hash_key()`, `verify()`, `create_key()`, `revoke_key()`, `set_enabled()`, `update_label()`, `list_keys()` |
| `core/admin_auth.py` | 管理员认证：PBKDF2 密码哈希 + 内存 session + 登录限流 | `hash_password()`, `verify_password()`, `create_session()`, `verify_session()`, `revoke_session()`, `change_password()`, `set_password()` |
| `core/filepicker.py` | 原生 OS 文件/目录选择器（subprocess 调用平台对话框） | `pick()`, `is_available()` |
| `core/proxy.py` | `/v1/*` 流式反向代理（纯标准库 `http.client`） | `open_upstream()`, `filter_response_headers()` |
| `web/app.py` | HTTP API 服务（FastAPI，含认证中间件与代理） | `app`, `run_server()`, `init_app()` |
| `web/static/` | 前端界面（多文件模块化） | `index.html`（登录页）+ `app.html`（主控台）+ `css/` + `js/` |
| `tests/` | 单元测试 | `python -m unittest discover tests` |

### ServerRunner 类

封装 `llama-server` 子进程的启停与日志读取：

- **启动** `start(cmd)` — 创建独立进程组（`CREATE_NO_WINDOW` + `CREATE_NEW_PROCESS_GROUP`），后台线程逐行读取输出，记录 `start_time`
- **优雅停止** `stop()` — Windows 用 PowerShell `CloseMainWindow`，非 Windows 发送 `SIGTERM`
- **强制停止** `force_stop()` — Windows 用 `taskkill /T /F /PID` 杀进程树，非 Windows `proc.kill()`；清空运行时信息
- **状态查询** `running` 属性 — 通过 `proc.poll()` 判断
- **日志读取** `drain()` — 取出队列中的日志事件 `[(kind, data), ...]`，kind ∈ `out` / `rc`
- **运行时信息** `set_runtime_info()` — 记录 model / profile / host / port；`exit_code` / `exit_time` 在进程退出时由 reader 线程写入

### Web 架构

- **HTTP 服务器**：FastAPI + uvicorn（ASGI），阻塞型端点声明为普通 `def` 自动跑 anyio 线程池，SSE 长连接为唯一 `async def`，不阻塞其他请求
- **认证中间件**：本机与远程均需凭证（密码设置后须登录）；受保护路径需 `Authorization: Bearer <token>`，OPTIONS 预检放行
- **CORS**：默认同源，仅在配置 `allowed_origins` 时挂载 `CORSMiddleware`
- **路由分发**：FastAPI 路由装饰器（`@app.get`/`@app.post`），未知 `/api/*` 由兜底路由返回 JSON 404
- **静态托管**：`StaticFiles` 挂载在 `/`（最后注册，API 路由优先匹配），`html=True` 自动服务 `index.html`（登录页）；`/app.html` 等其他 HTML 文件也可直接访问
- **实时日志**：SSE（Server-Sent Events）经 `StreamingResponse` 推送，`queue.Queue` 收集日志，生成器 `await asyncio.sleep` 让出 CPU
- **状态同步**：前端运行时每 3 秒轮询 `/api/status`
- **优雅退出**：`_shutdown_event` 通知 SSE 生成器退出 + `server_instance.should_exit=True` 触发 uvicorn 关停；`lifespan` 统一清理子进程
- **连接容错**：SSE 生成器捕获 `asyncio.CancelledError`/`GeneratorExit`；客户端断开不产生 traceback

## API 接口

> 标记 🔒 的端点为受保护路径：**本机与远程均须携带** `Authorization: Bearer <token>`（密码设置后所有人都须登录）。

### 配置与目录

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/config` | GET | 获取当前配置（含 llama.cpp 检测状态、auth_enabled、remote_access） |
| `/api/config` | POST 🔒 | 更新配置（llamacpp_dir / model_dir / host / port / auth_token / allowed_origins） |
| `/api/models` | GET | 列出模型文件 |
| `/api/profiles` | GET | 列出参数方案名 |
| `/api/profiles/path` | POST 🔒 | 设置方案文件路径 |
| `/api/profiles/get/{name}` | GET | 获取指定方案详情 |
| `/api/profiles/save` | POST 🔒 | 保存/另存方案（含字段归一化与校验） |
| `/api/profiles/delete` | POST 🔒 | 删除指定方案 |
| `/api/configs/list` | GET | 列出 configs/ 下方案集文件 |
| `/api/configs/load/{filename}` | POST 🔒 | 加载指定方案集 |
| `/api/browse?path={path}` | GET | 浏览服务端目录（path 为空返回根） |
| `/api/pick?type={dir\|file}&filter={ext}&title={title}` | GET 🔒 | 调起原生 OS 文件/目录选择对话框，返回 `{path, available}` |

### 服务控制与健康检查

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 获取服务状态（running / pid / command / model / profile / host / port / start_time / exit_code / exit_time） |
| `/api/healthcheck` | GET | 启动前健康检查，返回 `{ok, errors, warnings}` |
| `/api/start` | POST 🔒 | 启动 llama-server（返回 api_base） |
| `/api/stop` | POST 🔒 | 停止服务（支持 `force` 参数） |
| `/api/logs` | GET | SSE 日志流 |
| `/api/shutdown` | POST 🔒 | 关闭整个应用 |

### 版本管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/releases?os={os}` | GET | 获取 GitHub 发布列表（返回 `os_group` 字段） |
| `/api/update` | POST 🔒 | 开始下载更新（需先停止 llama-server） |
| `/api/update/progress/{id}` | GET | 查询更新进度 |
| `/api/update/backup_status` | GET | 查询 `_backup` 是否存在及可回滚文件数 |
| `/api/update/rollback` | POST 🔒 | 从 `_backup` 回滚到上个版本（运行中阻止） |
| `/api/platform` | GET | 获取当前平台信息 |

### OpenAI 兼容 `/v1/*` 反向代理

> `/v1/*` 是 OpenAI 兼容反向代理，透明转发至 llama-server。**始终要求 `sk-` API Key**（含本机访问，与 OpenAI 官方行为一致），错误返回 OpenAI 对象格式 `{"error":{"message","type","param","code"}}`。支持 SSE 流式（`http.client` + `read1` 逐块转发）。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST 🔑 | OpenAI Chat Completions（支持 `stream: true` SSE 流式） |
| `/v1/completions` | POST 🔑 | OpenAI Completions |
| `/v1/embeddings` | POST 🔑 | OpenAI Embeddings |
| `/v1/rerank` | POST 🔑 | Reranking |
| `/v1/models` | GET 🔑 | 列出可用模型 |
| `/v1/{任意路径}` | GET/POST/PUT/DELETE/PATCH 🔑 | 透明转发所有 `/v1/` 子路径至 llama-server |
| `/api/endpoints` | GET | 列出 base_url 与各端点路径（公开，无需认证） |
| `/api/health` | GET | 探活 llama-server 就绪状态（公开，无需认证） |
| `/api/examples` | GET | 生成 Python / JavaScript / cURL 调用示例（含 Bearer 头） |

> 🔑 = 始终需要 `Authorization: Bearer sk-...`（含本机）。可用 OpenAI SDK 直接调用：`base_url="http://host:8686/v1"`、`api_key="sk-..."`。

### API Key 与 Prompt 模板管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/prompts` | GET / POST / DELETE 🔒 | Prompt 模板管理（存储于 `configs/prompts.json`） |
| `/api/keys` | GET / POST 🔒 | API Key 管理：列出 / 创建（创建时返回一次明文，存储于 `configs/api_keys.json`） |
| `/api/keys/{id}` | DELETE 🔒 | 回收指定 Key |
| `/api/keys/toggle` | POST 🔒 | 启停指定 Key（body: `{id, enabled}`） |
| `/api/keys/rename` | POST 🔒 | 重命名指定 Key（body: `{id, label}`） |

### 管理员认证

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/auth/status` | GET | 查询认证状态（password_set / auth_enabled / session_valid / remote_access / is_local） |
| `/api/auth/login` | POST | 登录签发 session（body: `{password}`，返回 `{session_token, expires_at}`，IP 限流 5 次/5 分钟） |
| `/api/auth/logout` | POST 🔒 | 登出，吊销当前 session |
| `/api/auth/setup` | POST | 首次设置密码（仅本机，仅当密码未初始化；body: `{password, confirm}`） |
| `/api/auth/change-password` | POST | 修改密码（body: `{old_password, new_password}`，改密后吊销所有 session） |

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

### 认证与 CORS
- **认证中间件**（纯 ASGI，内层）先于 CORS 注册，OPTIONS 预检由 CORS（外层）先处理
- 三类路径分流：
  - **`/v1/*`（OpenAI 兼容推理 API）**：始终要求 `sk-` Key（含本机），`required_scope = proxy`，session 不被接受；失败返回 OpenAI 对象格式错误（401）
  - **`/api/*` 受保护管理端点**：**本机与远程均需凭证**（密码设置后所有人都须登录），接受三类凭证：
    - a) `sk-` admin Key（`required_scope = admin`）
    - b) `sess-` session（管理员登录，主路径）
    - c) legacy `auth_token`（过渡期兼容，`hmac.compare_digest` 比对）
    - 失败返回字符串格式错误（403），session 失效附 `auth_expired: true`
  - **其余路径**（静态文件、非受保护 `/api/*` 如 `/api/auth/status` `/api/health`）：透传
- `sk-` 前缀凭证走 `core/api_keys.verify`（锁内重载 + 60s debounce 更新 `last_used_at`）
- `sess-` 前缀凭证走 `core/admin_auth.verify_session`（懒清理过期项）
- 受保护路径拆为两组：`PROTECTED_EXACT`（精确匹配）与 `PROTECTED_PREFIX`（前缀匹配，如 `/api/keys/{id}`）
- 未设密码时前端引导本机完成 `/api/auth/setup`（该端点内部校验本机，不经中间件保护）；设密后所有受保护路径均需凭证
- CORS 默认不挂载（同源）；配置 `allowed_origins` 后才挂载 `CORSMiddleware`

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

### 版本更新与回滚
- 进度区间设计：下载 0-80% + 安装 80-100%，连续不回退
- 无 Content-Length 时缓慢推进到 70%，避免进度卡 0%
- 安装前检测 llama-server 是否运行，运行中拒绝更新
- 备份旧二进制（`llama-server*` / `.dll` / `.so` / `.dylib` / `.exe`）到 `_backup` 目录
- Linux/macOS 下为新可执行文件补 `+x` 权限
- 回滚从 `_backup` 恢复文件覆盖当前二进制，运行中阻止回滚；Linux/macOS 补 `+x`

### 日志推送
- SSE 实现实时日志推送，后端 `queue.Queue` 收集，SSE 线程定时 drain
- 无数据时 `sleep(0.08)` 让出 CPU
- 前端日志面板 500 行硬上限，超出自动裁剪最旧行，防止 DOM 内存膨胀
- SSE 断线指数退避自动重连（1s→2s→…→15s 上限），仅运行时重连
- 前端支持级别过滤、自动滚动开关、日志下载

### OpenAI 兼容 `/v1/*` 反向代理
- `core/proxy.py` 用纯标准库 `http.client` 实现流式反向代理，零第三方依赖
- `resp.read1(8192)` 逐块读取上游响应（非 `read(n)` 缓冲），确保 SSE 流式不卡死
- `/v1/{rest:path}` catch-all 路由透明转发所有 `/v1/` 子路径至 llama-server
- 请求剥离 `authorization` 头（不转发客户端凭证到 llama-server）；响应剥离 hop-by-hop 头与 `content-length`
- `gen_client_examples()` 生成 Python / JavaScript / cURL 调用代码（含 `Authorization: Bearer sk-...`），供前端一键复制
- Prompt 模板持久化到 `configs/prompts.json`，支持增删改与快速应用到 Chat 面板

### 前端资源优化
- 零外部资源：无 CDN、无 web 字体，系统字体栈，CSS/JS 分离为多文件
- 白底黑字面板式极简设计：CSS 变量集中配色，渲染开销极低
- 多文件模块化架构：`index.html`（登录页）+ `app.html`（主控台）+ `css/` + `js/`（8 个模块）
- 侧边栏菜单布局：7 个面板（仪表盘 / 路径配置 / 参数方案 / API 工作台 / 安全设置 / 版本管理 / 实时日志）
- 响应式自适应：`max-width:768px` 断点下侧边栏折叠为汉堡菜单，`max-width:640px` 下表单纵向堆叠
- 状态轮询仅运行时进行，停止后清除
- session token 自动注入所有 `/api/*` 请求，403 + `auth_expired` 自动跳转登录页

## 测试

项目附带 `unittest` 测试套件（`tests/` 目录），共 203 项测试覆盖纯逻辑函数与 API 层：

```bash
# 运行全部测试（203 项）
python -m unittest discover tests

# 运行单个模块
python -m unittest tests.test_launcher
python -m unittest tests.test_paths
python -m unittest tests.test_profiles
python -m unittest tests.test_version_manager
python -m unittest tests.test_api_client
python -m unittest tests.test_api_keys
python -m unittest tests.test_admin_auth
python -m unittest tests.test_v1_api
```

测试不依赖真实 llama-server、真实模型文件或真实 GitHub 网络。`test_api_client.py` 用 `unittest.mock` mock 掉 `urllib.request.urlopen`；`test_v1_api.py` 用 `ThreadingHTTPServer` 起 mock llama-server + `TestClient` 验证 `/v1/*` 反向代理与认证中间件（需 `httpx`，仅测试依赖）；`test_admin_auth.py` 用 `TestClient` 验证密码哈希、session 管理、登录限流与 `/api/auth/*` 端点（48 项，需 `httpx`）。

## 注意事项

- GitHub API 匿名访问限 60 次/小时，版本管理功能可能受限
- Windows 上停止服务需等待进程完全退出
- 模型文件较大，请确保模型目录有足够空间
- 远程访问时请确保防火墙允许对应端口，并务必设置管理 Token
- llama-server 的 `-fa` 参数必须显式传值（`on`/`off`/`auto`），否则会吞掉后续参数
- 更新版本前必须先停止 llama-server，否则 `.exe` 文件被锁定无法覆盖
- 回滚前必须先停止 llama-server，运行中阻止回滚
- Windows PowerShell/cmd 中查看含中文文件出现乱码时，执行 `chcp 65001` 切换 UTF-8 代码页

## 许可证

MIT License
