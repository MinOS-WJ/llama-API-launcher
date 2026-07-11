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

    def get_profile(self, name):
        """获取单个方案的副本；不存在返回 None。"""
        prof = self.profiles.get(name)
        return dict(prof) if prof is not None else None

    def upsert(self, name, profile):
        """新增或覆盖单个方案。返回是否成功（name 非空且 profile 为 dict）。"""
        if not name or not isinstance(name, str) or not isinstance(profile, dict):
            return False
        self.profiles[name] = dict(profile)
        return True

    def delete(self, name):
        """删除单个方案。返回是否实际删除。"""
        if name in self.profiles:
            del self.profiles[name]
            return True
        return False

    def rename(self, old_name, new_name):
        """重命名方案。返回是否成功。"""
        if (old_name not in self.profiles or not new_name
                or new_name in self.profiles):
            return False
        self.profiles[new_name] = self.profiles.pop(old_name)
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


# -------------------- 方案字段校验 --------------------

# 整型字段（0 或空省略；负值仅 gpu_layers 的 -1 有意义）
INT_FIELDS = ("context_size", "parallel", "batch_size", "ubatch_size",
              "gpu_layers", "threads")
# 布尔字段
BOOL_FIELDS = ("flash_attn", "cont_batching", "mlock", "no_mmap",
               "embedding", "reranking", "jinja", "verbose")
# 字符串字段
STR_FIELDS = ("pooling", "chat_template", "draft_model",
              "grammar_file", "extra_args")


def normalize_profile(raw):
    """把前端传入的方案字段规整为标准结构（缺失字段补默认值）。

    非法值会被修正为默认值并记入 warnings，避免启动时悄悄失败。
    返回 (profile, warnings)。
    """
    warnings = []
    if not isinstance(raw, dict):
        return ({}, ["方案数据不是对象"])
    prof = {}
    for f in INT_FIELDS:
        v = raw.get(f, 0)
        sval = str(v).strip() if v is not None else ""
        if sval == "" or sval == "0":
            prof[f] = 0
            continue
        try:
            prof[f] = int(sval)
        except ValueError:
            prof[f] = 0
            warnings.append(f"{f} 值 {v!r} 非整数，已重置为 0")
    for f in BOOL_FIELDS:
        v = raw.get(f, False)
        prof[f] = bool(v)
    for f in STR_FIELDS:
        v = raw.get(f, "")
        prof[f] = str(v).strip() if v is not None else ""
    return (prof, warnings)


def validate_profile(prof):
    """对规整后的方案做语义检查。返回 (errors, warnings)。"""
    errors, warnings = [], []
    if not isinstance(prof, dict):
        return (["方案数据不是对象"], [])
    gl = prof.get("gpu_layers", 0)
    # embedding / reranking 互斥
    if prof.get("embedding") and prof.get("reranking"):
        errors.append("embedding 与 reranking 模式互斥，不可同时启用")
    # GPU 层数提示（仅当选择明显不合理时给警告，不阻断）
    return (errors, warnings)

