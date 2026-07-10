#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数方案（profiles）与配置文件管理。

一个 JSON 配置文件包含多套方案（cpu/gpu/mix 等）；
configs 目录下可存在多个配置文件作为不同设备/模型的备选方案集。
"""

import json
from pathlib import Path


def load_json(path, fallback=None):
    """安全读取 JSON；失败时返回 fallback 的副本。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(fallback) if fallback is not None else None


def save_json(path, data):
    """写入 JSON（UTF-8、无 ASCII 转义、缩进 2）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ProfileManager:
    """加载/保存单个配置文件中的多套方案。"""

    def __init__(self, profiles_path=""):
        self.path = profiles_path
        self.profiles = {}
        if profiles_path:
            self.load(profiles_path)

    def load(self, path):
        """从 path 加载方案集；返回解析后的方案字典。"""
        self.path = path
        data = load_json(path, None)
        if not isinstance(data, dict) or not data:
            self.profiles = {}
            return self.profiles
        self.profiles = {name: prof for name, prof in data.items()
                         if isinstance(prof, dict)}
        return self.profiles

    def names(self):
        """返回方案名列表。"""
        return list(self.profiles.keys())

    def get(self, name):
        """获取指定方案；不存在返回 None。"""
        return self.profiles.get(name)

    def save(self, path=None):
        """保存当前方案集到 path（默认原路径）。成功返回 True。"""
        target = path or self.path
        if not target:
            return False
        save_json(target, self.profiles)
        self.path = target
        return True


def list_config_files(directory):
    """列出某目录下所有 JSON 配置文件（顶级，按名称排序）。"""
    base = Path(directory)
    if not base.is_dir():
        return []
    result = []
    try:
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_file() and entry.suffix.lower() == ".json":
                result.append(str(entry))
    except OSError:
        pass
    return result
