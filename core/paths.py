#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径检测、可执行文件查找、模型枚举与服务端目录浏览。"""

import os
import platform
import socket
import string
from pathlib import Path

SERVER_BIN_BASENAME = "llama-server"
MODEL_EXTS = (".gguf",)
# 目录浏览时显示的文件扩展名与前缀（其余文件隐藏，减少条目数）
BROWSE_FILE_EXTS = (".gguf", ".json")
BROWSE_FILE_PREFIXES = ("llama-server",)


def server_executable_candidates(llamacpp_dir):
    """在 llama.cpp 目录及其常见子目录中查找 llama-server 可执行文件。"""
    if not llamacpp_dir:
        return []
    base = Path(llamacpp_dir)
    names = ([SERVER_BIN_BASENAME + ".exe"]
             if platform.system() == "Windows" else [SERVER_BIN_BASENAME])
    found = [str(base / n) for n in names if (base / n).exists()]
    if not found:
        for sub in ("build/bin", "build", "bin", "release", "Release"):
            d = base / sub
            if d.is_dir():
                found = [str(d / n) for n in names if (d / n).exists()]
                if found:
                    break
    return found


def detect_llamacpp(directory):
    """识别目录是否为有效的 llama.cpp 目录（依据能否找到 llama-server）。
    返回 (status, message, exe_path)：status 仅 'ok' / 'bad'。
    """
    if not directory or not directory.strip():
        return ("bad", "✗ 未设置", "")
    base = Path(directory)
    if not base.is_dir():
        return ("bad", "✗ 目录不存在", "")
    cands = server_executable_candidates(directory)
    if cands:
        return ("ok", "✓ 已识别", cands[0])
    return ("bad", "✗ 未找到 llama-server", "")


def list_model_files(model_dir):
    """列出模型目录下的 .gguf 文件（含一级子目录）。"""
    if not model_dir or not Path(model_dir).is_dir():
        return []
    result = []
    try:
        for entry in sorted(Path(model_dir).iterdir(), key=lambda p: p.name.lower()):
            if entry.is_file() and entry.suffix.lower() in MODEL_EXTS:
                result.append(entry.name)
        for sub in sorted(Path(model_dir).iterdir(), key=lambda p: p.name.lower()):
            if sub.is_dir():
                for entry in sorted(sub.iterdir(), key=lambda p: p.name.lower()):
                    if entry.is_file() and entry.suffix.lower() in MODEL_EXTS:
                        result.append(f"{sub.name}/{entry.name}")
    except OSError:
        pass
    return result


# -------------------- 目录浏览 --------------------

def list_roots():
    """返回可浏览的根路径列表。
    Windows：枚举存在的盘符；Unix：返回 ["/"]。
    """
    if platform.system() == "Windows":
        return [f"{d}:\\" for d in string.ascii_uppercase
                if os.path.exists(f"{d}:\\")]
    return ["/"]


def _is_browsable_file(name):
    """判断文件是否在目录浏览器中显示（模型/配置/可执行文件）。"""
    lower = name.lower()
    if lower.endswith(BROWSE_FILE_EXTS):
        return True
    return any(lower.startswith(p) for p in BROWSE_FILE_PREFIXES)


def browse_directory(path):
    """浏览服务端目录，返回 {path, parent, entries, error}。
    path 为空时返回根列表；entries 中目录在前、文件在后，按名排序。
    永不抛异常，错误写入 error 字段。
    """
    if not path:
        roots = list_roots()
        entries = [{"name": r, "type": "dir", "size": 0} for r in roots]
        return {"path": "", "parent": "", "entries": entries, "error": ""}

    base = Path(path)
    try:
        resolved = base.resolve()
    except OSError:
        resolved = base

    if not resolved.is_dir():
        return {"path": path, "parent": "", "entries": [],
                "error": "不是有效目录"}

    # 计算 parent；根目录时 parent 为空
    parent = str(resolved.parent)
    if parent == str(resolved):
        parent = ""

    dirs, files = [], []
    error = ""
    try:
        for entry in resolved.iterdir():
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.is_file() and _is_browsable_file(entry.name):
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    files.append((entry.name, size))
            except OSError:
                # 单条目无权限跳过，不阻断整体
                continue
    except OSError as e:
        error = f"权限不足：{e}"

    dirs.sort(key=str.lower)
    files.sort(key=lambda x: x[0].lower())

    entries = [{"name": n, "type": "dir", "size": 0} for n in dirs]
    entries.extend({"name": n, "type": "file", "size": s} for n, s in files)

    return {"path": str(resolved), "parent": parent,
            "entries": entries, "error": error}


# -------------------- 启动前健康检查辅助 --------------------

def check_port_bindable(host, port):
    """检测端口是否可绑定。返回 (ok, error_message)。

    与 main.check_port 不同，此函数允许 host 为空（用 127.0.0.1 探测），
    专供健康检查使用，不阻断主流程。
    """
    h = (host or "127.0.0.1").strip() or "127.0.0.1"
    try:
        p = int(port)
    except (TypeError, ValueError):
        return (False, f"端口非整数：{port!r}")
    if not (1 <= p <= 65535):
        return (False, f"端口超出范围（1-65535）：{p}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((h, p))
            return (True, None)
        except OSError as e:
            return (False, str(e))


def resolve_model_path(model, model_dir):
    """将相对模型名解析为绝对路径。空返回空串。"""
    if not model:
        return ""
    if Path(model).is_absolute():
        return model
    return str(Path(model_dir) / model) if model_dir else model


def model_exists(model_full):
    """检查模型文件是否存在且后缀为 .gguf。返回 (exists, is_gguf)。"""
    if not model_full:
        return (False, False)
    p = Path(model_full)
    if not p.is_file():
        return (False, False)
    return (True, p.suffix.lower() in MODEL_EXTS)

