#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server 命令构建与子进程管理。"""

import platform
import queue
import shlex
import signal
import subprocess
import threading
from pathlib import Path


def build_command(exe, model_full, profile, host=None, port=None):
    """根据参数方案构建 llama-server 命令。

    host / port 由界面单独传入，为空时省略对应 flag，
    由 llama-server 使用默认值（通常 127.0.0.1:8080）。
    """
    cmd = [exe, "-m", model_full]

    def int_or_skip(key, flag):
        """int 字段为空或 0 则省略；-1 等负值保留。"""
        sval = str(profile.get(key, "")).strip()
        if sval == "" or sval == "0":
            return
        try:
            cmd.extend([flag, str(int(sval))])
        except ValueError:
            pass

    host = str(host or "").strip()
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
    int_or_skip("gpu_layers", "-ngl")  # -1 表示全部卸载，保留
    int_or_skip("threads", "-t")

    # -fa 必须显式传值，否则会吞掉后续参数
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
    """封装 llama-server 子进程：启动、读取输出、优雅/强制停止。"""

    def __init__(self):
        self.proc = None
        self.log_queue = queue.Queue()
        self._reader_thread = None
        self._command = None

    @property
    def running(self):
        if self.proc is None:
            return False
        return self.proc.poll() is None

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    @property
    def command(self):
        return self._command

    def start(self, cmd, on_started=None, on_error=None):
        """启动子进程。成功返回 True。"""
        if self.running:
            return False
        self._command = cmd
        try:
            creationflags = 0
            if platform.system() == "Windows":
                creationflags = (subprocess.CREATE_NO_WINDOW
                                 | subprocess.CREATE_NEW_PROCESS_GROUP)
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=creationflags)
        except Exception as e:
            if on_error:
                on_error(e)
            self.proc = None
            self._command = None
            return False
        if on_started:
            on_started()
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        return True

    def _reader(self):
        """后台线程：逐行读取子进程输出放入队列，结束时记录返回码。"""
        proc = self.proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                self.log_queue.put(("out", line.rstrip("\n")))
        except Exception:
            pass
        try:
            self.log_queue.put(("rc", proc.wait()))
        except Exception:
            pass

    def stop(self):
        """优雅停止（发送终止信号）。"""
        if not self.running:
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["powershell", "-Command",
                     f"$p = Get-Process -Id {self.proc.pid} -ErrorAction SilentlyContinue;"
                     f" if ($p) {{ $p.CloseMainWindow() }}"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=3)
            else:
                self.proc.send_signal(signal.SIGTERM)
        except Exception:
            pass

    def force_stop(self):
        """强制停止子进程树并释放资源。"""
        proc = self.proc
        if proc is None:
            return
        try:
            if platform.system() == "Windows":
                # Windows 上 proc.kill() 无法终止子进程；用 taskkill 杀进程树
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=10)
            else:
                proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        self.proc = None
        self._reader_thread = None
        self._command = None

    def drain(self):
        """取出队列中所有日志事件，返回 [(kind, data), ...]。"""
        events = []
        try:
            while True:
                events.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        return events
