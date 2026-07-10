# 开发者文档

本文档供接力开发者（含 AI Agent）参考，涵盖架构设计、模块细节、数据流、陷阱清单与扩展指南。阅读前建议先浏览 [README.md](README.md) 了解项目概貌。

---

## 1. 全局架构

```
┌─────────────────────────────────────────────────┐
│                  main.py (入口)                  │
│  argparse → check_port → init_app → run_server  │
└──────────────────────┬──────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │     web/app.py          │
          │  FastAPI app + uvicorn  │
          │  装饰器路由              │
          └──┬────────┬────────┬────┘
             │        │        │
     ┌───────▼──┐  ┌──▼───┐  ┌▼──────────────┐
     │ core/    │  │ core │  │ core/         │
     │ paths.py │  │ prof │  │ launcher.py   │
     │          │  │ iles │  │ ServerRunner  │
     └──────────┘  └──────┘  └──────┬────────┘
                                        │
                                   ┌────▼────┐
                                   │ 子进程   │
                                   │ llama-  │
                                   │ server  │
                                   └─────────┘
```

### 核心设计原则

1. **core/ 零 UI 依赖** — `core/` 下的模块只做逻辑（路径检测、命令构建、进程管理、版本管理），不 import 任何 web 相关库。可被独立测试和复用。
2. **web/ 是薄壳** — `web/app.py` 只做 HTTP 路由 + JSON 序列化 + 调用 core 函数。业务逻辑不写在 handler 里。
3. **阻塞端点用普通 `def`、SSE 用 `async def`** — FastAPI 自动把普通 `def` 路由丢进 anyio 线程池执行，不阻塞事件循环；`/api/logs` 是唯一的 `async def`（用 `StreamingResponse`）。`core/` 保持纯同步，不重写为 asyncio。
4. **单文件前端** — `web/static/index.html` 包含全部 HTML/CSS/JS，零外部资源。

---

## 2. 模块详解

### 2.1 `main.py` — 入口

```
main()
  ├── argparse: --host / --port / --no-browser
  ├── check_port() — socket.bind 检测端口可用性
  ├── init_app() — 加载配置、方案管理器、ServerRunner
  ├── open_browser_later() — daemon 线程延迟 2s 打开浏览器
  └── run_server() — 阻塞直到 Ctrl+C
```

**注意**：
- 仅 `--host 127.0.0.1`（默认）时自动打开浏览器；`0.0.0.0` 或 `--no-browser` 时不打开
- `check_port` 用 `socket.bind` 而非 `connect`，检测的是"能否绑定"而非"是否有人监听"
- `sys.path.insert(0, ...)` 确保能导入同目录下的 `core/` 和 `web/` 包

### 2.2 `core/paths.py` — 路径与目录浏览

#### 关键函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `server_executable_candidates(llamacpp_dir)` | `→ list[str]` | 在目录及其子目录（`build/bin`, `build`, `bin`, `release`）中查找 `llama-server` |
| `detect_llamacpp(directory)` | `→ (status, message, exe_path)` | status ∈ `'ok'`/`'bad'`，message 是中文提示 |
| `list_model_files(model_dir)` | `→ list[str]` | 列出 `.gguf` 文件，支持一级子目录（`子目录/文件名`） |
| `list_roots()` | `→ list[str]` | Windows 枚举盘符，Unix 返回 `['/']` |
| `browse_directory(path)` | `→ dict` | 返回 `{path, parent, entries, error}` |

#### `browse_directory` 返回结构

```python
{
    "path": "C:\\Users\\models",       # 解析后的绝对路径
    "parent": "C:\\Users",             # 上级目录，根目录时为空字符串
    "entries": [
        {"name": "subdir", "type": "dir", "size": 0},
        {"name": "model.gguf", "type": "file", "size": 4200000000}
    ],
    "error": ""                        # 权限错误等信息，不阻断浏览
}
```

#### 文件过滤规则

目录浏览器只显示：
- 所有目录
- 扩展名为 `.gguf` 或 `.json` 的文件
- 文件名以 `llama-server` 开头的文件

定义在 `BROWSE_FILE_EXTS` 和 `BROWSE_FILE_PREFIXES` 常量中。

### 2.3 `core/profiles.py` — 方案管理

#### `ProfileManager` 类

```python
pm = ProfileManager("configs/default.json")
pm.names()     # → ['cpu', 'gpu', 'mix']
pm.get("cpu")  # → {context_size: 0, threads: 12, ...}
pm.save()      # 保存到原路径
```

#### JSON 工具函数

- `load_json(path, fallback)` — 安全读取，失败返回 fallback 的副本
- `save_json(path, data)` — UTF-8 写入，`ensure_ascii=False`，缩进 2
- `list_config_files(directory)` — 列出目录下 `.json` 文件

#### 配置文件结构

`llama_launcher_config.json`（运行时自动生成）：
```json
{
  "llamacpp_dir": "C:/lamma-cpu",
  "model_dir": "C:/lamma-cpu/mods",
  "profiles_path": "C:\\...\\configs\\default.json",
  "last_model": "model.gguf",
  "current_profile": "cpu",
  "host": "127.0.0.1",
  "port": 8080
}
```

### 2.4 `core/launcher.py` — 命令构建与进程管理

#### `build_command(exe, model_full, profile, host, port)` → `list[str]`

构建 llama-server 命令行参数列表。关键逻辑：

```python
cmd = [exe, "-m", model_full]

# int 字段：0 或空则省略（-1 等负值保留）
int_or_skip("context_size", "-c")   # -c 4096
int_or_skip("gpu_layers", "-ngl")   # -ngl -1（全部卸载）

# bool 字段：True 则添加 flag
if profile.get("flash_attn"):
    cmd.extend(["-fa", "on"])       # ⚠️ 必须显式传 "on"

# string 字段：非空则添加
pooling = profile.get("pooling", "").strip()
if pooling:
    cmd.extend(["--pooling", pooling])

# extra_args：shlex 拆分透传
extra = profile.get("extra_args", "").strip()
if extra:
    cmd.extend(shlex.split(extra, posix=(platform.system() != "Windows")))
```

#### `ServerRunner` 类

**生命周期**：
```
start(cmd) → running=True → _reader 线程逐行读取 stdout
    ↓
stop() / force_stop() → proc 终止 → _reader 放入 ("rc", returncode)
    ↓
running=False → drain() 取出剩余日志
```

**日志队列**：`queue.Queue` 存储 `(kind, data)` 元组：
- `("out", "日志行内容")` — 普通输出
- `("rc", 0)` — 进程退出返回码

**`drain()` 方法**：一次性取出队列中所有事件，返回 `[(kind, data), ...]`。SSE handler 调用此方法获取日志并推送给前端。

**Windows 进程终止**：
- `stop()` — 用 PowerShell `CloseMainWindow()` 尝试优雅关闭
- `force_stop()` — `taskkill /T /F /PID` 杀整个进程树，然后 `proc.wait(timeout=5)`
- `force_stop()` 最后设置 `self.proc = None`，使 `running` 返回 `False`

### 2.5 `core/version_manager.py` — GitHub 版本管理

#### 资产名解析

`parse_asset_name(filename)` 解析 llama.cpp 官方预编译包文件名：

```
llama-b9860-bin-win-cpu-x64.zip
│     │      │   │   │   └ arch: x64
│     │      │   │   └──── variant: cpu
│     │      │   └──────── os: win
│     │      └──────────── 分隔符 -bin-
│     └─────────────────── build: b9860
└────────────────────────── 前缀 llama-b

→ {"build": "b9860", "os": "win", "arch": "x64",
   "variant": "cpu", "filename": "...", "ext": ".zip"}
```

**过滤规则**：只解析 `llama-b*-bin-*` 格式的文件名。`cudart-llama-bin-*`、`*-xcframework-*`、`Source code` 等被排除。

**OS 分组映射**：
```python
KNOWN_OS_GROUPS = {
    "win": "windows", "ubuntu": "linux",
    "linux": "linux", "macos": "macos",
}
```
未知 OS token 归入 `"others"` 分组。

**架构识别**：`rest[-1]` 如果是 `x64`/`arm64`/`x86`/`s390x`/`ppc64le` 则视为架构段，其余段拼接为 variant。

#### `fetch_releases(per_page=20)` → `list[dict]`

从 GitHub API 获取发布列表，解析每个资产的文件名。重试逻辑：
- 403 频率限制：重试 3 次，退避 1s / 2s，最终抛 `RuntimeError`
- 其他 `URLError`（超时/连接失败）：重试 3 次，退避 1s，最终抛 `RuntimeError`
- 其他异常：重试 3 次，退避 1s，原样抛出

> **陷阱**：不要用 `urllib.error.HTTPError` 传递用户可见的错误消息。`HTTPError.__str__()` 不返回构造时传入的 `msg` 参数。改用 `RuntimeError` 保证 `str(e)` 可读。

#### `install_asset(zip_path, target_dir, progress_cb)` → `(ok, message)`

更新流程：
1. 解压到临时目录（`.zip` 用 `zipfile`，`.tar.gz`/`.tgz` 用 `tarfile`）
2. 若压缩包内仅一个顶层目录，进入该目录
3. 备份旧文件（`llama-server*`、`.dll`、`.so`、`.dylib`、`.exe`）到 `target_dir/_backup/`
4. 复制新文件覆盖旧文件
5. Linux/macOS 下为 `llama-server` 等无扩展名文件补 `+x` 权限

**进度回调**：`progress_cb(phase, cur, total)`
- `("extract", 0, 0)` — 解压阶段
- `("backup", 0, 0)` — 备份阶段
- `("copy", i, total)` — 复制阶段，每 5 个文件或最后一个时回调

### 2.6 `web/app.py` — FastAPI 应用

基于 FastAPI + uvicorn 的 ASGI 应用，取代标准库 `ThreadingHTTPServer`。关键约束：
**阻塞型端点声明为普通 `def`**（FastAPI 自动丢进 anyio 线程池，不阻塞事件循环），
**SSE `/api/logs` 是唯一的 `async def`**（用 `StreamingResponse` 推送）。`core/` 保持纯同步。

#### 全局状态

```python
base_dir        # 项目根目录（main.py 所在目录）
config_path     # 配置文件路径
config_data     # 配置字典（运行时读写）
pm              # ProfileManager 实例
runner          # ServerRunner 实例
version_updates # 更新任务字典 {update_id: {status, progress, message}}
server_instance # uvicorn.Server 实例（用于 should_exit 关停）
_shutdown_event # threading.Event，通知 SSE 生成器退出
```

#### 应用构造

```python
app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
```

- 关闭 `docs_url`/`redoc_url`/`openapi_url` 保持"单页前端"体验，避免暴露 schema。
- `lifespan` 在应用关停时统一清理（Ctrl+C 与 `/api/shutdown` 共用）：
  ```python
  @asynccontextmanager
  async def lifespan(_app):
      yield
      _shutdown_event.set()           # 先通知 SSE 生成器退出
      if runner.running:
          runner.force_stop()
  ```

#### 路由（装饰器，全部普通 `def`，SSE 除外）

```python
@app.get("/api/config")
def get_config(): ...

@app.post("/api/config")
def set_config(body: dict = Body(default={})): ...

@app.get("/api/browse")
def browse(path: str = Query("")): ...      # 自动 percent-decode，支持中文 "全部" / 空 path

@app.post("/api/configs/load/{filename}")  # filename 含点（如 default.json）正常捕获
def load_config_file(filename: str): ...    # 注意：前端不发 body，故不声明 body 参数
```

> **要点**：POST 端点用 `body: dict = Body(default={})`，空 body 落到默认 `{}` 不报错（匹配旧版 `data=None` 透传语义）。

#### 响应与错误形状

成功响应直接 `return dict/list`（FastAPI 自动 JSON 序列化）；**错误响应必须显式返回
`JSONResponse(status_code=..., content={"error": msg})`**，不能用 `raise HTTPException`——
后者默认返回 `{"detail": ...}`，与前端期望的 `{"error": ...}` 契约不符。

```python
if not cands:
    return JSONResponse(status_code=400, content={"error": "未找到 llama-server"})
```

未知 `/api/*` 路径由兜底路由处理（保形旧版 JSON 404）：
```python
@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
def api_not_found():
    return JSONResponse(status_code=404, content={"error": "路径不存在"})
```

#### SSE 日志流（唯一 `async def`）

`StreamingResponse` + 异步生成器，断连/关停均静默退出：

```python
@app.get("/api/logs")
async def stream_logs(request: Request):
    async def gen():
        try:
            while not _shutdown_event.is_set():
                if await request.is_disconnected():   # 客户端干净断开（发 FIN）
                    return
                drained = runner.drain()
                for kind, data in drained:
                    payload = json.dumps({"kind": kind, "data": data}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                if not drained:
                    await asyncio.sleep(0.08)        # 无数据时让出 CPU
        except (asyncio.CancelledError, GeneratorExit):
            return   # 客户端断开 / uvicorn 关停连接时取消生成器，正常退出
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
```

> **陷阱**：生成器内**只 catch `CancelledError`/`GeneratorExit`**，不要 `except Exception` 吞掉所有异常——否则断连后循环不退出。半开 TCP（客户端断电未发 FIN）靠 `is_disconnected()` 检测不到时，由写失败触发取消异常兜底。

SSE 数据格式：`data: {"kind": "out", "data": "日志内容"}\n\n`

#### 版本更新流程

`update_llama` 在后台 `threading.Thread` 中执行更新，通过 `version_updates` 字典共享进度：

```python
# 进度区间设计（避免回退）：
# 下载阶段：0% → 80%
#   有 Content-Length：done * 80.0 / total
#   无 Content-Length：缓慢推进到 70%（每次 +0.3%）
# 安装阶段：80% → 100%
#   80 + cur * 20.0 / total
```

前端通过 `GET /api/update/progress/{id}` 每 500ms 轮询进度。

#### `shutdown` 的编程式关停

```python
@app.post("/api/shutdown")
def shutdown(background: BackgroundTasks):
    background.add_task(_do_shutdown)   # 响应发送后才执行
    return {"success": True}

def _do_shutdown():
    _shutdown_event.set()               # 先让 SSE 生成器退出，避免阻塞优雅关停
    if runner.running:
        runner.force_stop()
    if server_instance is not None:
        server_instance.should_exit = True   # uvicorn 官方关停入口
```

> **要点**：用 `server_instance.should_exit = True`（uvicorn 官方编程式关停入口，
> 置位后 main_loop 下一轮退出，不持有锁、不在请求线程内同步等待，无死锁）。
> 不要用 `server_instance.shutdown()`——那是 `socketserver` 的 API，`uvicorn.Server` 没有。
> 关停前**必须先 `_shutdown_event.set()`**，否则 SSE 长连接会阻塞 uvicorn 优雅关停。
> `BackgroundTasks` 保证响应先送达客户端，再执行关停。

#### 静态文件（最后挂载）

```python
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent / "static",
                           html=True), name="static")
```

> **陷阱**：`StaticFiles` 必须在所有 API 路由（含兜底 404 路由）注册**之后**最后挂载——
> Starlette 按声明顺序匹配，`Mount("/", ...)` 会拦截一切，先注册的 API 路由才优先命中。
> `html=True` 使请求 `/` 自动返回 `index.html`。

#### `init_app` / `run_server`

- `init_app(base)` 在 `main.py` 启动时调用（`base` 来自文件路径，lifespan 拿不到）。
- `run_server` 构造 `uvicorn.Config(app, host, port, access_log=False, log_config=None)`
  并调用 `server_instance.run()`（内部 `asyncio.run(self.serve())`，阻塞）。
- `access_log=False` 关闭 access log（对应旧版 `log_message` 静默），保留 error log。
- Ctrl+C 走 `lifespan` shutdown 清理（`_shutdown_event` + `runner.force_stop()`）。

### 2.7 `web/static/index.html` — 前端单页应用

#### 全局状态

```javascript
let isRunning = false;           // llama-server 是否运行
let isProcessing = false;        // 防止重复点击
let eventSource = null;          // SSE 连接
let sseReconnectDelay = 1000;    // SSE 重连延迟（指数退避）
let statusPollTimer = null;      // 状态轮询定时器
let releases = [];               // GitHub 发布列表缓存
let currentAssets = [];          // 当前选中发布的资产列表
```

#### 启停状态机

```
toggleServer()
  ├── isRunning=false → startServer()
  │     POST /api/start → updateStatus(true) → startLogStream() → startStatusPoll()
  └── isRunning=true  → stopServer()
        POST /api/stop → updateStatus(result.running) → stopStatusPoll()
```

> **已修复的陷阱**：`stopServer` 中 `updateStatus(result.running)` 必须直接传 `result.running`，不能写 `result.running === false`（那会把布尔值反转，导致停止后 UI 仍显示"运行中"，再点击会调用 stopServer 而非 startServer）。

#### SSE 日志处理

```javascript
eventSource.onmessage = function(event) {
    const data = JSON.parse(event.data);
    if (data.kind === 'rc') {
        addLog(`[退出 ${data.data}]`, 'rc');
        updateStatus(false);           // 进程退出，更新状态
    } else {
        addLog(data.data, data.kind);
    }
};
```

SSE 断线重连（指数退避）：
```javascript
eventSource.onerror = function() {
    eventSource.close();
    if (isRunning) {                   // 仅运行时重连
        setTimeout(() => {
            sseReconnectDelay = Math.min(sseReconnectDelay * 2, SSE_MAX_DELAY);
            startLogStream();
        }, sseReconnectDelay);
    }
};
```

#### 版本管理前端流程

```
openVersionManager()
  ├── refreshCurrent()           — 显示当前 llama.cpp 目录与检测状态
  ├── GET /api/platform          — 获取本机平台，设置 osFilter 下拉框
  └── checkUpdates()
        ├── GET /api/releases?os=全部  — 拉取全部发布（一次）
        ├── 渲染发布列表
        └── refreshAssets()      — 按 osFilter 本地过滤 os_group
              ├── 切换平台筛选时调用（不发 API 请求）
              └── 选中资产 → installAsset()
                    ├── POST /api/update {url, filename}
                    └── 每 500ms 轮询 /api/update/progress/{id}
```

> **设计决策**：前端始终拉取全部发布（`os=全部`），然后本地按 `os_group` 字段过滤。这样切换平台筛选器时不需要重复请求 GitHub API，节省 60 次/小时的匿名配额。

---

## 3. 数据流

### 3.1 启动 llama-server

```
用户点击"▶ 启动"
  → toggleServer() → startServer()
  → POST /api/start {model, profile, host, port}
  → start_server 路由:
      ├── 检测 runner.running（防重复启动）
      ├── server_executable_candidates(llamacpp_dir) 查找可执行文件
      ├── build_command(exe, model, profile, host, port) 构建命令
      ├── save_config() 持久化配置
      ├── runner.start(cmd) 启动子进程 + reader 线程
      └── 返回 {command: "..."}
  → 前端 addLog(command) + updateStatus(true) + startLogStream() + startStatusPoll()
```

### 3.2 日志推送

```
llama-server stdout
  → reader 线程逐行读取 → log_queue.put(("out", line))
  → SSE 生成器 drain() → yield "data: {...}\n\n"
  → 前端 eventSource.onmessage → addLog(text, kind)
  → 超过 500 行 → removeChild(firstChild)
```

### 3.3 停止 llama-server

```
用户点击"■ 停止"
  → toggleServer() → stopServer()
  → POST /api/stop {force: true}
  → stop_server 路由:
      ├── runner.force_stop() → taskkill /T /F /PID
      ├── 等待最多 2.5s 确认退出
      └── 返回 {success: true, running: false}
  → 前端 updateStatus(false) + stopStatusPoll()

  同时（异步）：
  → reader 线程检测到进程退出 → log_queue.put(("rc", code))
  → SSE 生成器推送 → 前端 onmessage → updateStatus(false)
```

> **双重状态更新**：停止时前端会收到两个状态更新——一个来自 `stopServer` 的响应，一个来自 SSE 的 `rc` 事件。两者都调用 `updateStatus(false)`，幂等无害。

### 3.4 版本更新

```
用户选择资产 → 点击"下载并更新"
  → POST /api/update {url, filename}
  → update_llama 路由:
      ├── 检测 runner.running（运行中拒绝更新）
      ├── 创建 update_id，初始化 version_updates[update_id]
      └── 启动 do_update 后台线程:
            ├── download_file(url, tmp, dl_cb)   — 下载 0-80%
            ├── install_asset(tmp, dir, inst_cb) — 安装 80-100%
            └── version_updates[update_id] 更新进度
  → 前端每 500ms 轮询 GET /api/update/progress/{id}
  → status === 'done' → 显示结果
```

---

## 4. 陷阱清单

### 4.1 llama-server `-fa` 参数

**问题**：`-fa` 不带值时，llama-server 会把下一个参数（如 `-m`）当作 `-fa` 的值，导致模型路径被吞掉。

**解决**：始终显式传值：`cmd.extend(["-fa", "on"])`。不要写 `cmd.append("-fa")`。

### 4.2 Windows 进程终止

**问题**：`proc.kill()` 在 Windows 上只终止父进程，子进程（如 llama-server 启动的 GPU worker）会残留，占用端口和 GPU 内存。

**解决**：用 `taskkill /T /F /PID` 杀整个进程树。`/T` = tree，`/F` = force。

### 4.3 阻塞端点必须用普通 `def`（FastAPI）

**问题**：FastAPI 的 `async def` 路由直接跑在事件循环上，若在其中调用阻塞 IO（`fetch_releases` urllib + sleep、`runner.stop()` 同步等待、`download_file`、`install_asset`），会卡住整个事件循环——SSE 长连接、其他请求全部停滞。

**解决**：阻塞型端点声明为普通 `def`（非 `async def`），FastAPI 自动丢进 anyio 线程池（默认 40 线程）执行，不阻塞事件循环。**只有 `/api/logs` SSE 端点是 `async def`**（需要 `await` 与 `StreamingResponse`）。

### 4.4 SSE 客户端断连

**问题**：客户端在 SSE 长连接期间断开（关闭页面、请求被取代、网络中断）。未处理时 uvicorn 会抛 `asyncio.CancelledError`，产生未处理异常告警。

**解决**：SSE 异步生成器 `try/except (asyncio.CancelledError, GeneratorExit): return` 静默退出。`await request.is_disconnected()` 检测客户端干净断开（发 FIN）时提前退出循环；半开 TCP 由写失败触发取消异常兜底。**只 catch 这两个异常**，不要 `except Exception` 吞掉所有异常（否则断连后循环不退出）。请求读取阶段的连接错误由 uvicorn/Starlette 原生处理，无需手动捕获。

### 4.5 stopServer 状态反转

**问题**：`updateStatus(result.running === false)` 把布尔值反转——服务器停止时（`running: false`），`false === false` 求值为 `true`，导致 `updateStatus(true)` 错误标记为"运行中"。

**解决**：直接传 `updateStatus(result.running)`。

### 4.6 HTTPError str() 不可靠

**问题**：`urllib.error.HTTPError` 构造时传入的 `msg` 参数不被 `str(e)` 返回。`str(HTTPError(...))` 返回的是 `"HTTP Error 403: Forbidden"` 之类的标准格式，不是自定义消息。

**解决**：在 `fetch_releases` 中改用 `RuntimeError("消息")` 抛出用户可见的错误。

### 4.7 uvicorn 关停：`should_exit` 而非 `shutdown()`

**问题**：旧版用 `socketserver` 的 `server_instance.shutdown()`，在请求线程内同步调用会死锁。迁移到 uvicorn 后，`uvicorn.Server` **没有 `shutdown()` 方法**（那是 `socketserver` 的 API），误用会 `AttributeError`。

**解决**：用 `server_instance.should_exit = True`（uvicorn 官方编程式关停入口，置位后 main_loop 下一轮退出，不持有锁、无死锁）。经 `BackgroundTasks` 在响应发送后执行，保证响应先送达客户端。

> **关键**：关停前**必须先 `_shutdown_event.set()`**，否则 SSE 长连接生成器永不返回，会阻塞 uvicorn 优雅关停流程。`lifespan` shutdown（Ctrl+C 路径）与 `_do_shutdown`（`/api/shutdown` 路径）都遵守此顺序。

### 4.8 GitHub API 频率限制

**问题**：匿名访问限 60 次/小时。测试或频繁刷新会很快耗尽。

**解决**：
- 后端：403 时重试 3 次（退避 1s/2s），最终抛出友好错误
- 前端：拉取全部发布后本地过滤，切换平台筛选不重复请求

### 4.9 进度条回退

**问题**：下载阶段进度到 100% 后，安装阶段设为 90%，导致进度条回退。

**解决**：下载映射到 0-80%，安装映射到 80-100%，连续不回退。无 Content-Length 时缓慢推进到 70%。

### 4.10 更新时文件锁定

**问题**：Windows 上 llama-server 运行时 `.exe` 文件被锁定，无法覆盖。

**解决**：`update_llama` 路由检测 `runner.running`，运行中返回 `"请先停止 llama-server 再更新版本"`。

---

## 5. 扩展指南

### 5.1 添加新的 API 端点

1. 用 `@app.get`/`@app.post` 装饰器在 `web/app.py` 注册路由（**必须在静态文件挂载之前**）
2. 阻塞型端点用普通 `def`；只有需要 `await`/`StreamingResponse` 的才用 `async def`
3. GET 路由用 `Query("")` 接 query 参数；POST 路由用 `body: dict = Body(default={})` 接 JSON
4. 成功响应直接 `return dict/list`；**错误响应用 `JSONResponse(status_code=..., content={"error": msg})`**（不要 `raise HTTPException`，否则返回 `{"detail":...}` 与前端契约不符）

```python
# 示例：添加 GET /api/foo
@app.get("/api/foo")
def get_foo():
    return {"bar": "baz"}

# 示例：添加 POST /api/foo（含 body）
@app.post("/api/foo")
def post_foo(body: dict = Body(default={})):
    if "x" not in body:
        return JSONResponse(status_code=400, content={"error": "缺少 x 参数"})
    return {"echo": body["x"]}
```

### 5.2 添加新的参数方案字段

1. 在 `configs/default.json` 中添加字段
2. 在 `build_command()` 中添加对应的命令行构建逻辑
3. 在 README 参数方案表格中记录

```python
# 示例：添加 --threads-cpu 字段
int_or_skip("threads_cpu", "--threads-cpu")
```

### 5.3 修改前端 UI

前端是单文件 `web/static/index.html`，所有 HTML/CSS/JS 内联。修改后刷新浏览器即可生效（无需重启服务器，但需清除缓存）。

CSS 采用白底黑字、面板式极简风格（`#ffffff` 背景，`#f5f5f5` 面板，`#0058a3` 主色），通过 `:root` CSS 变量集中管理配色，无圆角、无阴影、无过渡动画以降低渲染开销。`@media (max-width:640px)` 断点下表单与分栏纵向堆叠、控件全宽，适配手机/平板。

### 5.4 添加新的 llama-server 候选目录

修改 `core/paths.py` 的 `server_executable_candidates` 函数中的 `sub` 列表：

```python
for sub in ("build/bin", "build", "bin", "release", "Release"):
    # 添加新的子目录，如 "dist"
```

### 5.5 修改目录浏览器显示的文件类型

修改 `core/paths.py` 的常量：

```python
BROWSE_FILE_EXTS = (".gguf", ".json")           # 扩展名
BROWSE_FILE_PREFIXES = ("llama-server",)         # 文件名前缀
```

---

## 6. 测试指南

### 6.1 后端单元测试

```bash
# 路径检测
python -c "from core.paths import browse_directory; print(browse_directory(''))"

# 命令构建
python -c "from core.launcher import build_command; print(build_command('llama-server', 'model.gguf', {'threads': 8, 'flash_attn': True}, '127.0.0.1', 8080))"

# 资产名解析
python -c "from core.version_manager import parse_asset_name; print(parse_asset_name('llama-b9860-bin-win-cpu-x64.zip'))"
```

### 6.2 端点测试

```bash
python main.py --no-browser --port 8688

# 测试各端点
curl http://127.0.0.1:8688/api/config
curl http://127.0.0.1:8688/api/status
curl http://127.0.0.1:8688/api/browse?path=
curl http://127.0.0.1:8688/api/platform
```

### 6.3 并发验证（关键）

验证 SSE 长连接不阻塞其他请求：

```bash
# 终端1：保持 SSE 连接
curl -N http://127.0.0.1:8688/api/logs &

# 终端2：同时请求其他端点（应立即响应，不阻塞）
time curl http://127.0.0.1:8688/api/status
```

### 6.4 连接中断测试

验证客户端中途断开不产生 traceback：

```python
import socket
s = socket.socket()
s.connect(("127.0.0.1", 8688))
s.sendall(b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
s.close()  # 立即断开，不等响应
# 服务器日志应无 traceback
```

---

## 7. 已知限制

| 限制 | 说明 |
|------|------|
| GitHub API 60 次/小时 | 匿名访问限制，频繁使用版本管理会触发 |
| 单用户设计 | 无认证机制，不适合公网直接暴露 |
| 模型一级子目录 | `list_model_files` 仅扫描一级子目录 |
| 无进度取消 | 版本更新启动后无法取消 |
| SSE 无心跳 | 长时间无日志时 SSE 连接可能被代理超时断开（前端会自动重连） |

---

## 8. 文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `main.py` | ~72 | 入口：argparse、端口检测、浏览器打开 |
| `requirements.txt` | ~10 | Python 依赖（fastapi + uvicorn） |
| `core/paths.py` | ~118 | 路径检测、模型枚举、目录浏览 |
| `core/profiles.py` | ~65 | JSON 方案加载/保存/枚举 |
| `core/launcher.py` | ~188 | 命令构建 + ServerRunner 进程管理 |
| `core/version_manager.py` | ~259 | GitHub 发布解析、下载、解压替换 |
| `web/app.py` | ~411 | FastAPI 应用、装饰器路由、SSE、init_app/run_server |
| `web/static/index.html` | ~719 | 前端单页应用（HTML+CSS+JS 全内联） |
| `configs/default.json` | ~65 | 默认方案集（cpu/gpu/mix） |
