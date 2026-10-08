"""安全加固回归：安全响应头、Cookie 属性、口令策略、静态路径穿越防护。

约束来源：pilot/server.py 的 _security_headers（所有响应）、登录 Set-Cookie
（HttpOnly + SameSite=Strict；经反向代理 X-Forwarded-Proto=https 时追加
Secure）、password_policy_error（至少 8 位且含字母与数字）、_serve_static
（白名单 + resolve 前缀双重防穿越）。
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from pilot.db import Database
from pilot.auth import password_policy_error
from pilot.server import make_server

ADMIN_PW = "admin-pass-123"


class TestV26SecurityHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "pilot.sqlite3")
        self.httpd = make_server(self.db, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.db.close()
        self.tmp.cleanup()

    def _request(self, path, method="GET", payload=None, headers=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _init_admin(self, password=ADMIN_PW):
        return self._request("/api/auth/init", method="POST",
                             payload={"username": "admin", "password": password})

    def test_security_headers_on_static_and_api(self):
        # 静态页：完整 CSP（脚本/样式限定本源）+ 防嵌入/嗅探/引流
        status, headers, _ = self._request("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        # API：基础安全头 + 禁止缓存
        status, headers, _ = self._request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")

    def test_cookie_hardening(self):
        status, _, _ = self._init_admin()
        self.assertEqual(status, 200)
        # 登录并携带反向代理头 → Cookie 必须含 HttpOnly / SameSite=Strict / Secure
        status, headers, _ = self._request(
            "/api/auth/login", method="POST",
            payload={"username": "admin", "password": ADMIN_PW},
            headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", cookie)

    def test_password_policy(self):
        # 函数级：三类违规与合规口令
        self.assertEqual(password_policy_error("short1"), "密码至少 8 位")
        self.assertEqual(password_policy_error("nodigitpassword"),
                         "密码需同时包含字母与数字")
        self.assertEqual(password_policy_error("12345678"),
                         "密码需同时包含字母与数字")
        self.assertIsNone(password_policy_error(ADMIN_PW))
        # API 级：初始化管理员拒绝弱口令
        status, _, body = self._init_admin(password="nodigitpassword")
        self.assertEqual(status, 400)
        self.assertIn("字母与数字", body.decode("utf-8"))

    def test_path_traversal_blocked(self):
        for path in ("/static/..%2Fdb.py", "/static/../server.py",
                     "/static/%2e%2e/db.py", "/static/db.py"):
            status, _, _ = self._request(path)
            self.assertIn(status, (400, 403, 404), path)


if __name__ == "__main__":
    unittest.main()
