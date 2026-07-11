#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原生 OS 文件/目录选择对话框。

通过 ``subprocess`` 调起各平台的原生选择器，零第三方依赖：
- Windows：PowerShell + System.Windows.Forms（FolderBrowserDialog / OpenFileDialog）
- Linux：zenity（GNOME）或 kdialog（KDE），按可用性自动选择
- macOS：osascript（AppleScript choose file / choose folder）

返回选中的绝对路径字符串；用户取消或环境无图形界面时返回空串。

注意：对话框在**服务端**弹出（路径是服务端文件系统路径）。本机使用时即用户
所在桌面；远程无头服务器无显示器时无法弹出，调用方应回退到手动输入。
"""

import shutil
import subprocess
import sys


def _run(cmd, timeout=180):
    """执行命令，返回 stdout 去首尾空白。失败/超时返回空串。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


# -------------------- Windows --------------------

def _pick_windows(kind, title, filter_desc):
    """Windows 用 PowerShell 调起 System.Windows.Forms 对话框。"""
    if kind == "dir":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = '%s'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
            % title.replace("'", "''")
        )
    else:
        # 文件选择：filter_desc 形如 "GGUF 模型 (*.gguf)|*.gguf|所有文件 (*.*)|*.*"
        filt = filter_desc or "所有文件 (*.*)|*.*"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Title = '%s'; "
            "$d.Filter = '%s'; "
            "if ($d.ShowDialog() -eq 'OK') { $d.FileName }"
            % (title.replace("'", "''"), filt.replace("'", "''"))
        )
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])


# -------------------- Linux --------------------

def _pick_linux(kind, title, filter_desc):
    """Linux 优先 zenity，其次 kdialog。"""
    zenity = shutil.which("zenity")
    kdialog = shutil.which("kdialog")
    if zenity:
        if kind == "dir":
            return _run([zenity, "--file-selection", "--directory",
                         "--title", title])
        # 文件：filter_desc 用 zenity --file-filter 语法
        # 简化：若 filter_desc 含 *.gguf 则加 --file-filter
        cmd = [zenity, "--file-selection", "--title", title]
        if filter_desc and "*.gguf" in filter_desc:
            cmd.append("--file-filter=GGUF 模型 *.gguf")
        return _run(cmd)
    if kdialog:
        if kind == "dir":
            return _run([kdialog, "--getexistingdirectory", "/", "--title", title])
        return _run([kdialog, "--getopenfilename", "--title", title])
    return ""


# -------------------- macOS --------------------

def _pick_macos(kind, title, filter_desc):
    """macOS 用 osascript 调起原生选择器。"""
    title_esc = title.replace('"', '\\"')
    if kind == "dir":
        script = (
            f'set chosen to (choose folder with prompt "{title_esc}") as alias\n'
            f'return POSIX path of chosen'
        )
    else:
        # 文件类型过滤：filter_desc 含 gguf 时限定类型
        if filter_desc and "gguf" in filter_desc.lower():
            script = (
                f'set chosen to (choose file with prompt "{title_esc}" '
                f'of type {{"gguf"}} without invisibles)\n'
                f'return POSIX path of chosen'
            )
        else:
            script = (
                f'set chosen to (choose file with prompt "{title_esc}" '
                f'without invisibles)\n'
                f'return POSIX path of chosen'
            )
    return _run(["osascript", "-e", script])


# -------------------- 统一入口 --------------------

def pick(kind="dir", title="选择路径", filter_desc=""):
    """调起原生选择器，返回选中路径（用户取消/不可用返回空串）。

    :param kind: ``"dir"`` 选目录，``"file"`` 选文件
    :param title: 对话框标题
    :param filter_desc: 文件过滤器描述。目录选择忽略；
                        文件选择时形如 ``"GGUF 模型 (*.gguf)"``，Windows 用 ``|`` 分隔多组。
    :return: 绝对路径字符串或空串
    """
    if kind not in ("dir", "file"):
        kind = "dir"
    if not title:
        title = "选择路径" if kind == "dir" else "选择文件"
    if sys.platform == "win32":
        return _pick_windows(kind, title, filter_desc)
    if sys.platform == "darwin":
        return _pick_macos(kind, title, filter_desc)
    return _pick_linux(kind, title, filter_desc)


def is_available():
    """当前平台是否可能可弹出原生对话框（粗略判断，不保证有显示器）。"""
    if sys.platform == "win32":
        return shutil.which("powershell") is not None
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    return shutil.which("zenity") is not None or shutil.which("kdialog") is not None
