#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— 主图形界面。

职责：
  - 设置 llama.cpp 目录、模型目录、参数方案文件
  - 选择模型与参数方案
  - 启动 / 强制停止 llama-server
  - 查看日志
  - 版本管理（更新 llama.cpp）
"""

import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from core.paths import detect_llamacpp, list_model_files, server_executable_candidates
from core.profiles import ProfileManager, load_json, list_config_files
from core.launcher import build_command, quote_arg, ServerRunner
from version_dialog import VersionManagerDialog

APP_NAME = "llama.cpp API 启动器"
PROFILES_FILENAME = "user.json"   # 用户自定义方案集（若存在则作为默认）
CONFIG_FILENAME = "llama_launcher_config.json"


class LlamaLauncherApp:
    def __init__(self, root):
        self.root = root
        self.config_path = str(Path(__file__).resolve().parent / CONFIG_FILENAME)
        self.config = load_json(self.config_path, {
            "llamacpp_dir": "", "model_dir": "", "profiles_path": "",
            "last_model": "", "current_profile": "",
        })

        self.profiles_path = (self.config.get("profiles_path")
                              or self._default_profiles_path())
        self.pm = ProfileManager(self.profiles_path)

        self.runner = ServerRunner()
        self.version_dialog = None

        self._build_ui()
        self._update_detect()
        self._refresh_model_list()
        self._refresh_profiles()
        self._poll_log()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 配置 ----------
    def _default_profiles_path(self):
        app_dir = Path(__file__).resolve().parent / PROFILES_FILENAME
        return str(app_dir) if app_dir.exists() else str(
            Path(self.config_path).parent / PROFILES_FILENAME)

    def _save_config(self):
        try:
            p = Path(self.config_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.config["profiles_path"] = self.profiles_path
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("保存配置失败", str(e))

    def _load_window_icon(self):
        """从 assets/icon.ico 加载窗口图标；找不到则保持默认。"""
        ico = Path(__file__).resolve().parent / "assets" / "icon.ico"
        if not ico.exists():
            return
        try:
            self.root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass

    # ---------- UI ----------
    def _build_ui(self):
        self.root.title(APP_NAME)
        self.root.geometry("660x480")
        self.root.minsize(560, 380)
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
        self._load_window_icon()

        main = ttk.Frame(self.root, padding=6)
        main.pack(fill="both", expand=True)

        # 路径设置
        pf = ttk.LabelFrame(main, text="路径", padding=6)
        pf.pack(fill="x", pady=(0, 4))

        ttk.Label(pf, text="llama.cpp：").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.llamacpp_dir_var = tk.StringVar(value=self.config.get("llamacpp_dir", ""))
        ttk.Entry(pf, textvariable=self.llamacpp_dir_var).grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(pf, text="浏览…", command=self._pick_llamacpp_dir).grid(row=0, column=2)
        self.detect_var = tk.StringVar(value="")
        self.detect_label = ttk.Label(pf, textvariable=self.detect_var, font=("Segoe UI", 9, "bold"))
        self.detect_label.grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Button(pf, text="版本管理", command=self._open_version_manager).grid(row=0, column=4, padx=(6, 0))

        ttk.Label(pf, text="模型目录：").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(4, 0))
        self.model_dir_var = tk.StringVar(value=self.config.get("model_dir", ""))
        ttk.Entry(pf, textvariable=self.model_dir_var).grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=(4, 0))
        ttk.Button(pf, text="浏览…", command=lambda: self._pick_dir(self.model_dir_var)).grid(row=1, column=2, pady=(4, 0))

        ttk.Label(pf, text="参数方案：").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(4, 0))
        self.profiles_path_var = tk.StringVar(value=self.profiles_path)
        ttk.Entry(pf, textvariable=self.profiles_path_var, state="readonly").grid(row=2, column=1, sticky="ew", padx=(0, 4), pady=(4, 0))
        ttk.Button(pf, text="选择…", command=self._pick_profiles_file).grid(row=2, column=2, pady=(4, 0))
        self._configs_dir = Path(__file__).resolve().parent / "configs"
        if self._configs_dir.is_dir():
            ttk.Button(pf, text="方案集", command=self._pick_from_configs).grid(row=2, column=4, padx=(6, 0), pady=(4, 0))
        pf.columnconfigure(1, weight=1)

        self.llamacpp_dir_var.trace_add("write", self._on_path_changed)
        self.model_dir_var.trace_add("write", self._on_path_changed)

        # 模型/方案选择 + 控制按钮
        cf = ttk.Frame(main)
        cf.pack(fill="x", pady=(0, 4))
        ttk.Label(cf, text="模型：").pack(side="left", padx=(0, 4))
        self.model_var = tk.StringVar(value=self.config.get("last_model", ""))
        self.model_combo = ttk.Combobox(cf, textvariable=self.model_var, values=[], state="readonly", width=28)
        self.model_combo.pack(side="left", padx=(0, 8))

        ttk.Label(cf, text="方案：").pack(side="left", padx=(0, 4))
        self.profile_var = tk.StringVar(value=self.config.get("current_profile", ""))
        self.profile_combo = ttk.Combobox(cf, textvariable=self.profile_var, values=[], state="readonly", width=16)
        self.profile_combo.pack(side="left", padx=(0, 8))

        self.start_btn = ttk.Button(cf, text="▶ 启动", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttk.Button(cf, text="■ 停止", state="disabled", command=self._force_stop)
        self.stop_btn.pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="未运行")
        ttk.Label(cf, textvariable=self.status_var, font=("Segoe UI", 9, "bold")).pack(side="left")

        # 日志
        lf = ttk.Frame(main)
        lf.pack(fill="both", expand=True)
        self.log_text = tk.Text(lf, wrap="word", state="disabled",
                                bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- 选择 ----------
    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or ".")
        if d:
            var.set(d)

    def _pick_llamacpp_dir(self):
        d = filedialog.askdirectory(
            title="选择 llama.cpp 目录", initialdir=self.llamacpp_dir_var.get() or ".")
        if d:
            self.llamacpp_dir_var.set(d)
            self._update_detect()
            status, msg, _ = detect_llamacpp(d)
            if status == "bad":
                messagebox.showwarning("目录无效", f"{d}\n\n{msg}")

    def _pick_profiles_file(self):
        p = filedialog.askopenfilename(
            title="选择参数方案文件", defaultextension=".json",
            initialdir=str(Path(self.profiles_path).parent) if self.profiles_path else ".",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if p:
            self._apply_profiles_path(p)

    def _pick_from_configs(self):
        files = list_config_files(self._configs_dir)
        if not files:
            messagebox.showinfo("方案集", "configs 目录下暂无配置文件", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("选择方案集")
        win.geometry("420x360")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, text="configs 目录下的备选配置文件：", padding=8).pack(anchor="w")
        lb = tk.Listbox(win)
        lb.pack(fill="both", expand=True, padx=8)
        for f in files:
            lb.insert("end", Path(f).name)
        if files:
            lb.selection_set(0)

        def ok():
            sel = lb.curselection()
            if sel:
                self._apply_profiles_path(files[sel[0]])
            win.destroy()

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="确定", command=ok).pack(side="right")
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))

    def _apply_profiles_path(self, p):
        self.profiles_path = p
        self.profiles_path_var.set(p)
        self.pm.load(p)
        self._refresh_profiles()

    def _update_detect(self):
        status, msg, _ = detect_llamacpp(self.llamacpp_dir_var.get())
        self.detect_var.set(msg)
        self.detect_label.config(foreground="#0a7" if status == "ok" else "#c33")

    def _on_path_changed(self, *a):
        self.config["llamacpp_dir"] = self.llamacpp_dir_var.get()
        self.config["model_dir"] = self.model_dir_var.get()
        self._update_detect()
        self._refresh_model_list()

    def _refresh_model_list(self):
        models = list_model_files(self.model_dir_var.get())
        self.model_combo["values"] = models
        if self.model_var.get() and self.model_var.get() not in models:
            if not Path(self.model_var.get()).is_absolute():
                self.model_var.set(models[0] if models else "")

    def _refresh_profiles(self):
        names = self.pm.names()
        self.profile_combo["values"] = names
        if not self.profile_var.get() and names:
            self.profile_var.set(names[0])
        elif self.profile_var.get() and self.profile_var.get() not in names and names:
            self.profile_var.set(names[0])
        self.config["current_profile"] = self.profile_var.get()

    # ---------- 启动 / 停止 ----------
    def _start(self):
        if self.runner.running:
            return
        llamacpp_dir = self.llamacpp_dir_var.get().strip()
        cands = server_executable_candidates(llamacpp_dir)
        if not cands:
            messagebox.showerror("错误", "未找到 llama-server。")
            return
        model = self.model_var.get().strip()
        if not model:
            messagebox.showerror("错误", "请选择模型。")
            return
        profile = self.pm.get(self.profile_var.get())
        if not profile:
            messagebox.showerror("错误", "请选择参数方案。")
            return
        model_full = model if Path(model).is_absolute() else str(
            Path(self.model_dir_var.get()) / model)
        try:
            cmd = build_command(cands[0], model_full, profile)
        except Exception as e:
            messagebox.showerror("构建命令失败", str(e))
            return

        # 保存选择
        self.config["last_model"] = model
        self.config["current_profile"] = self.profile_var.get()
        self._save_config()

        self._log(" ".join(quote_arg(c) for c in cmd) + "\n", "cmd")

        def on_started():
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.status_var.set("运行中")

        def on_error(e):
            messagebox.showerror("启动失败", str(e))

        self.runner.start(cmd, on_started=on_started, on_error=on_error)

    def _force_stop(self):
        if not self.runner.proc:
            return
        self._log("[已强制停止]\n", "rc")
        self.runner.force_stop()

    def _on_stopped(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("未运行")

    def _poll_log(self):
        for kind, data in self.runner.drain():
            if kind == "rc":
                self._log(f"[退出 {data}]\n", "rc")
                self._on_stopped()
            else:
                self._log(data + "\n", kind)
        self.root.after(80, self._poll_log)

    def _log(self, text, tag="out"):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text, tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ---------- 版本管理 ----------
    def _open_version_manager(self):
        if self.version_dialog is not None and self.version_dialog.top.winfo_exists():
            self.version_dialog.top.lift()
            self.version_dialog.top.focus_force()
            return
        self.version_dialog = VersionManagerDialog(
            self.root,
            llamacpp_dir_getter=lambda: self.llamacpp_dir_var.get().strip(),
            on_updated=self._update_detect)
        self.version_dialog.set_runner_running_getter(lambda: self.runner.running)

    # ---------- 关闭 ----------
    def _on_close(self):
        if self.runner.running:
            if not messagebox.askyesno("确认", "运行中，退出并停止？"):
                return
            self._force_stop()
        self._save_config()
        self.root.destroy()
