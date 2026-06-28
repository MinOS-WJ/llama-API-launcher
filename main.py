#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— 统一主入口。

运行：python main.py
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，便于 from core... 导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tkinter as tk
from app import LlamaLauncherApp


def main():
    root = tk.Tk()
    app = LlamaLauncherApp(root)
    app.log_text.tag_config("out", foreground="#d4d4d4")
    app.log_text.tag_config("err", foreground="#ff6b6b")
    app.log_text.tag_config("cmd", foreground="#6bdfff")
    app.log_text.tag_config("rc", foreground="#ffd166")
    root.mainloop()


if __name__ == "__main__":
    main()
