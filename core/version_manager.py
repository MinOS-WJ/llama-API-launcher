#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp 版本管理：从 GitHub 下载官方预编译压缩包并解压替换以更新。

压缩包命名示例：
  llama-b9828-bin-win-cpu-x64.zip      -> build=b9828, os=win,    arch=x64,  variant=cpu
  llama-b9828-bin-ubuntu-x64.tar.gz    -> build=b9828, os=ubuntu, arch=x64,  variant=default
  llama-b9828-bin-macos-arm64.tar.gz   -> build=b9828, os=macos,  arch=arm64, variant=default
支持 .zip 与 .tar.gz / .tgz 两种压缩包格式。
"""

import json
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"


# -------------------- 平台识别 --------------------
def current_arch_token():
    m = platform.machine().lower()
    if m in ("amd64", "x86_64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def current_os_token():
    s = platform.system()
    if s == "Windows":
        return "win"
    if s == "Linux":
        return "ubuntu"  # 官方 Linux 资产以 ubuntu 命名
    if s == "Darwin":
        return "macos"
    return s.lower()


# -------------------- 资产名解析 --------------------
# 已知 OS 段到分组标签的映射（用于按 OS 筛选）
KNOWN_OS_GROUPS = {
    "win": "windows",
    "ubuntu": "linux",
    "linux": "linux",
    "macos": "macos",
}


def parse_asset_name(filename):
    """解析 llama-b9828-bin-win-cpu-x64.zip / llama-b9828-bin-ubuntu-x64.tar.gz 这类文件名。
    支持 .zip 与 .tar.gz 两种压缩包格式。
    返回 dict(build, os, arch, variant, filename, ext) 或 None。
    首段总视为 os 字段（未知 OS 也保留，便于"其他"分组与跨平台安装）。
    """
    # 识别扩展名
    if filename.endswith(".tar.gz"):
        stem = filename[:-7]
        ext = ".tar.gz"
    elif filename.endswith(".tgz"):
        stem = filename[:-4]
        ext = ".tar.gz"
    elif filename.endswith(".zip"):
        stem = filename[:-4]
        ext = ".zip"
    else:
        return None
    # 必须以 llama-b 开头且含 -bin-（排除 xcframework / ui / source code / cudart 等）
    if not (stem.startswith("llama-b") and "-bin-" in stem):
        return None
    parts = stem.split("-")
    if len(parts) < 4:
        return None
    build = parts[1]                       # b9828
    rest = parts[3:]                       # ['win','cpu','x64'] / ['ubuntu','x64'] / ['macos','arm64']
    info = {"build": build, "filename": filename, "ext": ext,
            "os": "", "arch": "", "variant": ""}
    # OS 段总在开头（已知或未知都识别，便于按 OS 分组）
    if rest:
        info["os"] = rest[0]
        rest = rest[1:]
    # arch 段总在末尾（含 s390x 等非主流架构）
    if rest and rest[-1] in ("x64", "arm64", "x86", "s390x", "ppc64le"):
        info["arch"] = rest[-1]
        rest = rest[:-1]
    info["variant"] = "-".join(rest) if rest else "default"
    return info


def asset_matches_platform(info):
    """判断资产是否匹配当前操作系统与架构。"""
    if not info:
        return False
    if info["os"] and info["os"] != current_os_token():
        return False
    if info["arch"] and info["arch"] != current_arch_token():
        return False
    return True


def asset_os_group(info):
    """将资产归类到 OS 分组：windows / linux / macos / others。
    info 为空或无 os 字段时返回空字符串。
    """
    if not info:
        return ""
    os_token = info.get("os", "")
    if not os_token:
        return ""
    return KNOWN_OS_GROUPS.get(os_token, "others")


def current_os_filter_label():
    """返回当前平台对应的筛选下拉选项标签（Windows / Linux / macOS / 全部）。"""
    token = current_os_token()
    mapping = {"win": "Windows", "ubuntu": "Linux", "macos": "macOS"}
    return mapping.get(token, "全部")


def variant_label(info):
    v = info.get("variant", "") if info else ""
    return v if v else "default"


# -------------------- GitHub 发布列表 --------------------
def fetch_releases(per_page=20, timeout=30):
    """从 GitHub API 获取发布列表。
    返回 list[dict]：{tag, name, published, assets:[{name,url,size,info}]}
    """
    url = f"{GITHUB_API}?per_page={per_page}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "llama-launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    releases = []
    for rel in data:
        assets = []
        for a in rel.get("assets", []):
            info = parse_asset_name(a.get("name", ""))
            if not info:
                continue
            assets.append({
                "name": a.get("name", ""),
                "url": a.get("browser_download_url", ""),
                "size": int(a.get("size", 0)),
                "info": info,
            })
        if not assets:
            continue
        releases.append({
            "tag": rel.get("tag_name", ""),
            "name": rel.get("name", ""),
            "published": (rel.get("published_at", "") or "")[:10],
            "assets": assets,
        })
    return releases


# -------------------- 下载与安装 --------------------
def download_file(url, dest, progress_cb=None, timeout=60):
    """下载文件到 dest；progress_cb(done_bytes, total_bytes) 可选。"""
    req = urllib.request.Request(url, headers={"User-Agent": "llama-launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length", "0") or 0)
        done = 0
        chunk = 64 * 1024
        with open(dest, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress_cb:
                    progress_cb(done, total)
    if progress_cb:
        progress_cb(done, total or done)


def install_asset(zip_path, target_dir, progress_cb=None):
    """解压压缩包（.zip 或 .tar.gz）并以替换文件方式更新 target_dir。
    progress_cb(phase, cur, total)：phase ∈ {'extract','backup','copy'}。
    Linux/macOS 下会为 llama-server 等可执行文件补 +x 权限。
    返回 (ok, message)。
    """
    target = Path(target_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return (False, f"无法创建目标目录：{e}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            if progress_cb:
                progress_cb("extract", 0, 0)
            lower = str(zip_path).lower()
            if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
                with tarfile.open(zip_path, "r:gz") as tf:
                    tf.extractall(tmp)
            else:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmp)
            tmp_path = Path(tmp)

            # 若压缩包内仅一个顶层目录，则进入该目录
            top = [p for p in tmp_path.iterdir() if not p.name.startswith(".")]
            src = top[0] if len(top) == 1 and top[0].is_dir() else tmp_path

            # 备份当前二进制（llama-server* 与动态库）到 _backup
            if progress_cb:
                progress_cb("backup", 0, 0)
            backup_dir = target / "_backup"
            try:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                backup_dir.mkdir(exist_ok=True)
                for p in target.iterdir():
                    name = p.name
                    if name == "_backup":
                        continue
                    if p.is_file() and (name.lower().startswith("llama")
                                        or p.suffix.lower() in (".dll", ".so", ".dylib", ".exe")):
                        shutil.copy2(p, backup_dir / name)
            except Exception:
                pass  # 备份失败不阻断更新

            # 复制新文件（覆盖）
            all_files = [p for p in src.rglob("*") if p.is_file()]
            total = len(all_files)
            for i, p in enumerate(all_files, 1):
                rel = p.relative_to(src)
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                # Linux/macOS：为 llama-server 等可执行文件补 +x 权限
                if platform.system() != "Windows":
                    name = dst.name.lower()
                    if name.startswith("llama") and dst.suffix == "":
                        try:
                            mode = dst.stat().st_mode
                            dst.chmod(mode | 0o755)
                        except OSError:
                            pass
                if progress_cb and (i % 5 == 0 or i == total):
                    progress_cb("copy", i, total)
        return (True, "更新完成")
    except Exception as e:
        return (False, f"解压/替换失败：{e}")
