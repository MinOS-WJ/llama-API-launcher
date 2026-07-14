#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``core.admin_auth`` 单元测试 + ``/api/auth/*`` API 测试。

覆盖：
- 密码哈希（PBKDF2）：正确/错误/salt 随机/格式非法/不同 iterations
- session：创建/校验/过期/伪造/revoke/改密吊销/cleanup
- 登录限流：5 次失败锁定/锁定期正确密码也拒/成功清零
- API（TestClient）：status/setup/login/change-password/logout + 远程 session 访问受保护端点

运行：python -m unittest tests.test_admin_auth
依赖：httpx（仅 API 测试场景，不计入 requirements.txt）
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import admin_auth
from core import api_keys
from core.profiles import load_json, save_json

try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except ImportError:
    HAS_TESTCLIENT = False


def _make_config(tmpdir, password=None):
    """创建临时配置文件；password 非 None 时写入哈希。返回路径。"""
    path = str(Path(tmpdir) / "config.json")
    cfg = {"admin_password_hash": "",
           "admin_auth_enabled": True}
    if password is not None:
        cfg["admin_password_hash"] = admin_auth.hash_password(password)
    save_json(path, cfg)
    return path


# -------------------- 密码哈希 --------------------

class TestPasswordHash(unittest.TestCase):
    """PBKDF2 哈希与校验。"""

    def test_hash_format(self):
        h = admin_auth.hash_password("secret123")
        parts = h.split("$")
        self.assertEqual(parts[0], "pbkdf2_sha256")
        self.assertEqual(int(parts[1]), admin_auth.PBKDF2_ITERATIONS)
        # salt 与 hash 均为 base64
        import base64
        base64.b64decode(parts[2])
        base64.b64decode(parts[3])

    def test_verify_correct(self):
        h = admin_auth.hash_password("mypassword")
        self.assertTrue(admin_auth.verify_password("mypassword", h))

    def test_verify_wrong(self):
        h = admin_auth.hash_password("mypassword")
        self.assertFalse(admin_auth.verify_password("wrong", h))

    def test_salt_random(self):
        """相同密码两次哈希应不同（salt 随机），但都能校验通过。"""
        h1 = admin_auth.hash_password("same")
        h2 = admin_auth.hash_password("same")
        self.assertNotEqual(h1, h2)
        self.assertTrue(admin_auth.verify_password("same", h1))
        self.assertTrue(admin_auth.verify_password("same", h2))

    def test_verify_malformed_stored(self):
        """格式非法的 stored 字符串返回 False，不抛异常。"""
        self.assertFalse(admin_auth.verify_password("x", "not_a_hash"))
        self.assertFalse(admin_auth.verify_password("x", ""))
        self.assertFalse(admin_auth.verify_password("x", "md5$abc$d2Qk"))
        self.assertFalse(admin_auth.verify_password("x", "pbkdf2_sha256$abc$bad$b64"))

    def test_verify_different_iterations(self):
        """旧哈希用更低 iterations 仍可校验（自描述格式）。"""
        import base64, hashlib
        salt = b"\x01" * 16
        old_iter = 1000
        dk = hashlib.pbkdf2_hmac("sha256", b"oldpw", salt, old_iter)
        stored = f"pbkdf2_sha256${old_iter}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
        self.assertTrue(admin_auth.verify_password("oldpw", stored))
        self.assertFalse(admin_auth.verify_password("wrong", stored))


# -------------------- session --------------------

class TestSession(unittest.TestCase):
    """内存 session 表：创建/校验/吊销/过期。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg_path = _make_config(self._tmp.name, password="pass123")
        admin_auth.revoke_all_sessions()
        admin_auth._login_attempts.clear()

    def tearDown(self):
        self._tmp.cleanup()
        admin_auth.revoke_all_sessions()
        admin_auth._login_attempts.clear()

    def test_create_session_success(self):
        ok, token, expires = admin_auth.create_session("pass123", self.cfg_path)
        self.assertTrue(ok)
        self.assertTrue(token.startswith(admin_auth.SESSION_PREFIX))
        self.assertGreater(expires, int(time.time()))

    def test_create_session_wrong_password(self):
        ok, msg, expires = admin_auth.create_session("wrong", self.cfg_path)
        self.assertFalse(ok)
        self.assertIn("密码错误", msg)
        self.assertEqual(expires, 0)

    def test_create_session_no_password_set(self):
        cfg2 = _make_config(self._tmp.name + "_2", password=None)
        ok, msg, expires = admin_auth.create_session("any", cfg2)
        self.assertFalse(ok)
        self.assertIn("尚未初始化", msg)

    def test_verify_session_valid(self):
        ok, token, _ = admin_auth.create_session("pass123", self.cfg_path)
        vok, vmsg = admin_auth.verify_session(token)
        self.assertTrue(vok)

    def test_verify_session_forged(self):
        ok, msg = admin_auth.verify_session("sess-forgedtoken123456")
        self.assertFalse(ok)

    def test_verify_session_wrong_prefix(self):
        ok, msg = admin_auth.verify_session("sk-notasession")
        self.assertFalse(ok)

    def test_verify_session_empty(self):
        ok, msg = admin_auth.verify_session("")
        self.assertFalse(ok)

    def test_revoke_session(self):
        ok, token, _ = admin_auth.create_session("pass123", self.cfg_path)
        self.assertTrue(admin_auth.verify_session(token)[0])
        admin_auth.revoke_session(token)
        self.assertFalse(admin_auth.verify_session(token)[0])

    def test_revoke_all_after_set_password(self):
        ok, token, _ = admin_auth.create_session("pass123", self.cfg_path)
        self.assertTrue(admin_auth.verify_session(token)[0])
        # 改密后所有 session 失效
        admin_auth.set_password(self.cfg_path, "newpass456")
        self.assertFalse(admin_auth.verify_session(token)[0])
        # 新密码可登录
        ok2, token2, _ = admin_auth.create_session("newpass456", self.cfg_path)
        self.assertTrue(ok2)

    def test_cleanup_expired(self):
        """手动插入过期 session，cleanup_expired 应清理。"""
        ok, token, _ = admin_auth.create_session("pass123", self.cfg_path)
        # 手动把过期时间设到过去
        h = admin_auth._hash_token(token)
        admin_auth._sessions[h]["expires_at"] = int(time.time()) - 1
        n = admin_auth.cleanup_expired()
        self.assertGreaterEqual(n, 1)
        self.assertFalse(admin_auth.verify_session(token)[0])

    def test_verify_expired_session_lazy_cleanup(self):
        """过期 session 在 verify 时被懒清理。"""
        ok, token, _ = admin_auth.create_session("pass123", self.cfg_path)
        h = admin_auth._hash_token(token)
        admin_auth._sessions[h]["expires_at"] = int(time.time()) - 1
        vok, msg = admin_auth.verify_session(token)
        self.assertFalse(vok)
        self.assertIn("过期", msg)
        # 已从内存表移除
        self.assertNotIn(h, admin_auth._sessions)


# -------------------- 登录限流 --------------------

class TestRateLimit(unittest.TestCase):

    def setUp(self):
        admin_auth._login_attempts.clear()

    def tearDown(self):
        admin_auth._login_attempts.clear()

    def test_under_limit_allowed(self):
        for _ in range(admin_auth.LOGIN_MAX_FAIL - 1):
            admin_auth._record_login_fail("1.2.3.4")
        ok, msg = admin_auth._check_login_rate("1.2.3.4")
        self.assertTrue(ok)

    def test_locked_after_max_fails(self):
        for _ in range(admin_auth.LOGIN_MAX_FAIL):
            admin_auth._record_login_fail("1.2.3.4")
        ok, msg = admin_auth._check_login_rate("1.2.3.4")
        self.assertFalse(ok)
        self.assertIn("秒后重试", msg)

    def test_locked_even_correct_password(self):
        """锁定期内即使密码正确也拒绝（由调用方在 create_session 前检查）。"""
        for _ in range(admin_auth.LOGIN_MAX_FAIL):
            admin_auth._record_login_fail("1.2.3.4")
        ok, msg = admin_auth._check_login_rate("1.2.3.4")
        self.assertFalse(ok)

    def test_success_clears_counter(self):
        for _ in range(admin_auth.LOGIN_MAX_FAIL - 1):
            admin_auth._record_login_fail("1.2.3.4")
        admin_auth._record_login_success("1.2.3.4")
        ok, msg = admin_auth._check_login_rate("1.2.3.4")
        self.assertTrue(ok)

    def test_different_ips_independent(self):
        for _ in range(admin_auth.LOGIN_MAX_FAIL):
            admin_auth._record_login_fail("1.1.1.1")
        # 另一 IP 不受影响
        ok, msg = admin_auth._check_login_rate("2.2.2.2")
        self.assertTrue(ok)

    def test_empty_ip_not_limited(self):
        admin_auth._record_login_fail("")
        ok, msg = admin_auth._check_login_rate("")
        self.assertTrue(ok)


# -------------------- 密码配置读写 --------------------

class TestPasswordConfig(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg_path = _make_config(self._tmp.name, password=None)
        admin_auth.revoke_all_sessions()

    def tearDown(self):
        self._tmp.cleanup()
        admin_auth.revoke_all_sessions()

    def test_is_password_set_false(self):
        self.assertFalse(admin_auth.is_password_set(self.cfg_path))

    def test_is_password_set_true(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.assertTrue(admin_auth.is_password_set(self.cfg_path))

    def test_set_password_too_short(self):
        ok, msg = admin_auth.set_password(self.cfg_path, "123")
        self.assertFalse(ok)
        self.assertIn("至少", msg)
        self.assertFalse(admin_auth.is_password_set(self.cfg_path))

    def test_change_password_wrong_old(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        ok, msg = admin_auth.change_password(self.cfg_path, "wrongold", "newpass456")
        self.assertFalse(ok)
        self.assertIn("旧密码错误", msg)

    def test_change_password_success(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        ok, msg = admin_auth.change_password(self.cfg_path, "pass123", "newpass456")
        self.assertTrue(ok, msg)
        # 旧密码登录失败
        ok2, _, _ = admin_auth.create_session("pass123", self.cfg_path)
        self.assertFalse(ok2)
        # 新密码登录成功
        ok3, _, _ = admin_auth.create_session("newpass456", self.cfg_path)
        self.assertTrue(ok3)


# -------------------- API 测试（TestClient）--------------------

@unittest.skipIf(not HAS_TESTCLIENT, "httpx 未安装，跳过 TestClient 测试")
class TestAuthAPI(unittest.TestCase):
    """``/api/auth/*`` 端点 + 中间件 session 校验。"""

    def setUp(self):
        from web import app as webapp
        self.webapp = webapp
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg_path = _make_config(self._tmp.name, password=None)
        # 设置 web.app 全局状态
        webapp.config_path = self.cfg_path
        webapp.config_data = load_json(self.cfg_path, {})
        webapp.api_keys_path = str(Path(self._tmp.name) / "api_keys.json")
        webapp.web_host = "127.0.0.1"
        webapp.web_port = 8686
        admin_auth.revoke_all_sessions()
        admin_auth._login_attempts.clear()
        # 本机客户端（用于 setup 等本机限定端点）
        self.local_client = TestClient(webapp.app, client=("127.0.0.1", 0))
        # 远程客户端（模拟非本机访问，需认证）
        self.remote_client = TestClient(webapp.app)

    def tearDown(self):
        self._tmp.cleanup()
        admin_auth.revoke_all_sessions()
        admin_auth._login_attempts.clear()

    # ---- /api/auth/status（公开）----

    def test_status_no_password_set(self):
        r = self.local_client.get("/api/auth/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["password_set"])
        self.assertTrue(data["auth_enabled"])

    def test_status_password_set(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.get("/api/auth/status")
        data = r.json()
        self.assertTrue(data["password_set"])

    # ---- /api/auth/setup（本机 + 未初始化）----

    def test_setup_local_success(self):
        r = self.local_client.post("/api/auth/setup",
                                   json={"password": "pass123", "confirm": "pass123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["success"])
        self.assertTrue(admin_auth.is_password_set(self.cfg_path))

    def test_setup_remote_forbidden(self):
        r = self.remote_client.post("/api/auth/setup",
                                    json={"password": "pass123", "confirm": "pass123"})
        self.assertEqual(r.status_code, 403)

    def test_setup_already_set(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        r = self.local_client.post("/api/auth/setup",
                                   json={"password": "new", "confirm": "new"})
        self.assertEqual(r.status_code, 409)

    def test_setup_mismatch(self):
        r = self.local_client.post("/api/auth/setup",
                                   json={"password": "a", "confirm": "b"})
        self.assertEqual(r.status_code, 400)

    def test_setup_too_short(self):
        r = self.local_client.post("/api/auth/setup",
                                   json={"password": "123", "confirm": "123"})
        self.assertEqual(r.status_code, 400)

    # ---- /api/auth/login ----

    def test_login_success(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["session_token"].startswith(admin_auth.SESSION_PREFIX))
        self.assertGreater(data["expires_at"], int(time.time()))

    def test_login_wrong_password(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_login_no_password_set(self):
        r = self.local_client.post("/api/auth/login", json={"password": "any"})
        self.assertEqual(r.status_code, 409)

    def test_login_rate_limited(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        # 连续失败 LOGIN_MAX_FAIL 次
        for _ in range(admin_auth.LOGIN_MAX_FAIL):
            self.local_client.post("/api/auth/login", json={"password": "wrong"})
        # 第 6 次：正确密码也应被锁
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        self.assertEqual(r.status_code, 429)

    # ---- /api/auth/change-password ----

    def test_change_password_success(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/change-password",
                                   json={"old_password": "pass123",
                                         "new_password": "newpass456"})
        self.assertEqual(r.status_code, 200, r.text)
        # 旧密码失效
        r2 = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        self.assertEqual(r2.status_code, 401)

    def test_change_password_wrong_old(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/change-password",
                                   json={"old_password": "wrong",
                                         "new_password": "newpass456"})
        self.assertEqual(r.status_code, 401)

    # ---- /api/auth/logout ----

    def test_logout_revokes_session(self):
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        token = r.json()["session_token"]
        # logout 需带 session（受保护端点）
        r2 = self.local_client.post("/api/auth/logout",
                                    headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 200)
        # session 已失效
        self.assertFalse(admin_auth.verify_session(token)[0])

    def test_logout_remote_without_session_forbidden(self):
        """远程无凭证访问受保护的 logout 应 403。"""
        r = self.remote_client.post("/api/auth/logout")
        self.assertEqual(r.status_code, 403)

    # ---- 远程 session 访问受保护端点 ----

    def test_remote_protected_with_session(self):
        """远程携带有效 session 可访问受保护端点。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        # 先登录拿 session（本机登录）
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        token = r.json()["session_token"]
        # 远程携带 session 访问 /api/keys（受保护）
        r2 = self.remote_client.get("/api/keys",
                                    headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 200)

    def test_remote_protected_without_session_forbidden(self):
        """远程无凭证访问受保护端点 403。"""
        r = self.remote_client.get("/api/keys")
        self.assertEqual(r.status_code, 403)

    def test_remote_protected_with_expired_session(self):
        """远程携带过期/无效 session 访问受保护端点 403 + auth_expired。"""
        r = self.remote_client.get("/api/keys",
                                   headers={"Authorization": "sess-invalidtoken123"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json().get("auth_expired"))

    # ---- 本机也需认证（密码设置后本机不再免认证）----

    def test_local_protected_without_session_forbidden(self):
        """本机无凭证访问受保护端点 → 403（本机不再免认证）。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.get("/api/keys")
        self.assertEqual(r.status_code, 403)

    def test_local_protected_with_session_ok(self):
        """本机携带有效 session 访问受保护端点 → 200。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        token = r.json()["session_token"]
        r2 = self.local_client.get("/api/keys",
                                   headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 200)

    def test_local_protected_with_admin_key_ok(self):
        """本机携带 admin-scope API Key 访问受保护端点 → 200。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        # 创建一个 admin Key（create_key 返回含明文，仅此一次）
        entry = api_keys.create_key(self.webapp.api_keys_path, label="test", scope="admin")
        plaintext = entry["plaintext"]
        r = self.local_client.get("/api/keys",
                                  headers={"Authorization": f"Bearer {plaintext}"})
        self.assertEqual(r.status_code, 200)

    def test_local_logout_without_session_forbidden(self):
        """本机无凭证访问受保护的 logout → 403。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/logout")
        self.assertEqual(r.status_code, 403)

    # ---- /v1/* 不受 session 影响 ----

    def test_v1_rejects_session_token(self):
        """/v1/* 只认 sk- Key，session 不被接受。"""
        admin_auth.set_password(self.cfg_path, "pass123")
        self.webapp.config_data = load_json(self.cfg_path, {})
        r = self.local_client.post("/api/auth/login", json={"password": "pass123"})
        token = r.json()["session_token"]
        # /v1/models 带 session 应 401（不是 sk- 前缀）
        r2 = self.local_client.get("/v1/models",
                                   headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r2.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
