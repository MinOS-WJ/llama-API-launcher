#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— Web 后端（FastAPI 实现）。

关键设计：
- FastAPI + uvicorn 取代标准库 ThreadingHTTPServer。
- 阻塞型端点（网络/磁盘/子进程/同步 sleep）声明为普通 ``def``，FastAPI 自动
  丢进 anyio 线程池执行，不阻塞事件循环；SSE 长连接与 ``/v1/*`` 反向代理是 ``async def``。
- ``core/`` 模块保持纯同步、零 web 依赖，路由直接调用其函数。
- 错误响应有两种格式：``/api/*`` 用 ``JSONResponse(content={"error": msg})`` 字符串格式
  （匹配前端契约）；``/v1/*`` 用 OpenAI 对象格式 ``{"error":{"message","type","param","code"}}``。
- ``/v1/*`` 是 OpenAI 兼容反向代理：透明转发到 llama-server，始终要求 sk- API Key
  （含本机访问），支持 SSE 流式（``http.client`` + ``read1``）。
- 安全：``/api/*`` 管理端点本机与远程均需凭证（密码设置后须登录）；``/v1/*`` 始终需 sk- Key；CORS 默认同源。
"""

import asyncio
import hmac
import json
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.paths import (browse_directory, check_port_bindable, detect_llamacpp,
                        list_model_files, model_exists, resolve_model_path,
                        server_executable_candidates)
from core.profiles import (ProfileManager, list_config_files, load_json,
                           normalize_profile, save_json, validate_profile)
from core.launcher import ServerRunner, build_command, quote_arg
from core import admin_auth
from core import api_client
from core import api_keys
from core import filepicker
from core import proxy as proxy_core
from core import version_manager as vm

APP_NAME = "llama.cpp API 启动器"
CONFIG_FILENAME = "llama_launcher_config.json"
DEFAULT_PROFILES_REL = "configs/default.json"
PROMPTS_REL = "configs/prompts.json"
API_KEYS_REL = "configs/api_keys.json"

# -------------------- 全局状态 --------------------
# 与旧版命名一一对应，便于对照；init_app 在 main.py 启动时初始化。
base_dir = ""
config_path = ""
config_data = {}
prompts_path = ""
api_keys_path = ""       # API Key 管理文件路径
pm = None                 # ProfileManager 实例
runner = ServerRunner()   # llama-server 子进程封装
version_updates = {}      # {update_id: {status, progress, message, success}}
server_instance = None    # uvicorn.Server 实例（用于编程式关停）
web_host = "127.0.0.1"    # 启动器自身监听地址（run_server 赋值）
web_port = 8686           # 启动器自身监听端口
_shutdown_event = threading.Event()  # 通知 SSE 线程退出

# 受保护的危险接口（方法, 路径）；本机与远程均需凭证（密码设置后所有人都须登录）。
# 拆为两组：精确匹配（PROTECTED_EXACT）与前缀匹配（PROTECTED_PREFIX，如 /api/keys/{id}）。
PROTECTED_EXACT = {
    ("POST", "/api/start"),
    ("POST", "/api/stop"),
    ("POST", "/api/update"),
    ("POST", "/api/update/rollback"),
    ("POST", "/api/shutdown"),
    ("POST", "/api/config"),
    ("POST", "/api/profiles/path"),
    ("POST", "/api/profiles/save"),
    ("POST", "/api/profiles/delete"),
    ("POST", "/api/prompts"),
    ("GET", "/api/keys"),
    ("POST", "/api/keys"),
    ("POST", "/api/keys/toggle"),
    ("POST", "/api/keys/rename"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/pick"),
}
# 前缀匹配的受保护路径（不含方法，任何方法都需认证）。
# 注意：/api/auth/ 下除 logout 外其余端点（login/setup/change-password/status）
# 均不在此列——它们是登录入口或自证接口，不能被认证拦截。
PROTECTED_PREFIX = ("/api/keys/",)
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


# -------------------- 生命周期 --------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：Ctrl+C 与 /api/shutdown 共用同一条关停清理路径。"""
    # 启动时清理过期 session（服务重启后内存表已空，此处理论上无操作，
    # 但保留以应对未来持久化场景）
    admin_auth.cleanup_expired()
    yield
    # 关停清理：先通知 SSE 生成器退出，再强制结束子进程
    _shutdown_event.set()
    if runner.running:
        print("正在停止 llama-server...")
        runner.force_stop()


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)


# -------------------- 认证中间件（纯 ASGI，内层）--------------------
# ⚠️ 不使用 @app.middleware("http") / BaseHTTPMiddleware：后者会包装响应 body 流，
# Ctrl+C 关停时 asyncio 取消事件循环会中断 body_stream，产生 CancelledError →
# WouldBlock 级联 traceback。纯 ASGI 中间件直接透传 receive/send，不包装 body，
# 关停时无 body 迭代器被取消，traceback 消失。
# 注册顺序：本中间件先 add_middleware（内层），init_app 再 add CORSMiddleware（外层）。

def _extract_bearer_token(scope):
    """从 ASGI scope 的 headers / query string 中提取 token。"""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1")
            if text.lower().startswith("bearer "):
                return text[7:].strip()
            return text.strip()
        if name == b"x-auth-token":
            return value.decode("latin-1").strip()
    qs = scope.get("query_string", b"")
    if qs:
        for pair in qs.decode("latin-1").split("&"):
            if pair.startswith("token="):
                return pair[6:]
    return ""


async def _send_json_error(send, status_code, message, auth_expired=False):
    """纯 ASGI 方式发送 JSON 错误响应（不经 BaseHTTPMiddleware 包装）。

    ``auth_expired=True`` 时附带该字段，前端据此区分「session 失效→跳登录」
    与「权限不足→提示」。
    """
    payload = {"error": message}
    if auth_expired:
        payload["auth_expired"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_openai_error(send, status_code, message,
                             error_type="invalid_request_error", code=None):
    """纯 ASGI 方式发送 OpenAI 格式错误响应（用于 /v1/* 路径）。

    OpenAI 错误结构：{"error":{"message","type","param":null,"code"}}
    """
    body = json.dumps({
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": code,
        }
    }, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _openai_error_dict(message, error_type="invalid_request_error", code=None):
    """构造 OpenAI 错误 dict（供路由 handler 用 JSONResponse 返回）。"""
    return {"error": {"message": message, "type": error_type, "param": None, "code": code}}


def _is_protected(method, path):
    """判定 (method, path) 是否落在受保护集合。

    精确匹配走 PROTECTED_EXACT；前缀匹配走 PROTECTED_PREFIX（如 /api/keys/{id}）。
    """
    if (method, path) in PROTECTED_EXACT:
        return True
    return any(path.startswith(pfx) for pfx in PROTECTED_PREFIX)


class AuthMiddleware:
    """纯 ASGI 认证中间件（三类路径分流）。

    类别 1 ``/v1/*`` — OpenAI 兼容推理 API：**始终要求 sk- Key**（含本机访问），
           失败返回 OpenAI 错误格式（401），required_scope = proxy。session 不被接受。
    类别 2 ``/api/*`` 受保护 — 管理端点：**本机与远程均需凭证**（密码一旦设置，
           所有人都须登录），接受两类凭证：
           a) sk- admin Key
           b) sess- session（管理员登录，主路径）
           失败返回字符串错误格式（403），session 失效附 ``auth_expired: true``。
           注：未设密码时前端引导本机完成 ``/api/auth/setup``（该端点内部校验本机，
           不经此中间件保护），设密后自动登录获得 session。
    类别 3 其余路径（静态文件、非受保护 /api/*）— 透传。
    OPTIONS 预检一律放行（由外层 CORS 处理）。
    """

    def __init__(self, app, **kwargs):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 非 HTTP 请求（如 lifespan）直接透传
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        raw_path = scope.get("path", "") or "/"
        # 去掉末尾斜杠用于精确匹配；前缀匹配用 raw_path（/api/keys/{id} 无尾斜杠）
        path = raw_path.rstrip("/") or "/"
        # OPTIONS 预检放行
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # ── 类别 1：/v1/* — 始终要求 sk- Key（含本机），OpenAI 错误格式 ──
        if path == "/v1" or path.startswith("/v1/"):
            provided = _extract_bearer_token(scope)
            if not provided or not provided.startswith(api_keys.KEY_PREFIX):
                await _send_openai_error(send, 401,
                    "No API key provided. Set the 'Authorization' header with a valid sk- key.",
                    "invalid_request_error", "missing_api_key")
                return
            ok, info = api_keys.verify(api_keys_path, provided, "proxy")
            if not ok:
                await _send_openai_error(send, 401, str(info),
                    "invalid_request_error", "invalid_api_key")
                return
            await self.app(scope, receive, send)
            return

        # ── 类别 2：/api/* 受保护 — 本机与远程均需凭证，字符串错误 ──
        if _is_protected(method, path):
            provided = _extract_bearer_token(scope)
            if not provided:
                await _send_json_error(send, 403, "未授权：缺少凭证。请登录后重试。")
                return
            # a) sk- API Key：管理端点需 admin scope（不变）
            if provided.startswith(api_keys.KEY_PREFIX):
                ok, info = api_keys.verify(api_keys_path, provided, "admin")
                if ok:
                    await self.app(scope, receive, send)
                    return
                await _send_json_error(send, 403, f"未授权：{info}")
                return
            # b) sess- session token（管理员登录）
            if provided.startswith(admin_auth.SESSION_PREFIX):
                ok, info = admin_auth.verify_session(provided)
                if ok:
                    await self.app(scope, receive, send)
                    return
                await _send_json_error(send, 403, f"未授权：{info}",
                                       auth_expired=True)
                return
            # 拒绝：凭证不匹配
            await _send_json_error(
                send, 403,
                "未授权：无效凭证。请登录后重试，或携带有效的 API Key。")
            return

        # ── 类别 3：其余路径（静态文件、/api/* 非受保护）— 透传 ──
        await self.app(scope, receive, send)


# 注册认证中间件（内层）；CORS 在 init_app 中后注册（外层）
app.add_middleware(AuthMiddleware)


def init_app(base):
    """初始化全局状态：加载配置、方案管理器、运行器、CORS。"""
    global base_dir, config_path, config_data, prompts_path, api_keys_path, pm, runner, version_updates
    base_dir = base
    config_path = str(Path(base) / CONFIG_FILENAME)
    prompts_path = str(Path(base) / PROMPTS_REL)
    api_keys_path = str(Path(base) / API_KEYS_REL)
    # 默认配置：旧配置文件缺失的字段会被补齐，保证新功能可用
    defaults = {
        "llamacpp_dir": "", "model_dir": "", "profiles_path": "",
        "last_model": "", "current_profile": "",
        "host": "127.0.0.1", "port": 8080,
        "allowed_origins": [],
        "github_token": "",             # GitHub Personal Access Token（可选，提升 API 速率限制）
        "admin_password_hash": "",      # 管理员密码 PBKDF2 哈希（空=未初始化）
        "admin_auth_enabled": True,     # 是否启用管理员登录认证
    }
    config_data = load_json(config_path, defaults)
    if not isinstance(config_data, dict):
        config_data = dict(defaults)
    else:
        # 合并：补齐旧配置文件中缺失的新字段（不覆盖已有值）
        for k, v in defaults.items():
            config_data.setdefault(k, v)
    # 默认指向 configs/default.json（消除旧版 user.json 不一致）
    profiles_path = (config_data.get("profiles_path")
                     or str(Path(base) / DEFAULT_PROFILES_REL))
    pm = ProfileManager(profiles_path)
    config_data["profiles_path"] = profiles_path  # 回写保证一致
    runner = ServerRunner()
    version_updates = {}
    _shutdown_event.clear()
    # CORS：默认同源（不挂中间件），仅当配置 allowed_origins 时放开
    _configure_cors(config_data.get("allowed_origins", []))


_cors_configured = False


def _configure_cors(allowed_origins):
    """根据配置挂载 CORSMiddleware（仅一次）。空列表=同源不挂。"""
    global _cors_configured
    if _cors_configured:
        return
    origins = [o for o in (allowed_origins or []) if isinstance(o, str) and o]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )
    _cors_configured = True


def save_config():
    """持久化 config_data 到 config_path。"""
    try:
        save_json(config_path, config_data)
        return True, None
    except Exception as e:
        return False, str(e)


# -------------------- 配置管理 --------------------

@app.get("/api/config")
def get_config():
    detect_status, detect_msg, _ = detect_llamacpp(config_data.get("llamacpp_dir", ""))
    return {
        "llamacpp_dir": config_data.get("llamacpp_dir", ""),
        "model_dir": config_data.get("model_dir", ""),
        "profiles_path": pm.path if pm else "",
        "last_model": config_data.get("last_model", ""),
        "current_profile": config_data.get("current_profile", ""),
        "host": config_data.get("host", "127.0.0.1"),
        "port": config_data.get("port", 8080),
        "detect_status": detect_status,
        "detect_msg": detect_msg,
        "github_token_set": bool(config_data.get("github_token", "")),
        "github_token_url": "https://github.com/settings/tokens/new?scopes=public_repo",
        "remote_access": _is_remote_listening(),
    }


@app.post("/api/config")
def set_config(body: dict = Body(default={})):
    for key in ("llamacpp_dir", "model_dir", "host", "port",
                "allowed_origins", "github_token"):
        if key in body:
            config_data[key] = body[key]

    detect_status, detect_msg, _ = detect_llamacpp(config_data.get("llamacpp_dir", ""))
    success, err = save_config()
    if not success:
        return JSONResponse(status_code=500, content={"error": err})
    # 若 allowed_origins 变更，提示需重启生效（中间件仅启动时挂载一次）
    cors_note = ""
    if "allowed_origins" in body and not _cors_configured:
        _configure_cors(config_data.get("allowed_origins", []))
    elif "allowed_origins" in body:
        cors_note = "CORS 已在启动时加载，新配置需重启生效"
    return {
        "detect_status": detect_status,
        "detect_msg": detect_msg,
        "models": list_model_files(config_data.get("model_dir", "")),
        "cors_note": cors_note,
    }


def _is_remote_listening():
    """判断 Web 服务器是否绑定在非本机地址（用于前端风险提示）。"""
    # 此处用 config 中的 host 字段近似判断（实际绑定由 main.py --host 决定）
    host = str(config_data.get("host", "127.0.0.1"))
    return host in ("0.0.0.0", "::")


@app.get("/api/models")
def get_models():
    return list_model_files(config_data.get("model_dir", ""))


@app.get("/api/profiles")
def get_profiles():
    return {
        "names": pm.names() if pm else [],
        "current": config_data.get("current_profile", ""),
    }


@app.post("/api/profiles/path")
def set_profiles_path(body: dict = Body(default={})):
    if "path" not in body:
        return JSONResponse(status_code=400, content={"error": "缺少 path 参数"})
    path = body["path"]
    pm.load(path)
    config_data["profiles_path"] = path
    success, err = save_config()
    if not success:
        return JSONResponse(status_code=500, content={"error": err})
    return {"names": pm.names()}


@app.get("/api/configs/list")
def list_config_files_route():
    configs_dir = Path(base_dir) / "configs"
    files = list_config_files(configs_dir)
    return [Path(f).name for f in files]


# 注意：该端点前端不发 body（仅带 Content-Type header），故不声明 body 参数。
@app.post("/api/configs/load/{filename}")
def load_config_file(filename: str):
    configs_dir = Path(base_dir) / "configs"
    target = configs_dir / filename
    if not target.exists():
        return JSONResponse(status_code=404, content={"error": "文件不存在"})
    pm.load(str(target))
    config_data["profiles_path"] = str(target)
    success, err = save_config()
    if not success:
        return JSONResponse(status_code=500, content={"error": err})
    return {"names": pm.names()}


# -------------------- 参数方案编辑器 --------------------

@app.get("/api/profiles/get/{name}")
def get_profile(name: str):
    prof = pm.get_profile(name) if pm else None
    if prof is None:
        return JSONResponse(status_code=404, content={"error": f"方案 {name} 不存在"})
    return {"name": name, "profile": prof}


@app.post("/api/profiles/save")
def save_profile(body: dict = Body(default={})):
    """新增/覆盖方案。body: {name, profile, set_current?}。
    字段经 normalize_profile 规整，非法值返回 warnings。
    """
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "缺少方案名 name"})
    raw = body.get("profile", {})
    prof, norm_warnings = normalize_profile(raw)
    errors, val_warnings = validate_profile(prof)
    if errors:
        return JSONResponse(status_code=400, content={"error": "；".join(errors)})
    if not pm.upsert(name, prof):
        return JSONResponse(status_code=400, content={"error": "方案数据无效"})
    if not pm.save():
        return JSONResponse(status_code=500, content={"error": "保存文件失败"})
    if body.get("set_current"):
        config_data["current_profile"] = name
        save_config()
    return {"names": pm.names(), "current": config_data.get("current_profile", ""),
            "warnings": norm_warnings + val_warnings}


@app.post("/api/profiles/delete")
def delete_profile(body: dict = Body(default={})):
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse(status_code=400, content={"error": "缺少方案名 name"})
    if not pm.delete(name):
        return JSONResponse(status_code=404, content={"error": f"方案 {name} 不存在"})
    if not pm.save():
        return JSONResponse(status_code=500, content={"error": "保存文件失败"})
    if config_data.get("current_profile") == name:
        config_data["current_profile"] = ""
        save_config()
    return {"names": pm.names(), "current": config_data.get("current_profile", "")}


# -------------------- 目录浏览 --------------------

@app.get("/api/browse")
def browse(path: str = Query(""), filter: str = Query("")):
    result = browse_directory(path, file_filter=filter)
    return JSONResponse(content=result,
                        status_code=200 if not result.get("error") else 400)


# -------------------- 服务控制 --------------------

@app.get("/api/status")
def get_status():
    """返回服务运行状态与运行元信息（模型/方案/host/port/启动时间/退出码）。"""
    return {
        "running": runner.running,
        "pid": runner.pid,
        "command": runner.command,
        "model": runner.model,
        "profile": runner.profile,
        "host": runner.host,
        "port": runner.port,
        "start_time": runner.start_time,
        "exit_code": runner.exit_code,
        "exit_time": runner.exit_time,
    }


@app.get("/api/healthcheck")
def healthcheck(model: str = Query(""), profile: str = Query(""),
                host: str = Query(""), port: int = Query(None)):
    """启动前健康检查，返回结构化 {ok, errors, warnings}。
    严重错误（errors）应阻止启动；警告（warnings）允许继续。
    """
    errors, warnings = [], []

    # 1. llama-server 是否存在
    llamacpp_dir = config_data.get("llamacpp_dir", "").strip()
    cands = server_executable_candidates(llamacpp_dir)
    if not cands:
        errors.append("未找到 llama-server 可执行文件，请检查 llama.cpp 目录")

    # 2. 模型文件
    model_val = (model or config_data.get("last_model", "")).strip()
    if not model_val:
        errors.append("未选择模型")
    else:
        model_full = resolve_model_path(model_val, config_data.get("model_dir", ""))
        exists, is_gguf = model_exists(model_full)
        if not exists:
            errors.append(f"模型文件不存在：{model_full}")
        elif not is_gguf:
            errors.append(f"模型文件后缀非 .gguf：{model_full}")

    # 3. 参数方案
    profile_val = (profile or config_data.get("current_profile", "")).strip()
    prof = pm.get(profile_val) if pm and profile_val else None
    if not profile_val:
        errors.append("未选择参数方案")
    elif not prof:
        errors.append(f"参数方案 {profile_val} 不存在")
    else:
        p_err, p_warn = validate_profile(prof)
        errors.extend(p_err)
        warnings.extend(p_warn)
        # 常见不合理组合提示（警告）
        if prof.get("embedding") and prof.get("gpu_layers", 0) == -1:
            warnings.append("embedding 模式下 gpu_layers=-1 可能无意义")

    # 4. 端口可绑定
    host_val = (host or config_data.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    port_val = port or config_data.get("port", 8080)
    port_ok, port_err = check_port_bindable(host_val, port_val)
    if not port_ok:
        # 运行中的 llama-server 自身会占用该端口，属正常
        if runner.running and runner.host == host_val and runner.port == int(port_val):
            warnings.append(f"端口 {port_val} 已被当前 llama-server 占用（运行中）")
        else:
            errors.append(f"端口 {host_val}:{port_val} 无法绑定：{port_err}")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


@app.post("/api/start")
def start_server(body: dict = Body(default={})):
    if runner.running:
        return JSONResponse(status_code=400, content={"error": "服务已在运行"})

    llamacpp_dir = config_data.get("llamacpp_dir", "").strip()
    cands = server_executable_candidates(llamacpp_dir)
    if not cands:
        return JSONResponse(status_code=400, content={"error": "未找到 llama-server"})

    model = body.get("model", config_data.get("last_model", "")).strip()
    if not model:
        return JSONResponse(status_code=400, content={"error": "请选择模型"})

    profile_name = body.get("profile", config_data.get("current_profile", "")).strip()
    profile = pm.get(profile_name) if pm else None
    if not profile:
        return JSONResponse(status_code=400, content={"error": "请选择参数方案"})

    port_val = body.get("port", config_data.get("port", 8080))
    try:
        port_val = int(port_val)
        if not (1 <= port_val <= 65535):
            raise ValueError
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "端口必须是 1-65535 的整数"})

    host_val = body.get("host", config_data.get("host", "127.0.0.1")).strip()
    model_dir = config_data.get("model_dir", "")
    model_full = model if Path(model).is_absolute() else str(Path(model_dir) / model)

    try:
        cmd = build_command(cands[0], model_full, profile, host=host_val, port=port_val)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"构建命令失败: {e}"})

    config_data["last_model"] = model
    config_data["current_profile"] = profile_name
    config_data["host"] = host_val
    config_data["port"] = port_val
    save_config()

    cmd_str = " ".join(quote_arg(c) for c in cmd)
    success = runner.start(cmd, on_started=None, on_error=lambda e: None)
    if not success:
        return JSONResponse(status_code=500, content={"error": "启动失败"})
    # 记录本次启动的业务上下文，供状态接口展示
    runner.set_runtime_info(model=model, profile=profile_name,
                            host=host_val, port=port_val)
    return {"command": cmd_str, "api_base": api_client.base_url(host_val, port_val)}


@app.post("/api/stop")
def stop_server(body: dict = Body(default={})):
    if not runner.running:
        return JSONResponse(status_code=400, content={"error": "服务未运行"})
    force = body.get("force", False)
    if force:
        runner.force_stop()
    else:
        runner.stop()
    # 等待最多 2.5 秒确认退出
    for _ in range(5):
        time.sleep(0.5)
        if not runner.running:
            break
    if runner.running:
        runner.force_stop()
    return {"success": True, "running": runner.running}


@app.post("/api/shutdown")
def shutdown(background: BackgroundTasks):
    # 先响应客户端，BackgroundTasks 在响应发送后才执行关停
    background.add_task(_do_shutdown)
    return {"success": True}


def _do_shutdown():
    """关停：先通知 SSE 退出，再强制结束子进程，最后触发 uvicorn 优雅关停。"""
    _shutdown_event.set()
    if runner.running:
        runner.force_stop()
    if server_instance is not None:
        # uvicorn 官方编程式关停入口：置位后 main_loop 下一轮退出。
        # 不持有锁、不在请求线程内同步等待，无死锁。
        server_instance.should_exit = True


# -------------------- SSE 日志流（唯一 async def）--------------------

@app.get("/api/logs")
async def stream_logs(request: Request):
    async def gen():
        try:
            while not _shutdown_event.is_set():
                # 客户端干净断开（发 FIN）时检测到，提前退出
                if await request.is_disconnected():
                    return
                drained = runner.drain()
                for kind, data in drained:
                    payload = json.dumps({"kind": kind, "data": data},
                                         ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                if not drained:
                    await asyncio.sleep(0.08)  # 无数据时让出 CPU
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开 / uvicorn 关停连接时取消生成器任务，正常退出。
            # 不要用 except Exception 吞掉所有异常，否则断连后循环不退出。
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# -------------------- 版本管理 --------------------

@app.get("/api/releases")
def get_releases(os: str = Query(""), force: bool = Query(False)):
    try:
        releases = vm.fetch_releases(force_refresh=force, github_token=config_data.get("github_token"))
        group_map = {"Windows": "windows", "Linux": "linux",
                     "macOS": "macos", "其他": "others"}
        target_group = (group_map.get(os, "")
                        if os and os != "全部" else "")
        filtered = []
        for r in releases:
            assets = []
            for a in r["assets"]:
                group = vm.asset_os_group(a["info"])
                if target_group and group != target_group:
                    continue
                assets.append({
                    "name": a["name"],
                    "url": a["url"],
                    "size": a["size"],
                    "variant": vm.variant_label(a["info"]),
                    "os_group": group,  # 返回给前端，前端本地过滤
                })
            if assets:
                filtered.append({
                    "tag": r["tag"],
                    "name": r["name"],
                    "published": r["published"],
                    "assets": assets,
                })
        return filtered
    except Exception as e:
        msg = str(e).strip()
        if not msg:
            msg = f"{type(e).__name__}（无详细错误信息）"
        if "频率限制" in msg or "rate limit" in msg.lower():
            msg = "GitHub API 访问频率限制，请稍后再试（约1小时后恢复）。"
        elif "timed out" in msg.lower() or "timeout" in msg.lower():
            msg = "网络请求超时，请检查网络连接后重试。"
        elif "connection" in msg.lower() and "refused" in msg.lower():
            msg = "无法连接到 GitHub，请检查网络连接。"
        return JSONResponse(status_code=500, content={"error": msg})


@app.post("/api/update")
def update_llama(body: dict = Body(default={})):
    if "url" not in body:
        return JSONResponse(status_code=400, content={"error": "缺少 url 参数"})
    url = body["url"]
    filename = body.get("filename", "")
    llamacpp_dir = config_data.get("llamacpp_dir", "").strip()
    if not llamacpp_dir:
        return JSONResponse(status_code=400, content={"error": "未设置 llama.cpp 目录"})
    if runner.running:
        return JSONResponse(status_code=400,
                             content={"error": "请先停止 llama-server 再更新版本"})

    update_id = str(int(time.time() * 1000))
    version_updates[update_id] = {"status": "running", "progress": 0, "message": "准备下载…"}

    def do_update():
        tmp_path = None
        try:
            suffix = ".tar.gz" if filename.lower().endswith((".tar.gz", ".tgz")) else ".zip"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)

            # 进度区间：下载 0-80%，安装 80-100%，避免回退
            def dl_cb(done, total):
                if total:
                    pct = done * 80.0 / total
                    version_updates[update_id]["progress"] = pct
                    version_updates[update_id]["message"] = (
                        f"下载 {done // 1024}/{total // 1024} KB（{pct:.0f}%）")
                else:
                    # 无 Content-Length 时仅缓慢推进到 70%，避免卡 0%
                    pct = min(70.0, version_updates[update_id].get("progress", 0) + 0.3)
                    version_updates[update_id]["progress"] = pct
                    version_updates[update_id]["message"] = f"下载 {done // 1024} KB（大小未知）"

            vm.download_file(url, tmp_path, progress_cb=dl_cb)
            version_updates[update_id]["message"] = "正在解压并替换文件…"
            version_updates[update_id]["progress"] = 80

            def inst_cb(phase, cur, total):
                if total:
                    version_updates[update_id]["progress"] = 80 + cur * 20.0 / total
                    version_updates[update_id]["message"] = f"{phase}: {cur}/{total}"
                else:
                    version_updates[update_id]["message"] = f"{phase}…"

            ok, msg = vm.install_asset(tmp_path, llamacpp_dir, progress_cb=inst_cb)
            version_updates[update_id]["status"] = "done"
            version_updates[update_id]["progress"] = 100
            version_updates[update_id]["message"] = msg
            version_updates[update_id]["success"] = ok
        except Exception as e:
            version_updates[update_id]["status"] = "done"
            version_updates[update_id]["progress"] = 0
            version_updates[update_id]["message"] = str(e)
            version_updates[update_id]["success"] = False
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    threading.Thread(target=do_update, daemon=True).start()
    return {"update_id": update_id}


@app.get("/api/update/progress/{update_id}")
def update_progress(update_id: str):
    return version_updates.get(update_id, {"status": "not_found"})


@app.get("/api/update/backup_status")
def backup_status():
    """查询 _backup 是否存在且可回滚。"""
    llamacpp_dir = config_data.get("llamacpp_dir", "").strip()
    if not llamacpp_dir:
        return {"has_backup": False, "running": runner.running}
    return {"has_backup": vm.has_backup(llamacpp_dir), "running": runner.running}


@app.post("/api/update/rollback")
def rollback_llama():
    """回滚到 _backup 中的上一个版本。运行中拒绝回滚。"""
    llamacpp_dir = config_data.get("llamacpp_dir", "").strip()
    if not llamacpp_dir:
        return JSONResponse(status_code=400, content={"error": "未设置 llama.cpp 目录"})
    if runner.running:
        return JSONResponse(status_code=400,
                            content={"error": "请先停止 llama-server 再回滚版本"})
    if not vm.has_backup(llamacpp_dir):
        return JSONResponse(status_code=400,
                            content={"error": "未找到可恢复的 _backup 备份，无法回滚"})

    update_id = str(int(time.time() * 1000))
    version_updates[update_id] = {"status": "running", "progress": 0,
                                  "message": "准备回滚…"}

    def do_rollback():
        try:
            def rb_cb(cur, total):
                if total:
                    version_updates[update_id]["progress"] = cur * 100.0 / total
                    version_updates[update_id]["message"] = f"回滚: {cur}/{total}"
                else:
                    version_updates[update_id]["message"] = "回滚中…"

            ok, msg = vm.rollback_asset(llamacpp_dir, progress_cb=rb_cb)
            version_updates[update_id]["status"] = "done"
            version_updates[update_id]["progress"] = 100 if ok else 0
            version_updates[update_id]["message"] = msg
            version_updates[update_id]["success"] = ok
        except Exception as e:
            version_updates[update_id]["status"] = "done"
            version_updates[update_id]["progress"] = 0
            version_updates[update_id]["message"] = str(e)
            version_updates[update_id]["success"] = False

    threading.Thread(target=do_rollback, daemon=True).start()
    return {"update_id": update_id}


@app.get("/api/platform")
def get_platform():
    return {
        "os": vm.current_os_filter_label(),
        "os_token": vm.current_os_token(),
        "arch": vm.current_arch_token(),
    }


# -------------------- OpenAI 兼容 /v1/* 反向代理 --------------------

def _runtime_api_base():
    """返回启动器自身的 base URL（OpenAI 客户端应指向此处）。"""
    host = web_host or "127.0.0.1"
    port = web_port or 8686
    return f"http://{host}:{port}"


def _backend_api_base():
    """返回 llama-server 直连地址（调试用，非客户端使用）。"""
    if runner.running and runner.host and runner.port:
        return api_client.base_url(runner.host, runner.port)
    host = config_data.get("host", "127.0.0.1") or "127.0.0.1"
    port = config_data.get("port", 8080) or 8080
    return api_client.base_url(host, port)


@app.get("/api/endpoints")
def get_endpoints():
    """返回启动器的 OpenAI 兼容 base_url 与端点路径，供前端展示与复制。"""
    base = _runtime_api_base()       # 启动器 URL（客户端用）
    backend = _backend_api_base()    # llama-server 直连（调试用）
    running = runner.running
    return {
        "base_url": base,
        "backend_url": backend,
        "running": running,
        "model": runner.model,
        "endpoints": {
            "chat_completions": f"{base}/v1/chat/completions",
            "completions": f"{base}/v1/completions",
            "embeddings": f"{base}/v1/embeddings",
            "rerank": f"{base}/v1/rerank",
            "models": f"{base}/v1/models",
        },
        "auth_required": True,   # /v1/* 始终需要 Key
    }


@app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def v1_reverse_proxy(rest: str, request: Request):
    """OpenAI 兼容反向代理：透明转发 ``/v1/*`` 到 llama-server。

    认证已由 AuthMiddleware 完成（sk- Key 必验，含本机）。
    用 ``http.client`` + ``read1`` 实现 SSE 流式转发，不引入第三方依赖。
    """
    if not runner.running:
        return JSONResponse(status_code=503,
            content=_openai_error_dict("llama-server is not running", "api_error"))

    upstream_path = f"/v1/{rest}"
    method = request.method
    # 读取请求体（POST/PUT/PATCH）
    body = await request.body() if method in ("POST", "PUT", "PATCH") else b""

    loop = asyncio.get_event_loop()

    def _do_request():
        return proxy_core.open_upstream(
            runner.host, runner.port, method, upstream_path,
            dict(request.headers), body)

    try:
        conn, resp = await loop.run_in_executor(None, _do_request)
    except (ConnectionError, OSError) as e:
        return JSONResponse(status_code=502,
            content=_openai_error_dict(f"upstream connection failed: {e}", "api_error"))
    except Exception as e:
        return JSONResponse(status_code=502,
            content=_openai_error_dict(f"proxy error: {e}", "api_error"))

    resp_headers = proxy_core.filter_response_headers(resp.getheaders())

    async def _stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                # read1：每次最多读一次底层 socket read，立即返回（不缓冲），
                # 避免 SSE 流被 read(n) 缓冲至 n 字节才返回导致卡死。
                chunk = await loop.run_in_executor(None, resp.read1, 8192)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return StreamingResponse(
        _stream(), status_code=resp.status, headers=resp_headers,
        media_type=resp_headers.get("content-type", "application/json"),
    )


@app.get("/api/health")
def api_health():
    """探活 llama-server（管理端点，非 OpenAI API；公开，无需认证）。"""
    if not runner.running:
        return JSONResponse(status_code=400, content={"error": "llama-server 未运行"})
    ok, data = api_client.health(runner.host, runner.port)
    return {"reachable": ok, "data": data}


@app.get("/api/examples")
def get_examples():
    """生成 Python/JavaScript/cURL 调用示例（指向启动器 /v1/*，含 Bearer 头）。"""
    base = _runtime_api_base()
    return api_client.gen_client_examples(base, runner.model)


# -------------------- API Key 管理 --------------------

@app.get("/api/keys")
def list_keys_route():
    """列出所有 Key（不含 hash、不含明文）。"""
    return api_keys.list_keys(api_keys_path)


@app.post("/api/keys")
def create_key_route(body: dict = Body(default={})):
    """创建 Key。body: {label, scope?, expires_at?}。成功仅此一次返回明文。"""
    label = str(body.get("label", "")).strip()
    if not label:
        return JSONResponse(status_code=400, content={"error": "缺少标签 label"})
    scope = str(body.get("scope", "admin")).strip() or "admin"
    try:
        expires_at = int(body.get("expires_at", 0) or 0)
    except (TypeError, ValueError):
        expires_at = 0
    try:
        created = api_keys.create_key(api_keys_path, label, scope, expires_at)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"key": created, "keys": api_keys.list_keys(api_keys_path)}


@app.delete("/api/keys/{kid}")
def revoke_key_route(kid: str):
    """按 id 回收 Key。"""
    ok, msg = api_keys.revoke_key(api_keys_path, kid)
    if not ok:
        return JSONResponse(status_code=404, content={"error": msg})
    return {"keys": api_keys.list_keys(api_keys_path)}


@app.post("/api/keys/toggle")
def toggle_key_route(body: dict = Body(default={})):
    """启停 Key。body: {id, enabled}。"""
    kid = str(body.get("id", "")).strip()
    enabled = bool(body.get("enabled", True))
    ok, msg = api_keys.set_enabled(api_keys_path, kid, enabled)
    if not ok:
        return JSONResponse(status_code=404, content={"error": msg})
    return {"keys": api_keys.list_keys(api_keys_path)}


@app.post("/api/keys/rename")
def rename_key_route(body: dict = Body(default={})):
    """改标签。body: {id, label}。"""
    kid = str(body.get("id", "")).strip()
    label = str(body.get("label", "")).strip()
    if not label:
        return JSONResponse(status_code=400, content={"error": "缺少标签 label"})
    ok, msg = api_keys.update_label(api_keys_path, kid, label)
    if not ok:
        return JSONResponse(status_code=404, content={"error": msg})
    return {"keys": api_keys.list_keys(api_keys_path)}


# -------------------- Prompt 模板管理 --------------------

def _load_prompts():
    """读取 prompts.json；返回 list[{name, category, content}]。"""
    data = load_json(prompts_path, None)
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    return []


def _save_prompts(prompts):
    save_json(prompts_path, prompts)


@app.get("/api/prompts")
def get_prompts():
    return _load_prompts()


@app.post("/api/prompts")
def save_prompt(body: dict = Body(default={})):
    """新增/覆盖单个 prompt 模板。body: {name, category?, content, old_name?}。
    old_name 用于重命名；不存在 old_name 时按 name 新增/覆盖。
    """
    name = str(body.get("name", "")).strip()
    content = body.get("content", "")
    if not name:
        return JSONResponse(status_code=400, content={"error": "缺少模板名 name"})
    category = str(body.get("category", "通用")).strip() or "通用"
    old_name = str(body.get("old_name", "")).strip()
    prompts = _load_prompts()
    # 找到待替换项索引
    target_idx = None
    for i, p in enumerate(prompts):
        if (old_name and p.get("name") == old_name) or (not old_name and p.get("name") == name):
            target_idx = i
            break
    entry = {"name": name, "category": category, "content": content}
    if target_idx is not None:
        prompts[target_idx] = entry
    else:
        prompts.append(entry)
    try:
        _save_prompts(prompts)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return prompts


@app.delete("/api/prompts/{name}")
def delete_prompt(name: str):
    prompts = _load_prompts()
    new_list = [p for p in prompts if p.get("name") != name]
    if len(new_list) == len(prompts):
        return JSONResponse(status_code=404, content={"error": f"模板 {name} 不存在"})
    try:
        _save_prompts(new_list)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return new_list


# -------------------- 管理员登录认证 --------------------

def _client_ip(request: Request) -> str:
    """获取客户端 IP（用于登录限流）。"""
    client = request.client
    return client[0] if client else ""


def _is_local_request(request: Request) -> bool:
    """判断请求是否来自本机。"""
    return _client_ip(request) in LOCAL_HOSTS


@app.get("/api/auth/status")
def auth_status(request: Request):
    """查询认证状态（前端首屏判断是否需登录）。公开端点，不保护。"""
    password_set = admin_auth.is_password_set(config_path)
    auth_enabled = bool(config_data.get("admin_auth_enabled", True))
    # 判断当前请求携带的 session 是否有效
    session_valid = False
    provided = _extract_bearer_token({
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in request.headers.items()]
    }) if hasattr(request, "headers") else ""
    if provided and provided.startswith(admin_auth.SESSION_PREFIX):
        ok, _ = admin_auth.verify_session(provided)
        session_valid = ok
    return {
        "password_set": password_set,
        "auth_enabled": auth_enabled,
        "session_valid": session_valid,
        "remote_access": _is_remote_listening(),
        "is_local": _is_local_request(request),
    }


@app.post("/api/auth/login")
def auth_login(request: Request, body: dict = Body(default={})):
    """登录签发 session。公开端点（登录入口），不保护，但有 IP 限流。"""
    password = str(body.get("password", "") or "")
    if not password:
        return JSONResponse(status_code=400, content={"error": "请输入密码"})
    ip = _client_ip(request)
    # 限流检查
    ok, msg = admin_auth._check_login_rate(ip)
    if not ok:
        return JSONResponse(status_code=429, content={"error": msg})
    ok, token_or_msg, expires = admin_auth.create_session(password, config_path)
    if not ok:
        admin_auth._record_login_fail(ip)
        status = 409 if "尚未初始化" in token_or_msg else 401
        return JSONResponse(status_code=status, content={"error": token_or_msg})
    admin_auth._record_login_success(ip)
    return {"session_token": token_or_msg, "expires_at": expires}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    """登出，吊销当前 session。受保护（需有效 session / admin Key / legacy token）。"""
    provided = _extract_bearer_token({
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in request.headers.items()]
    }) if hasattr(request, "headers") else ""
    if provided:
        admin_auth.revoke_session(provided)
    return {"success": True}


@app.post("/api/auth/setup")
def auth_setup(request: Request, body: dict = Body(default={})):
    """首次设置密码。仅本机可调用；仅当密码未初始化时可调用。"""
    if not _is_local_request(request):
        return JSONResponse(status_code=403,
                            content={"error": "首次设置仅允许本机访问"})
    if admin_auth.is_password_set(config_path):
        return JSONResponse(status_code=409,
                            content={"error": "密码已设置，请使用修改密码接口"})
    password = str(body.get("password", "") or "")
    confirm = str(body.get("confirm", "") or "")
    if password != confirm:
        return JSONResponse(status_code=400, content={"error": "两次输入的密码不一致"})
    ok, msg = admin_auth.set_password(config_path, password)
    if not ok:
        return JSONResponse(status_code=400, content={"error": msg})
    return {"success": True}


@app.post("/api/auth/change-password")
def auth_change_password(body: dict = Body(default={})):
    """修改密码。用旧密码自证身份（无需 session）。改密后吊销所有 session。"""
    old_password = str(body.get("old_password", "") or "")
    new_password = str(body.get("new_password", "") or "")
    if not old_password or not new_password:
        return JSONResponse(status_code=400, content={"error": "请输入旧密码与新密码"})
    ok, msg = admin_auth.change_password(config_path, old_password, new_password)
    if not ok:
        status = 409 if "尚未初始化" in msg else 401
        return JSONResponse(status_code=status, content={"error": msg})
    return {"success": True}


# -------------------- 原生文件选择器 --------------------

@app.get("/api/pick")
def pick_path(type: str = Query("dir", pattern="^(dir|file)$"),
              filter: str = Query(""),
              title: str = Query("选择路径")):
    """调起服务端原生 OS 文件/目录选择对话框，返回选中路径。

    本机使用时弹出原生桌面对话框；远程无头服务器无显示器时返回空串，
    前端应回退到手动输入。属受保护端点（本机与远程均需登录 session）。
    """
    filter_desc = ""
    if type == "file" and filter:
        # filter 简写：gguf → "GGUF 模型 (*.gguf)"
        if filter == "gguf":
            filter_desc = "GGUF 模型 (*.gguf)|*.gguf"
        elif filter == "json":
            filter_desc = "JSON 文件 (*.json)|*.json"
        else:
            filter_desc = filter
    path = filepicker.pick(kind=type, title=title, filter_desc=filter_desc)
    available = filepicker.is_available()
    return {"path": path, "available": available}


# -------------------- 未知 /api/* 兜底（保形：旧版返回 JSON 404）--------------------

@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
def api_not_found():
    return JSONResponse(status_code=404, content={"error": "路径不存在"})


# -------------------- 静态文件（最后挂载，API 路由优先匹配）--------------------
# html=True：请求 "/" 自动返回 index.html。
app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent / "static", html=True),
    name="static",
)


# -------------------- 服务器 --------------------

def run_server(host="127.0.0.1", port=8686):
    """启动 uvicorn 服务器，阻塞直到 Ctrl+C 或 should_exit。"""
    global server_instance, web_host, web_port
    web_host = host
    web_port = port
    config = uvicorn.Config(
        app, host=host, port=port,
        access_log=False,   # 关闭 access log（对旧版 log_message 静默）
        log_config=None,    # 不改默认日志格式，保留 error log
    )
    server_instance = uvicorn.Server(config)
    print(f"{APP_NAME}")
    print(f"访问地址：http://{host}:{port}")
    print(f"OpenAI 兼容 API：http://{host}:{port}/v1/*（需 sk- API Key）")
    print(f"远程访问：使用 --host 0.0.0.0")
    # 认证状态提示
    if admin_auth.is_password_set(config_path):
        print("已启用管理员登录认证：所有访问（含本机）需先登录获取 session")
    else:
        print("提示：尚未设置管理员密码，首次使用请在浏览器中设置密码后登录。")
    print("按 Ctrl+C 停止服务")
    # ⚠️ 不用 server_instance.run()：其内部 asyncio.run() 会安装自己的 SIGINT
    # 处理器（_on_sigint），Ctrl+C 时抛 KeyboardInterrupt 并取消所有 asyncio 任务，
    # 导致 lifespan / 流式响应的 CancelledError traceback 级联。
    # 改用手动事件循环 + loop.run_until_complete()：asyncio 不安装 SIGINT 处理器，
    # uvicorn 的 capture_signals() 安装 handle_exit（设置 should_exit=True），
    # 主循环检测到后走优雅关停（lifespan.shutdown + 连接清理），无 CancelledError。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server_instance.serve())
    except KeyboardInterrupt:
        # 兜底：极端情况下 SIGINT 仍可能抛 KeyboardInterrupt（如事件循环未启动时）
        pass
    finally:
        # 确保清理：通知 SSE 生成器退出 + 强制结束子进程
        _shutdown_event.set()
        if runner.running:
            runner.force_stop()
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


if __name__ == "__main__":
    init_app(str(Path(__file__).resolve().parent.parent))
    run_server(host="0.0.0.0", port=8686)
