#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— Web 后端（FastAPI 实现）。

关键设计：
- FastAPI + uvicorn 取代标准库 ThreadingHTTPServer。
- 阻塞型端点（网络/磁盘/子进程/同步 sleep）声明为普通 ``def``，FastAPI 自动
  丢进 anyio 线程池执行，不阻塞事件循环；SSE 长连接是唯一的 ``async def``。
- ``core/`` 模块保持纯同步、零 web 依赖，路由直接调用其函数。
- 错误响应统一用 ``JSONResponse(content={"error": msg})`` 以匹配前端契约
  （FastAPI 默认的 ``{"detail": ...}`` 会被前端忽略）。
"""

import asyncio
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

from core.paths import (browse_directory, detect_llamacpp,
                        list_model_files, server_executable_candidates)
from core.profiles import ProfileManager, list_config_files, load_json, save_json
from core.launcher import ServerRunner, build_command, quote_arg
from core import version_manager as vm

APP_NAME = "llama.cpp API 启动器"
CONFIG_FILENAME = "llama_launcher_config.json"
DEFAULT_PROFILES_REL = "configs/default.json"

# -------------------- 全局状态 --------------------
# 与旧版命名一一对应，便于对照；init_app 在 main.py 启动时初始化。
base_dir = ""
config_path = ""
config_data = {}
pm = None                 # ProfileManager 实例
runner = ServerRunner()   # llama-server 子进程封装
version_updates = {}      # {update_id: {status, progress, message, success}}
server_instance = None    # uvicorn.Server 实例（用于编程式关停）
_shutdown_event = threading.Event()  # 通知 SSE 线程退出


# -------------------- 生命周期 --------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：Ctrl+C 与 /api/shutdown 共用同一条关停清理路径。"""
    yield
    # 关停清理：先通知 SSE 生成器退出，再强制结束子进程
    _shutdown_event.set()
    if runner.running:
        print("正在停止 llama-server...")
        runner.force_stop()


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)

# 与旧版一致：响应带 Access-Control-Allow-Origin: *
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def init_app(base):
    """初始化全局状态：加载配置、方案管理器、运行器。"""
    global base_dir, config_path, config_data, pm, runner, version_updates
    base_dir = base
    config_path = str(Path(base) / CONFIG_FILENAME)
    config_data = load_json(config_path, {
        "llamacpp_dir": "", "model_dir": "", "profiles_path": "",
        "last_model": "", "current_profile": "",
        "host": "127.0.0.1", "port": 8080,
    })
    # 默认指向 configs/default.json（消除旧版 user.json 不一致）
    profiles_path = (config_data.get("profiles_path")
                     or str(Path(base) / DEFAULT_PROFILES_REL))
    pm = ProfileManager(profiles_path)
    config_data["profiles_path"] = profiles_path  # 回写保证一致
    runner = ServerRunner()
    version_updates = {}
    _shutdown_event.clear()


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
    }


@app.post("/api/config")
def set_config(body: dict = Body(default={})):
    for key in ("llamacpp_dir", "model_dir", "host", "port"):
        if key in body:
            config_data[key] = body[key]

    detect_status, detect_msg, _ = detect_llamacpp(config_data.get("llamacpp_dir", ""))
    success, err = save_config()
    if not success:
        return JSONResponse(status_code=500, content={"error": err})
    return {
        "detect_status": detect_status,
        "detect_msg": detect_msg,
        "models": list_model_files(config_data.get("model_dir", "")),
    }


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


# -------------------- 目录浏览 --------------------

@app.get("/api/browse")
def browse(path: str = Query("")):
    result = browse_directory(path)
    return JSONResponse(content=result,
                        status_code=200 if not result.get("error") else 400)


# -------------------- 服务控制 --------------------

@app.get("/api/status")
def get_status():
    return {
        "running": runner.running,
        "pid": runner.pid,
        "command": runner.command,
    }


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
    return {"command": cmd_str}


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
def get_releases(os: str = Query("")):
    try:
        releases = vm.fetch_releases()
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
        if "403" in msg or "rate limit" in msg.lower():
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


@app.get("/api/platform")
def get_platform():
    return {
        "os": vm.current_os_filter_label(),
        "os_token": vm.current_os_token(),
        "arch": vm.current_arch_token(),
    }


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
    global server_instance
    config = uvicorn.Config(
        app, host=host, port=port,
        access_log=False,   # 关闭 access log（对旧版 log_message 静默）
        log_config=None,    # 不改默认日志格式，保留 error log
    )
    server_instance = uvicorn.Server(config)
    print(f"{APP_NAME}")
    print(f"访问地址：http://{host}:{port}")
    print(f"远程访问：使用 --host 0.0.0.0")
    print("按 Ctrl+C 停止服务")
    # server_instance.run() 内部 asyncio.run(self.serve())，
    # Ctrl+C 走 lifespan shutdown 清理（_shutdown_event + runner.force_stop）；
    # Windows 下 SIGINT 会同步抛 KeyboardInterrupt 中断 asyncio.run()，
    # 可能跳过 lifespan，此处兜底吞掉并再做一次清理，避免打印 traceback。
    try:
        server_instance.run()
    except KeyboardInterrupt:
        _shutdown_event.set()
        if runner.running:
            runner.force_stop()


if __name__ == "__main__":
    init_app(str(Path(__file__).resolve().parent.parent))
    run_server(host="0.0.0.0", port=8686)
