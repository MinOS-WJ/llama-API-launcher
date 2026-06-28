#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径与可执行文件检测工具。"""

import platform
from pathlib import Path

SERVER_BIN_BASENAME = "llama-server"
MODEL_EXTS = (".gguf",)


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
