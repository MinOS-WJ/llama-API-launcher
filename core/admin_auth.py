#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理员登录认证：密码哈希 + 内存 session + 登录限流。

纯同步、零 Web 依赖（参照 ``core/api_keys.py`` 风格）。
- 密码用 PBKDF2-HMAC-SHA256 加盐哈希（低熵密码必须慢哈希），
  自描述格式 ``pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>``，
  未来提升迭代次数仍可校验旧哈希。
- session 明文格式 ``sess-<32 字节 urlsafe>``，仅签发时返回一次；
  内存只存 SHA-256 哈希，不存明文，服务重启自动失效。
- session 只服务 ``/api/*`` 管理端点；``/v1/*`` 推理代理仍只认 ``sk-`` Key。
- 登录限流：基于客户端 IP 的失败计数器，连续失败 5 次锁定 5 分钟。
- 所有写操作用模块级 ``_write_lock`` 串行化；密码/token 比对一律
  ``hmac.compare_digest``，无时序攻击面。
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time

from core.profiles import load_json, save_json

SESSION_PREFIX = "sess-"           # 区分 sk- API Key
SESSION_TTL = 12 * 3600             # session 有效期 12 小时
SESSION_DEBOUNCE = 60               # last_used_at 更新间隔（内存无落盘，仅防抖）
PBKDF2_ITERATIONS = 200_000         # OWASP 2023 推荐 ≥ 600k；本场景取 20w 兼顾安全与启动开销
PBKDF2_ALGO = "sha256"
SALT_BYTES = 16
MIN_PASSWORD_LEN = 6

# 登录限流参数
LOGIN_MAX_FAIL = 5                  # 连续失败次数上限
LOGIN_LOCK_TIME = 300               # 锁定时长（秒）

# 模块级写锁：保护 _sessions / _login_attempts / 配置文件写
_write_lock = threading.Lock()

# 内存 session 表：{token_hash: {created_at, expires_at, last_used_at}}
_sessions: dict = {}

# 登录失败计数：{ip: {"count": n, "locked_until": ts}}
_login_attempts: dict = {}


# -------------------- 密码哈希 --------------------

def hash_password(password: str, salt: bytes = None) -> str:
    """返回 ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`` 自描述格式。

    salt 为 None 时随机生成，故相同密码两次哈希结果不同。
    """
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"),
                             salt, PBKDF2_ITERATIONS)
    return (f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
            f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}")


def verify_password(password: str, stored: str) -> bool:
    """用 ``hmac.compare_digest`` 做常数时间比较，防时序攻击。

    stored 格式非法或算法不匹配返回 False（不抛异常）。
    """
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"),
                                 salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# -------------------- session 管理（内存） --------------------

def _hash_token(token: str) -> str:
    """session 明文 → SHA-256 哈希（内存不存明文）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(password: str, config_path: str) -> tuple:
    """校验密码并签发 session。

    返回 ``(ok, session_token_or_msg, expires_at)``。
    密码从 ``config_path`` 的 ``admin_password_hash`` 字段读取。
    """
    cfg = load_json(config_path, {})
    stored = cfg.get("admin_password_hash", "") or ""
    if not stored:
        return False, "管理员密码尚未初始化，请先在本机完成初始设置", 0
    if not verify_password(password, stored):
        return False, "密码错误", 0
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + SESSION_TTL
    with _write_lock:
        _sessions[_hash_token(token)] = {
            "created_at": now,
            "expires_at": expires,
            "last_used_at": now,
        }
    return True, token, expires


def verify_session(token: str) -> tuple:
    """校验 session token，返回 ``(ok, msg)``。

    过期或不存在返回失败；命中则刷新 ``last_used_at``（防抖，无需落盘）。
    过期项顺手清理（懒清理策略）。
    """
    if not token or not token.startswith(SESSION_PREFIX):
        return False, "无效的会话凭证"
    now = int(time.time())
    h = _hash_token(token)
    with _write_lock:
        s = _sessions.get(h)
        if not s:
            return False, "会话不存在或已失效（服务可能已重启）"
        if now > s["expires_at"]:
            _sessions.pop(h, None)
            return False, "会话已过期，请重新登录"
        if now - s["last_used_at"] >= SESSION_DEBOUNCE:
            s["last_used_at"] = now
        return True, "ok"


def revoke_session(token: str) -> None:
    """吊销指定 session（登出）。"""
    if not token:
        return
    with _write_lock:
        _sessions.pop(_hash_token(token), None)


def revoke_all_sessions() -> None:
    """吊销所有 session（改密后调用，强制全部重新登录）。"""
    with _write_lock:
        _sessions.clear()


def cleanup_expired() -> int:
    """清理过期 session，返回清理数量。lifespan 启动时调用一次。"""
    now = int(time.time())
    n = 0
    with _write_lock:
        for h in list(_sessions):
            if now > _sessions[h]["expires_at"]:
                _sessions.pop(h, None)
                n += 1
    return n


# -------------------- 登录限流 --------------------

def _check_login_rate(ip: str) -> tuple:
    """检查 IP 是否被锁定。返回 ``(ok, msg)``。锁定期间即使密码正确也拒绝。"""
    if not ip:
        return True, "ok"
    now = int(time.time())
    with _write_lock:
        rec = _login_attempts.get(ip)
        if rec and rec.get("locked_until", 0) > now:
            remain = rec["locked_until"] - now
            return False, f"登录失败次数过多，请 {remain} 秒后重试"
    return True, "ok"


def _record_login_fail(ip: str) -> None:
    """记录一次登录失败；达到上限则锁定该 IP。"""
    if not ip:
        return
    now = int(time.time())
    with _write_lock:
        rec = _login_attempts.get(ip)
        if rec and rec.get("locked_until", 0) > now:
            # 已锁定，累加计数但不延长锁期
            rec["count"] = rec.get("count", 0) + 1
            return
        count = (rec.get("count", 0) + 1) if rec else 1
        if count >= LOGIN_MAX_FAIL:
            _login_attempts[ip] = {"count": count, "locked_until": now + LOGIN_LOCK_TIME}
        else:
            _login_attempts[ip] = {"count": count, "locked_until": 0}


def _record_login_success(ip: str) -> None:
    """登录成功时清零该 IP 的失败计数。"""
    if not ip:
        return
    with _write_lock:
        _login_attempts.pop(ip, None)


# -------------------- 密码配置读写 --------------------

def is_password_set(config_path: str) -> bool:
    """是否已设置管理员密码。"""
    return bool(load_json(config_path, {}).get("admin_password_hash", ""))


def set_password(config_path: str, new_password: str) -> tuple:
    """设置/修改密码。返回 ``(ok, msg)``，msg 为 None 表示成功。

    调用方负责权限校验（首次设置需本机；修改需旧密码自证或当前 session）。
    改密后吊销所有现有 session，强制重新登录。
    """
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        return False, f"密码长度至少 {MIN_PASSWORD_LEN} 位"
    with _write_lock:
        cfg = load_json(config_path, {})
        cfg["admin_password_hash"] = hash_password(new_password)
        save_json(config_path, cfg)
    # 改密后吊销所有 session（读 config 已完成，可安全清表）
    revoke_all_sessions()
    return True, None


def change_password(config_path: str, old_password: str, new_password: str) -> tuple:
    """修改密码：先用旧密码自证身份，再写入新哈希。

    返回 ``(ok, msg)``。旧密码错误返回 401 级别失败。
    """
    cfg = load_json(config_path, {})
    stored = cfg.get("admin_password_hash", "") or ""
    if not stored:
        return False, "管理员密码尚未初始化，请先在本机完成初始设置"
    if not verify_password(old_password, stored):
        return False, "旧密码错误"
    return set_password(config_path, new_password)
