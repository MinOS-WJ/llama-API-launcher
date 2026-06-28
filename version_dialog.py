#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— 版本管理对话框（tk）。"""

import os
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import version_manager as vm
from core.paths import detect_llamacpp


class VersionManagerDialog:
    """从 GitHub 下载官方预编译包并解压替换以更新 llama.cpp。"""

    def __init__(self, parent, llamacpp_dir_getter, on_updated=None):
        self.parent = parent
        self.llamacpp_dir_getter = llamacpp_dir_getter
        self.on_updated = on_updated
        self.releases = []
        self._current_assets = []
        self._busy = False

        self.top = tk.Toplevel(parent)
        self.top.title("llama.cpp 版本管理")
        self.top.geometry("720x540")
        self.top.minsize(620, 460)
        self.top.transient(parent)
        self.top.grab_set()
        self._build_ui()
        self._refresh_current()

    # ---------- UI ----------
    def _build_ui(self):
        top = self.top

        bar = ttk.Frame(top, padding=8)
        bar.pack(fill="x")
        self.current_var = tk.StringVar(value="当前：未识别")
        ttk.Label(bar, textvariable=self.current_var,
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(bar, text="刷新当前", command=self._refresh_current).pack(side="left", padx=(8, 0))
        ttk.Label(bar, text="平台：").pack(side="right")
        self.os_filter_var = tk.StringVar(value=vm.current_os_filter_label())
        os_combo = ttk.Combobox(bar, textvariable=self.os_filter_var, state="readonly",
                                values=["全部", "Windows", "Linux", "macOS", "其他"],
                                width=10)
        os_combo.pack(side="right", padx=(0, 8))
        os_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_assets())
        self.check_btn = ttk.Button(bar, text="检查更新（GitHub）", command=self._check_updates)
        self.check_btn.pack(side="right", padx=(0, 8))

        body = ttk.Frame(top, padding=(8, 0))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ttk.Label(left, text="发布版本").pack(anchor="w")
        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True)
        self.rel_list = tk.Listbox(lf, exportselection=False)
        rsb = ttk.Scrollbar(lf, orient="vertical", command=self.rel_list.yview)
        self.rel_list.config(yscrollcommand=rsb.set)
        self.rel_list.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        self.rel_list.bind("<<ListboxSelect>>", lambda e: self._refresh_assets())

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="资产包（assets）").pack(anchor="w")
        rf = ttk.Frame(right)
        rf.pack(fill="both", expand=True)
        self.asset_list = tk.Listbox(rf, exportselection=False)
        asb = ttk.Scrollbar(rf, orient="vertical", command=self.asset_list.yview)
        self.asset_list.config(yscrollcommand=asb.set)
        self.asset_list.pack(side="left", fill="both", expand=True)
        asb.pack(side="right", fill="y")

        foot = ttk.Frame(top, padding=8)
        foot.pack(fill="x")
        self.install_btn = ttk.Button(foot, text="下载并更新", state="disabled", command=self._install)
        self.install_btn.pack(side="left")
        self.progress = ttk.Progressbar(foot, mode="determinate", length=300)
        self.progress.pack(side="left", padx=8, fill="x", expand=True)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(foot, textvariable=self.status_var).pack(side="left")

    # ---------- 行为 ----------
    def _refresh_current(self):
        d = self.llamacpp_dir_getter()
        status, msg, _ = detect_llamacpp(d)
        self.current_var.set(f"当前目录：{d or '（未设置）'}  —  {msg}")

    def _check_updates(self):
        if self._busy:
            return
        self._busy = True
        self.check_btn.config(state="disabled")
        self.install_btn.config(state="disabled")
        self.status_var.set("正在从 GitHub 获取发布列表…")
        self.progress.config(mode="indeterminate")
        self.progress.start(12)

        def work():
            try:
                rels = vm.fetch_releases()
                self.top.after(0, lambda: self._on_releases(rels, None))
            except Exception as e:
                self.top.after(0, lambda: self._on_releases(None, e))

        threading.Thread(target=work, daemon=True).start()

    def _on_releases(self, rels, err):
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.check_btn.config(state="normal")
        self._busy = False
        if err:
            self.status_var.set(f"获取失败：{err}")
            messagebox.showerror("获取失败", str(err), parent=self.top)
            return
        self.releases = rels or []
        self.rel_list.delete(0, "end")
        for r in self.releases:
            self.rel_list.insert("end", f"{r['tag']}  ({r['published']})")
        if self.releases:
            self.rel_list.selection_set(0)
            self._refresh_assets()
            self.status_var.set(f"共 {len(self.releases)} 个版本")
        else:
            self.status_var.set("未获取到版本")

    def _refresh_assets(self):
        sel = self.rel_list.curselection()
        self.asset_list.delete(0, "end")
        self._current_assets = []
        if not sel:
            self.install_btn.config(state="disabled")
            return
        r = self.releases[sel[0]]
        group_label = self.os_filter_var.get()
        group_map = {"Windows": "windows", "Linux": "linux",
                     "macOS": "macos", "其他": "others"}
        target_group = group_map.get(group_label, "")  # "全部" → ""
        for a in r["assets"]:
            info = a["info"]
            if target_group and vm.asset_os_group(info) != target_group:
                continue
            label = f"{a['name']}  [{vm.variant_label(info)}]  {a['size'] // 1024}KB"
            self.asset_list.insert("end", label)
            self._current_assets.append(a)
        if self._current_assets:
            self.asset_list.selection_set(0)
            self.install_btn.config(state="normal")
        else:
            self.install_btn.config(state="disabled")
            if target_group:
                self.status_var.set(f"{group_label} 平台无匹配资产，可切换为“全部”")

    def _install(self):
        if self._busy:
            return
        asel = self.asset_list.curselection()
        if not asel:
            return
        asset = self._current_assets[asel[0]]
        llamacpp_dir = self.llamacpp_dir_getter()
        if not llamacpp_dir:
            messagebox.showerror("错误", "未设置 llama.cpp 目录", parent=self.top)
            return
        status, _, _ = detect_llamacpp(llamacpp_dir)
        if status == "bad":
            if not messagebox.askyesno(
                    "目录未识别",
                    f"目标目录未被识别为有效 llama.cpp 目录：\n{llamacpp_dir}\n\n仍要在此解压更新吗？",
                    parent=self.top):
                return

        if self.runner_running():
            if not messagebox.askyesno(
                    "服务运行中",
                    "llama-server 正在运行，更新前建议先停止。是否继续？",
                    parent=self.top):
                return

        self._busy = True
        self.install_btn.config(state="disabled")
        self.check_btn.config(state="disabled")
        self.progress.config(mode="determinate", maximum=100, value=0)
        self.status_var.set(f"正在下载 {asset['name']}…")

        def work():
            tmp_path = None
            try:
                suffix = ".tar.gz" if asset["name"].lower().endswith((".tar.gz", ".tgz")) else ".zip"
                fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                os.close(fd)

                def dl_cb(done, total):
                    if total:
                        pct = done * 100.0 / total
                        self.top.after(0, lambda p=pct, d=done, t=total:
                                       self._set_progress(p, f"下载 {d // 1024}/{t // 1024} KB"))

                vm.download_file(asset["url"], tmp_path, progress_cb=dl_cb)
                self.top.after(0, lambda: self.status_var.set("正在解压并替换文件…"))

                def inst_cb(phase, cur, total):
                    if total:
                        pct = cur * 100.0 / total
                        msg = f"{phase}: {cur}/{total}"
                    else:
                        pct = 100  # extract/backup 阶段保持满进度
                        msg = phase
                    self.top.after(0, lambda p=pct, m=msg: self._set_progress(p, m))

                ok, msg = vm.install_asset(tmp_path, llamacpp_dir, progress_cb=inst_cb)
                self.top.after(0, lambda: self._on_installed(ok, msg))
            except Exception as e:
                self.top.after(0, lambda: self._on_installed(False, str(e)))
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        threading.Thread(target=work, daemon=True).start()

    def runner_running(self):
        """由主窗口注入；默认返回 False。"""
        getter = getattr(self, "_runner_running_getter", None)
        return bool(getter and getter())

    def set_runner_running_getter(self, getter):
        self._runner_running_getter = getter

    def _set_progress(self, pct, status):
        self.progress.config(value=max(0, min(100, pct)))
        if status:
            self.status_var.set(status)

    def _on_installed(self, ok, msg):
        self._busy = False
        self.check_btn.config(state="normal")
        self.progress.config(value=100 if ok else 0)
        self.status_var.set(msg)
        if ok:
            messagebox.showinfo("完成", msg, parent=self.top)
            self._refresh_current()
            if self.on_updated:
                self.on_updated()
        else:
            messagebox.showerror("更新失败", msg, parent=self.top)
