#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server 命令构建与子进程管理。"""

import platform
import shlex
import subprocess
import threading
import queue
from pathlib import Path


def build_command(exe, model_full, profile):
    """根据一个参数方案构建 llama-server 命令。"""
    cmd = [exe, "-m", model_full]

    def int_or_skip(key, flag):
        sval = str(profile.get(key, "")).strip()
        if sval == "" or sval == "0":
            return
        try:
            cmd.extend([flag, str(int(sval))])
        except ValueError:
            pass

    host = str(profile.get("host", "")).strip()
    port = str(profile.get("port", "")).strip()
    if host:
        cmd.extend(["--host", host])
    if port:
        try:
            cmd.extend(["--port", str(int(port))])
        except ValueError:
            pass

    int_or_skip("context_size", "-c")
    int_or_skip("parallel", "-np")
    int_or_skip("batch_size", "-b")
    int_or_skip("ubatch_size", "-ub")
    int_or_skip("gpu_layers", "-ngl")
    int_or_skip("threads", "-t")

    if profile.get("flash_attn"):
        cmd.extend(["-fa", "on"])
    if profile.get("cont_batching"):
        cmd.append("--cont-batching")
    if profile.get("mlock"):
        cmd.append("--mlock")
    if profile.get("no_mmap"):
        cmd.append("--no-mmap")
    if profile.get("embedding"):
        cmd.append("--embedding")
    if profile.get("reranking"):
        cmd.append("--reranking")
    if profile.get("jinja"):
        cmd.append("--jinja")
    if profile.get("verbose"):
        cmd.append("-v")

    pooling = str(profile.get("pooling", "")).strip()
    if pooling:
        cmd.extend(["--pooling", pooling])
    chat_template = str(profile.get("chat_template", "")).strip()
    if chat_template:
        cmd.extend(["--chat-template", chat_template])

    draft = str(profile.get("draft_model", "")).strip()
    if draft:
        cmd.extend(["-md", draft if Path(draft).is_absolute()
                    else str(Path(model_full).parent / draft)])

    grammar = str(profile.get("grammar_file", "")).strip()
    if grammar:
        cmd.extend(["--grammar-file", grammar])

    extra = str(profile.get("extra_args", "")).strip()
    if extra:
        cmd.extend(shlex.split(extra, posix=(platform.system() != "Windows")))
    return cmd


def quote_arg(arg):
    """为日志展示对含空格的参数加引号。"""
    return '"' + arg.replace('"', '\\"') + '"' if (" " in arg or "\t" in arg) else arg


class ServerRunner:
    """封装 llama-server 子进程：启动、读取输出、强制停止。"""

    def __init__(self):
        self.proc = None
        self.log_queue = queue.Queue()
        self._reader_thread = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd, on_started=None, on_error=None):
        """启动子进程。成功返回 True。"""
        if self.running:
            return False
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=creationflags)
        except Exception as e:
            if on_error:
                on_error(e)
            self.proc = None
            return False
        if on_started:
            on_started()
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        return True

    def _reader(self):
        proc = self.proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                self.log_queue.put(("out", line.rstrip("\n")))
        except Exception as e:
            self.log_queue.put(("err", str(e)))
        self.log_queue.put(("rc", proc.wait()))

    def force_stop(self):
        proc = self.proc
        if proc is None:
            return
        try:
            proc.kill()  # 强制终止
        except Exception:
            pass

    def drain(self):
        """取出队列中所有日志事件，返回 [(kind, data), ...]。"""
        events = []
        try:
            while True:
                events.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        return events
