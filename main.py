#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama.cpp API 启动器 —— 统一主入口。

运行：python main.py [--host HOST] [--port PORT] [--no-browser]
默认监听 127.0.0.1:8686，浏览器访问 http://localhost:8686
远程访问使用 --host 0.0.0.0 允许外部连接。
"""

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 确保能导入同目录下的包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web.app import init_app, run_server


def check_port(host, port):
    """检测端口是否可用。返回 (ok, error_message)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True, None
        except OSError as e:
            return False, str(e)


def open_browser_later(host, port):
    """延迟 2 秒打开浏览器（daemon 线程，等待服务器就绪）。"""
    def opener():
        time.sleep(2)
        webbrowser.open(f"http://{host}:{port}")
    threading.Thread(target=opener, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="llama.cpp API 启动器（Web 版）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Web 服务器监听地址（默认 127.0.0.1，远程访问用 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8686,
                        help="Web 服务器监听端口（默认 8686）")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器")
    args = parser.parse_args()

    # 端口占用检测
    ok, err = check_port(args.host, args.port)
    if not ok:
        print(f"错误：端口 {args.host}:{args.port} 被占用！")
        print(f"请关闭占用该端口的程序，或使用 --port 指定其他端口。")
        print(f"错误信息：{err}")
        sys.exit(1)

    # 初始化应用（加载配置、方案管理器、运行器）
    init_app(str(Path(__file__).resolve().parent))

    # 仅本地访问时自动打开浏览器（远程模式不打开）
    if not args.no_browser and args.host == "127.0.0.1":
        open_browser_later(args.host, args.port)

    # 启动服务器（阻塞，Ctrl+C 优雅退出）
    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
