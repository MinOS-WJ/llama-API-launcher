# 项目开发指南（Project Guide）

> 面向后续开发者和 AI Agent 的速查文档。README.md 面向使用者（功能、安装、使用），
> 本文档面向开发者（架构、约定、认证模型、路线图）。建议先读 README.md 再读本文。

## 项目定位

面向 Windows 和轻量服务器用户的 **llama.cpp 可视化运行控制台** + **OpenAI 兼容 API 工作台**。

核心价值不是替代 llama.cpp，而是把模型选择、参数配置、服务启停、日志查看、版本更新、API 调试做成浏览器界面，降低本机和远程服务器上的使用门槛。同时通过 `/v1/*` 反向代理把 llama-server 包装成本地/远程可用的 OpenAI API 替代品。

## 架构原则

1. **分层清晰**：业务逻辑放 `core/`（纯同步、零 Web 依赖），`web/app.py` 只做路由适配。不要把业务逻辑堆进 `web/app.py`。
2. **轻依赖**：第三方库仅 `fastapi` + `uvicorn`。`/v1/*` 代理用标准库 `http.client`，不引入 httpx/aiohttp。
3. **API 契约双格式**：`/api/*` 管理端点错误用 `{"error": "message"}` 字符串格式；`/v1/*` OpenAI 代理用 `{"error":{"message","type","param","code"}}` 对象格式。不要改成 FastAPI 默认的 `{"detail": ...}`。
4. **异步策略**：阻塞型端点（网络/磁盘/子进程）声明为普通 `def`，FastAPI 自动丢进 anyio 线程池；只有 SSE `/api/logs` 和 `/v1/*` 流式代理是 `async def`。
5. **静态文件最后挂载**：`StaticFiles(html=True)` 挂载在 `/`，API 路由优先匹配；`index.html`（登录页）+ `app.html`（主控台）多页面。
6. **配置即数据**：所有参数集中在 JSON 文件，代码不内嵌硬编码。
7. **新增功能要有明确验收标准**，避免只做 UI 不闭环。

## 认证体系

### 凭证类型

| 凭证 | 前缀 | 存储 | 作用域 | 覆盖路径 |
|------|------|------|--------|---------|
| API Key | `sk-` | `configs/api_keys.json`（SHA-256 hash + prefix） | `admin` / `proxy` | `/v1/*`（始终，proxy scope）+ `/api/*`（admin scope） |
| Session | `sess-` | 内存（仅 SHA-256 hash） | 管理员 | `/api/*` 受保护端点（人通过浏览器登录） |
| Legacy token | 无固定前缀 | `llama_launcher_config.json`（明文） | 主控 | `/api/*` 受保护端点（过渡期兼容） |

### 中间件分流（`AuthMiddleware`，纯 ASGI）

三类路径：

1. **`/v1/*`** — **始终要求 `sk-` Key**（含本机访问，与 OpenAI 官方一致）。失败返回 OpenAI 错误格式（401）。session 不被接受。
2. **`/api/*` 受保护端点** — **本机与远程均需凭证**（密码设置后所有人都须登录）。接受三类凭证：`sk-` admin Key / `sess-` session / legacy `auth_token`。失败返回字符串错误格式（403），session 失效附 `auth_expired: true`。
3. **其余路径**（静态文件、公开 `/api/*`）— 透传。

**公开端点**（不经中间件保护）：`/api/auth/status`、`/api/auth/login`、`/api/auth/setup`（内部校验本机 + 未初始化）、`/api/auth/change-password`（旧密码自证）、`/api/health`、`/api/endpoints`、`/api/config` GET、`/api/models`、`/api/profiles` GET 等。

### 密码与 Session

- 密码哈希：PBKDF2-HMAC-SHA256，200,000 迭代，自描述格式 `pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>`
- Session：`sess-` 前缀，TTL 12h，内存存储（重启失效），懒清理 + lifespan 启动清理
- 登录限流：IP 维度，5 次失败锁 5 分钟
- 改密后所有 session 立即吊销
- 所有密码/token 比对用 `hmac.compare_digest`（常数时间，防时序攻击）
- 忘记密码恢复：删除 `llama_launcher_config.json` 中 `admin_password_hash` 字段后重启

### 前端认证流程

- `index.html` 首屏调 `/api/auth/status`：
  - `session_valid` → 跳 `app.html`
  - `!password_set && is_local` → 显示首次设置表单，设密后自动登录
  - `!password_set && !is_local` → 提示需本机初始化
  - `password_set && !session_valid` → 显示登录表单
- `app.html` 首屏 `requireAuth()` 守卫，session 无效则跳登录页
- `App._apiHeaders()` 自动注入 `Authorization: Bearer <sess-...>`
- 403 `auth_expired: true` → 清 session 跳登录页
- SSE（EventSource 不支持自定义 header）通过 `?token=` query string 传 session

## 前端架构

多文件模块化，全局 `App` 命名空间模式（非 ES module），`<script>` 按序加载：

```
web/static/
├── index.html          # 登录页（登录 / 首次设置 / 远程提示三视图）
├── app.html            # 主控台（侧边栏菜单 + 7 面板）
├── css/
│   ├── main.css        # 设计令牌 + 通用组件
│   └── app.css         # 侧边栏布局 + 登录页 + 响应式
└── js/
    ├── api.js          # API 工具 + session 注入 + 403 处理 + v1 Key
    ├── auth.js         # 登录 / 登出 / 首次设置 / 修改密码 / 首屏守卫
    ├── app.js          # 主控台初始化 + 菜单切换 + 状态/启停/SSE 日志
    ├── config.js       # 路径配置 + 原生文件选择器 + 健康检查
    ├── profiles.js     # 参数方案编辑器（列表 + 表单分栏）
    ├── workbench.js    # API 工作台（Chat / Embeddings / Rerank / 模型 / 示例 / Prompt）
    ├── security.js     # API Key 管理 + 管理员密码管理
    └── version.js      # 版本管理 + 回滚
```

新增功能按职责拆分到对应模块，不要堆进单个文件。

## 开发约定

- **端口**：Web 管理端口默认 8686；llama-server 推理端口默认 8080（独立配置）
- **错误响应**：`JSONResponse(content={"error": msg})`，不用 `HTTPException`
- **API Key 管理**：写操作用模块级 `threading.Lock` 序列化；明文仅创建时返回一次
- **受保护路径**：拆为 `PROTECTED_EXACT`（精确匹配）与 `PROTECTED_PREFIX`（前缀匹配，如 `/api/keys/{id}`）
- **Windows 进程管理**：停止 llama-server 用 `taskkill /T /F /PID`（非 `proc.kill()`）；更新时考虑 `.exe` 文件锁定
- **GitHub API**：匿名限流 60 次/小时，需重试 + 指数退避（1s, 2s, max 3 次）
- **连接容错**：`send_json/send_file/send_sse` 须捕获 `ConnectionError` 静默处理；客户端断开不产生 traceback
- **版本更新进度**：下载阶段映射 0-80%，安装阶段 80-100%，避免进度回退
- **`-fa`/`--flash-attn` 参数**：必须带显式值（`on`/`off`/`auto`），否则会吞掉下一个参数
- **原生文件选择器**：服务端 subprocess 调起 OS 对话框（Windows PowerShell / Linux zenity / macOS osascript），远程无头服务器返回空串前端回退手动输入

## 测试

```bash
python -m unittest discover tests
```

- 207 项测试（44 项跳过 = httpx 未安装时的 TestClient 条件跳过）
- 测试不依赖真实 llama.cpp、真实模型文件或真实 GitHub 网络
- 网络访问 GitHub 的测试要 mock；API 测试用 `httpx` mock，不依赖真实 llama-server
- 关键测试模块：`test_launcher` / `test_paths` / `test_profiles` / `test_version_manager` / `test_api_client` / `test_api_keys`（36 项）/ `test_admin_auth`（52 项）/ `test_v1_api`（20 项）

## 路线图

### 已完成

- ✅ 编码与文档可读性（UTF-8 无 BOM + Windows 终端乱码处理提示）
- ✅ 基础安全保护（CORS 同源 + 认证 + 危险接口保护）
- ✅ 多 API Key 管理体系（生成 / 命名 / 启停 / 回收 / 作用域）
- ✅ 管理员登录认证（PBKDF2 + session + 限流 + 本机也须登录）
- ✅ OpenAI 兼容 `/v1/*` 反向代理（流式透传，OpenAI SDK 直接可用）
- ✅ 启动前健康检查（`/api/healthcheck` + 结构化 errors/warnings）
- ✅ 参数方案编辑器（UI 新建 / 修改 / 删除 / 重命名 + 字段校验）
- ✅ 版本管理与回滚（`/api/update/rollback` + 备份检测）
- ✅ 模型与服务状态增强（model/profile/host/port/exit_code + API 地址复制）
- ✅ 原生文件选择器（`/api/pick` 调起 OS 对话框）
- ✅ 多文件模块化前端（登录页 + 菜单式控制台）

### 待实现

- ⬜ **OpenAI 兼容代理参数过滤**：`/v1/*` 转发前清洗 llama-server 不支持的参数（如 `previous_response_id`、`reasoning`），修复 Copilot Chat / VS Code 等客户端 400 错误。实现位置：`core/proxy.py` 新增 `sanitize_request_body(path, body_bytes)` 纯函数 + 黑名单常量。
- ⬜ 模型目录递归与索引（可配置深度 + 大小/时间排序）
- ⬜ 日志体验改进（级别过滤已有，可补：常见错误摘要提示）
