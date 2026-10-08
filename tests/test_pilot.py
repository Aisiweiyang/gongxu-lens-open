"""试点应用集成测试：认证、CSRF、角色权限、导入体检、运行快照、
选定方案校验、反馈校验、容量预留冲突、备份恢复一致性、审计日志。

通过真实 HTTP 服务器（stdlib http.server 起于临时端口）端到端验证。
"""

import http.client
import json
import threading
import unittest
import urllib.parse
from pathlib import Path
import tempfile

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot.db import Database
from pilot.server import make_server


class PilotTestServer:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "pilot.sqlite3")
        self.httpd = make_server(self.db, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.db.close()
        self.tmp.cleanup()


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ""
        self.csrf = ""

    def request(self, method, path, body=None, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        set_cookie = res.getheader("Set-Cookie") or ""
        conn.close()
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = {"_raw": raw.decode("utf-8", errors="replace")}
        return res.status, parsed

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None, csrf=True):
        return self.request("POST", path, body or {}, csrf=csrf)

    def put(self, path, body):
        return self.request("PUT", path, body)

    def delete(self, path):
        return self.request("DELETE", path)


DEMAND = {
    "demand_id": "D1", "buyer_name": "测试需方", "target_material_code": "PP",
    "application": "周转箱", "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15", "destination": "园区",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}

SUPPLIES = [
    {"supply_id": "S1", "supplier_name": "甲", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
     "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
     "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "园区",
     "price_cny_per_t": 4200.0, "price_includes_freight": False, "yield_pct": 100.0,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
     "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "测试"},
    {"supply_id": "S2", "supplier_name": "乙", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 11.0, "moisture_pct": 0.3,
     "ash_pct": 3.5, "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-08", "available_to": "2026-10-10", "origin": "外省",
     "price_cny_per_t": 3800.0, "price_includes_freight": False, "yield_pct": 100.0,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 480.0,
     "evidence_source": "检测报告", "evidence_date": "2026-08-05", "source": "测试"},
]


class TestPilotAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 登录限流为模块级全局计数：套件开始前清零，消除套件间顺序依赖
        import pilot.server as _servermod
        _servermod._login_attempts.clear()
        cls.server = PilotTestServer()
        cls.c = Client(cls.server.port)
        # 本类使用独立服务器：先初始化自己的管理员
        status, data = cls.c.post("/api/auth/init",
                                  {"username": "x", "password": "pass-12345678"}, csrf=False)
        assert status == 200, data

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_health(self):
        status, data = self.c.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["users_exist"])

    def test_init_admin_only_when_empty(self):
        # 已有账号（setUpClass 初始化过）时拒绝再次初始化
        status, data = self.c.post("/api/auth/init",
                                   {"username": "y", "password": "pass-12345678"}, csrf=False)
        self.assertEqual(status, 403)

    def test_init_admin_rejects_weak_password(self):
        # 在无账号服务器上，弱密码被拒绝（本类服务器已有账号 → 用新服务器验证）
        server = PilotTestServer()
        try:
            c = Client(server.port)
            status, data = c.post("/api/auth/init",
                                  {"username": "z", "password": "short"}, csrf=False)
            self.assertEqual(status, 400)
        finally:
            server.close()

    def test_login_bad_password(self):
        status, data = self.c.post("/api/auth/login",
                                   {"username": "x", "password": "wrong-password"})
        self.assertEqual(status, 401)

    def test_login_and_me(self):
        status, data = self.c.post("/api/auth/login",
                                   {"username": "x", "password": "pass-12345678"})
        self.assertEqual(status, 200)
        self.assertTrue(data["csrf"])
        self.c.csrf = data["csrf"]
        status, me = self.c.get("/api/auth/me")
        self.assertEqual(me["role"], "admin")

    def test_csrf_enforced_on_mutation(self):
        # 不带 CSRF 的写入被拒绝（服务器校验，不只隐藏按钮）
        fresh = Client(self.server.port)
        status, data = self.c.post("/api/auth/login",
                                   {"username": "x", "password": "pass-12345678"})
        fresh.csrf = data["csrf"]
        fresh.cookie = self.c.cookie
        status, data = fresh.post("/api/tasks", {"demand": DEMAND}, csrf=False)
        self.assertEqual(status, 403)


class TestPilotWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 登录限流为模块级全局计数：套件开始前清零，消除套件间顺序依赖
        import pilot.server as _servermod
        _servermod._login_attempts.clear()
        cls.server = PilotTestServer()
        cls.admin = Client(cls.server.port)
        status, data = cls.admin.post("/api/auth/init",
                                      {"username": "admin", "password": "admin-pass-123"},
                                      csrf=False)
        assert status == 200, data
        status, data = cls.admin.post("/api/auth/login",
                                      {"username": "admin", "password": "admin-pass-123"})
        cls.admin.csrf = data["csrf"]
        status, data = cls.admin.post("/api/users",
                                      {"username": "editor1", "password": "editor-pass-123",
                                       "role": "editor"})
        assert status == 201, data
        cls.editor = Client(cls.server.port)
        status, data = cls.editor.post("/api/auth/login",
                                       {"username": "editor1", "password": "editor-pass-123"})
        cls.editor.csrf = data["csrf"]
        # 夹具：任务 + 供给 + 一次运行快照（测试间互不依赖）
        status, data = cls.editor.post("/api/tasks", {"demand": DEMAND})
        assert status == 201, data
        for supply in SUPPLIES:
            status, data = cls.editor.post("/api/tasks/D1/supplies", {"supply": supply})
            assert status == 201, data
        status, data = cls.editor.post("/api/tasks/D1/runs")
        assert status == 201, data
        cls.run_id = data["run_id"]

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_01_fixture_ready(self):
        status, data = self.editor.get("/api/tasks/D1")
        self.assertEqual(status, 200)
        self.assertEqual(data["demand"]["required_mass_t"], 15.0)
        self.assertEqual(len(data["supplies"]), 2)
        self.assertGreaterEqual(len(data["runs"]), 1)

    def test_02_wizard_missing_required(self):
        status, data = self.editor.post("/api/tasks", {"demand": {"demand_id": "BAD"}})
        self.assertEqual(status, 400)
        self.assertIn("必填", data["error"])

    def test_03_add_supplies_idempotent_overwrite(self):
        # 同 supply_id 再录入 = 覆盖（幂等），不产生重复
        status, data = self.editor.post("/api/tasks/D1/supplies", {"supply": SUPPLIES[0]})
        self.assertEqual(status, 201, data)
        rows = self.server.db.execute(
            "SELECT COUNT(*) AS n FROM supplies WHERE task_id='D1' AND supply_id='S1'").fetchone()
        self.assertEqual(rows["n"], 1)

    def test_04_import_preview_and_structural_reject(self):
        csv_bad = ("supply_id,supplier_name,material_code,available_mass_t\n"
                   "X1,丙,PP,-5\n")
        status, data = self.editor.post("/api/tasks/D1/supplies/import",
                                        {"csv_text": csv_bad, "commit": False})
        self.assertEqual(status, 200)
        self.assertFalse(data["can_commit"])
        self.assertTrue(any(i["col"] == "available_mass_t" for i in data["preview"]["issues"]))
        status, data = self.editor.post("/api/tasks/D1/supplies/import",
                                        {"csv_text": csv_bad, "commit": True})
        self.assertEqual(status, 400)  # 结构错误拒绝导入，不静默丢行
        self.assertIn("结构错误", data["error"])

    def test_05_run_engine_and_run_snapshot(self):
        status, data = self.editor.post("/api/tasks/D1/runs")
        self.assertEqual(status, 201, data)
        run_id = data["run_id"]
        status, data = self.editor.get(f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        result = data["result"]
        self.assertIn("options", result)
        self.assertIn("scenarios", result)
        self.assertGreaterEqual(len(result["options"]), 2)
        self.assertIn("evidence_plans", result)

    def test_06_select_requires_valid_option(self):
        status, data = self.editor.post("/api/tasks/D1/selections",
                                        {"run_id": self.run_id, "option_id": "opt-fake"})
        self.assertEqual(status, 400)
        status, data = self.editor.get(f"/api/runs/{self.run_id}")
        valid_id = data["result"]["options"][0]["option_id"]
        status, data = self.editor.post("/api/tasks/D1/selections",
                                        {"run_id": self.run_id, "option_id": valid_id,
                                         "reason": "测试选定"})
        self.assertEqual(status, 201)

    def test_07_feedback_validation(self):
        status, data = self.editor.post("/api/tasks/D1/feedback",
                                        {"candidate_id": "S1", "decision": "通过",
                                         "reviewed_at": "2026-09-05"})
        self.assertEqual(status, 201)
        status, data = self.editor.post("/api/tasks/D1/feedback",
                                        {"candidate_id": "S1", "decision": "瞎写",
                                         "reviewed_at": "2026-09-05"})
        self.assertEqual(status, 400)

    def test_08_reservation_conflict(self):
        # S2 可供 18t：先预留 18t，再预留 1t → 409 冲突
        status, data = self.editor.post("/api/tasks/D1/reservations",
                                        {"supply_id": "S2", "quantity_t": 18.0,
                                         "run_id": self.run_id})
        self.assertEqual(status, 201)
        status, data = self.editor.post("/api/tasks/D1/reservations",
                                        {"supply_id": "S2", "quantity_t": 1.0})
        self.assertEqual(status, 409)
        self.assertIn("库存不足", data["error"])  # 全局批次库存口径
        # 释放后可以再预留
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT id FROM reservations WHERE task_id='D1' AND batch_id='S2'").fetchall()
        status, data = self.editor.post(f"/api/reservations/{rows[0]['id']}/release")
        self.assertEqual(status, 200)
        status, data = self.editor.post("/api/tasks/D1/reservations",
                                        {"supply_id": "S2", "quantity_t": 1.0})
        self.assertEqual(status, 201)

    def test_09_passport_download(self):
        status, body = self.editor.request("GET", "/api/tasks/D1/passport")
        self.assertEqual(status, 200)
        document = body
        self.assertEqual(document["schema_version"], "2.0.0")

    def test_10_role_enforcement(self):
        # 只读账号：GET 通过、写入 403（服务端校验）
        status, data = self.admin.post("/api/users",
                                       {"username": "viewer1", "password": "viewer-pass-123",
                                        "role": "viewer"})
        self.assertEqual(status, 201)
        viewer = Client(self.server.port)
        status, data = viewer.post("/api/auth/login",
                                   {"username": "viewer1", "password": "viewer-pass-123"})
        viewer.csrf = data["csrf"]
        status, data = viewer.get("/api/tasks/D1")
        self.assertEqual(status, 200)
        status, data = viewer.post("/api/tasks", {"demand": DEMAND})
        self.assertEqual(status, 403)
        status, data = viewer.post("/api/tasks/D1/runs")
        self.assertEqual(status, 403)
        status, data = viewer.get("/api/users")
        self.assertEqual(status, 403)
        # 普通编辑不能管理用户/备份
        status, data = self.editor.get("/api/users")
        self.assertEqual(status, 403)
        status, data = self.editor.post("/api/backup")
        self.assertEqual(status, 403)

    def test_11_backup_restore_roundtrip(self):
        # 备份前自造选定方案（不依赖其他测试的写入）
        status, data = self.editor.get(f"/api/runs/{self.run_id}")
        valid_id = data["result"]["options"][0]["option_id"]
        status, data = self.editor.post("/api/tasks/D1/selections",
                                        {"run_id": self.run_id, "option_id": valid_id,
                                         "reason": "备份前选定"})
        self.assertEqual(status, 201)
        status, data = self.admin.post("/api/backup")
        self.assertEqual(status, 200)
        backup_name = data["backup"]
        # 制造差异：恢复前删掉一个供给
        self.editor.delete("/api/tasks/D1/supplies/S1")
        status, data = self.editor.get("/api/tasks/D1/supplies")
        self.assertEqual(len(data["supplies"]), 1)
        # 恢复
        status, data = self.admin.post(f"/api/backups/{backup_name}/restore")
        self.assertEqual(status, 200)
        status, data = self.admin.get("/api/tasks/D1/supplies")
        self.assertEqual(len(data["supplies"]), 2)  # 恢复后任务/附件引用一致
        with self.server.db.request():
            rows = self.server.db.execute("SELECT COUNT(*) AS n FROM selections").fetchone()
        self.assertGreaterEqual(rows["n"], 1)  # 选定方案随恢复一致

    def test_12_audit_log(self):
        # 夹具事件自 setUpClass 起存在，不依赖其他测试
        status, admin_data = self.admin.get("/api/audit")
        self.assertEqual(status, 200)
        events = {row["event"] for row in admin_data["audit"]}
        for expected in ("auth.init_admin", "user.create", "task.create", "run.create"):
            self.assertIn(expected, events)
        status, denied = self.editor.get("/api/audit")
        self.assertEqual(status, 403)
        self.assertIn("error", denied)
        # 审计行含操作者与事件（前后版本字段存在于 audit_log 结构）
        row = admin_data["audit"][0]
        for key in ("actor", "event", "entity", "created_at"):
            self.assertIn(key, row)

    def test_13_idempotent_import(self):
        # 同 supply_id 重复导入 = 覆盖（幂等），不产生重复行
        csv_ok = ("supply_id,supplier_name,material_code,available_mass_t,mfi_g_10min,"
                  "moisture_pct,ash_pct,recycled_content_pct,available_from,available_to,"
                  "price_cny_per_t,distance_km,evidence_source,evidence_date,form,color,"
                  "polymer_type,grade,preprocessing,transport_mode,factor_id,material_name_raw\n"
                  "S9,丁,PP,5,12,0.3,3,95,2026-09-10,2026-10-31,4000,60,检测报告,2026-08-15,"
                  "颗粒,黑色,PP,注塑级,已造粒,公路货运,DEFRA-ARTIC-HGV-DIESEL-2024,再生PP颗粒（黑色）\n")
        status, data = self.editor.post("/api/tasks/D1/supplies/import",
                                        {"csv_text": csv_ok, "commit": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["written"], 1)
        status, data = self.editor.post("/api/tasks/D1/supplies/import",
                                        {"csv_text": csv_ok, "commit": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["written"], 1)
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT COUNT(*) AS n FROM supplies WHERE task_id='D1' AND supply_id='S9'").fetchone()
        self.assertEqual(rows["n"], 1)


if __name__ == "__main__":
    unittest.main()


class TestP0Regressions(unittest.TestCase):
    """v11 专项：全局库存防超卖、导入事务回滚、并发写互不污染、统一校验。"""

    @classmethod
    def setUpClass(cls):
        # 登录限流为模块级全局计数：套件开始前清零，消除套件间顺序依赖
        import pilot.server as _servermod
        _servermod._login_attempts.clear()
        cls.server = PilotTestServer()
        cls.admin = Client(cls.server.port)
        status, data = cls.admin.post("/api/auth/init",
                                      {"username": "admin", "password": "admin-pass-123"},
                                      csrf=False)
        assert status == 200, data
        status, data = cls.admin.post("/api/auth/login",
                                      {"username": "admin", "password": "admin-pass-123"})
        cls.admin.csrf = data["csrf"]
        cls.editor = Client(cls.server.port)
        status, data = cls.admin.post("/api/users",
                                      {"username": "editor1", "password": "editor-pass-123",
                                       "role": "editor"})
        assert status == 201, data
        status, data = cls.editor.post("/api/auth/login",
                                       {"username": "editor1", "password": "editor-pass-123"})
        cls.editor.csrf = data["csrf"]

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def _make_task(self, task_id, required=15.0):
        demand = dict(DEMAND, demand_id=task_id, required_mass_t=required)
        status, data = self.editor.post("/api/tasks", {"demand": demand})
        assert status == 201, data
        return status, data

    def test_P04_csv_partial_failure_rolls_back(self):
        # 第二行失败（价格 abc）→ 整批 400，第一行绝不入库
        self._make_task("P04A")
        csv_text = ("supply_id,supplier_name,material_code,available_mass_t,price_cny_per_t,"
                    "mfi_g_10min,moisture_pct,ash_pct,recycled_content_pct,available_from,"
                    "available_to,distance_km,evidence_source,evidence_date,form,color,"
                    "polymer_type,grade,preprocessing,transport_mode,factor_id,material_name_raw\n"
                    "P4-OK,甲,PP,8,4000,12,0.3,3,95,2026-09-10,2026-10-31,30,检测报告,2026-08-15,"
                    "颗粒,黑色,PP,注塑级,已造粒,公路货运,DEFRA-ARTIC-HGV-DIESEL-2024,再生PP颗粒（黑色）\n"
                    "P4-BAD,乙,PP,8,abc,12,0.3,3,95,2026-09-10,2026-10-31,30,检测报告,2026-08-15,"
                    "颗粒,黑色,PP,注塑级,已造粒,公路货运,DEFRA-ARTIC-HGV-DIESEL-2024,再生PP颗粒（黑色）\n")
        status, data = self.editor.post("/api/tasks/P04A/supplies/import",
                                        {"csv_text": csv_text, "commit": True})
        self.assertEqual(status, 400)
        # 预检与正式导入同一套校验：非法值在写入前即被拒绝（消息为预检拒绝）
        self.assertIn("拒绝导入", data["error"])
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT COUNT(*) AS n FROM supplies WHERE task_id='P04A'").fetchone()
        self.assertEqual(rows["n"], 0)  # 第一行绝不入库
        # 后续无关写请求不会提交残留：正常再导一次合法 CSV
        good = csv_text.replace("P4-BAD,乙,PP,8,abc", "P4-BAD,乙,PP,8,4000")
        status, data = self.editor.post("/api/tasks/P04A/supplies/import",
                                        {"csv_text": good, "commit": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["written"], 2)

    def test_P04_concurrent_writes_no_pollution(self):
        # 两个线程并发：一个合法导入、一个非法导入——合法成功且只有自己两行，非法零写入
        self._make_task("P04B")
        good = ("supply_id,supplier_name,material_code,available_mass_t,price_cny_per_t\n"
                "CA,甲,PP,8,4000\nCB,乙,PP,8,4000\n")
        bad = ("supply_id,supplier_name,material_code,available_mass_t,price_cny_per_t\n"
                "CX,丙,PP,8,-500\nCY,丁,PP,8,4000\n")
        results = {}
        def worker(name, csv_text, task_id):
            c = Client(self.server.port)
            c.cookie = self.editor.cookie
            c.csrf = self.editor.csrf
            status, data = c.post(f"/api/tasks/{task_id}/supplies/import",
                                  {"csv_text": csv_text, "commit": True})
            results[name] = (status, data)
        threads = [
            threading.Thread(target=worker, args=("good", good, "P04B")),
            threading.Thread(target=worker, args=("bad", bad, "P04B")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results["good"][0], 200, results["good"])
        self.assertEqual(results["good"][1]["written"], 2)
        self.assertEqual(results["bad"][0], 400)
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT supply_id FROM supplies WHERE task_id='P04B'").fetchall()
        ids = {r["supply_id"] for r in rows}
        self.assertEqual(ids, {"CA", "CB"})  # 非法导入的 CX/CY 未残留

    def test_P05_unified_validation_form_api_csv(self):
        # 同一非法值在表单/API/CSV 给出一致结论
        # 1) 表单/API：需求 -15
        bad_demand = dict(DEMAND, demand_id="P05A", required_mass_t=-15)
        status, data = self.editor.post("/api/tasks", {"demand": bad_demand})
        self.assertEqual(status, 400)
        self.assertTrue(data.get("details"))
        # 2) API：供给价格 -500、距离 -10、日期 2026-99-99
        bad_supply = dict(SUPPLIES[0], supply_id="P05S", price_cny_per_t=-500,
                          distance_km=-10, available_from="2026-99-99")
        status, data = self.editor.post("/api/tasks/P04A/supplies", {"supply": bad_supply})
        self.assertEqual(status, 400)
        fields = {d["field"] for d in data.get("details", [])}
        self.assertIn("price_cny_per_t", fields)
        self.assertIn("distance_km", fields)
        self.assertIn("available_from", fields)
        # 3) CSV 预览：同一非法值报结构错误（同一套校验）
        bad_csv = ("supply_id,supplier_name,material_code,available_mass_t,price_cny_per_t,"
                   "distance_km,available_from\n"
                   "P5X,丙,PP,8,-500,-10,2026-99-99\n")
        status, data = self.editor.post("/api/tasks/P04A/supplies/import",
                                        {"csv_text": bad_csv, "commit": False})
        self.assertEqual(status, 200)
        issue_cols = {i["col"] for i in data["preview"]["issues"]}
        self.assertIn("price_cny_per_t", issue_cols)
        self.assertIn("distance_km", issue_cols)
        self.assertIn("available_from", issue_cols)
        # 4) 千分位：预览与正式导入一致支持
        thousand_csv = ("supply_id,supplier_name,material_code,available_mass_t,price_cny_per_t\n"
                        "P5K,戊,PP,8,\"4,000\"\n")
        status, data = self.editor.post("/api/tasks/P04A/supplies/import",
                                        {"csv_text": thousand_csv, "commit": False})
        self.assertEqual(status, 200)
        self.assertFalse(data["preview"]["structural"])
        status, data = self.editor.post("/api/tasks/P04A/supplies/import",
                                        {"csv_text": thousand_csv, "commit": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["written"], 1)

    def test_P03_cross_task_inventory_no_oversell(self):
        # 同一批次（batch S2X=8t）在 T1 预留 8t 后，T2 再预留 → 409。
        # 用独立批次 ID，避免与并发测试的全局批次实体互相污染（污染本身就是 语义）。
        self._make_task("P03T1")
        self._make_task("P03T2")
        supply8 = dict(SUPPLIES[1], supply_id="S2X", available_mass_t=8.0)
        status, data = self.editor.post("/api/tasks/P03T1/supplies", {"supply": supply8})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post("/api/tasks/P03T2/supplies", {"supply": supply8})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post("/api/tasks/P03T1/reservations",
                                        {"supply_id": "S2X", "quantity_t": 8.0})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post("/api/tasks/P03T2/reservations",
                                        {"supply_id": "S2X", "quantity_t": 8.0})
        self.assertEqual(status, 409, data)
        self.assertIn("库存不足", data["error"])
        # 引擎使用实时可用量：T2 运行时 S2 可用量应为 0
        status, data = self.editor.post("/api/tasks/P03T2/runs")
        self.assertEqual(status, 201, data)
        status, data = self.editor.get(f"/api/runs/{data['run_id']}")
        s2 = next(s for s in data["result"]["supplies"] if s["supply_id"] == "S2X")
        self.assertAlmostEqual(s2["available_mass_t"], 0.0, places=4)

    def test_P03_concurrent_reservation_single_winner(self):
        # 并发预留同一批次 8t（两个任务各 8t）→ 恰好一个成功（原子条件更新）
        self._make_task("P03C1")
        self._make_task("P03C2")
        supply8 = dict(SUPPLIES[1], supply_id="S2", available_mass_t=8.0)
        for t in ("P03C1", "P03C2"):
            status, data = self.editor.post(f"/api/tasks/{t}/supplies", {"supply": supply8})
            assert status == 201, data
        results = {}
        def worker(task_id):
            c = Client(self.server.port)
            c.cookie = self.editor.cookie
            c.csrf = self.editor.csrf
            status, data = c.post(f"/api/tasks/{task_id}/reservations",
                                  {"supply_id": "S2", "quantity_t": 8.0})
            results[task_id] = (status, data)
        threads = [threading.Thread(target=worker, args=("P03C1",)),
                   threading.Thread(target=worker, args=("P03C2",))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        codes = sorted(results[t][0] for t in ("P03C1", "P03C2"))
        self.assertEqual(codes, [201, 409], results)  # 恰好一个成功
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT COALESCE(SUM(quantity_t),0) AS s FROM reservations "
                "WHERE batch_id='S2' AND status='reserved'").fetchone()
        self.assertAlmostEqual(rows["s"], 8.0, places=4)  # 总有效占用 ≤ 8t



    def test_P02_evidence_loop_updates_decision(self):
        # 补证通过 → 生成证据版本 → 供应快照更新 → 重算后门槛变化
        self._make_task("P02T1")
        supply = dict(SUPPLIES[0], supply_id="P02S", available_mass_t=8.0,
                      evidence_source="", evidence_date="")
        status, data = self.editor.post("/api/tasks/P02T1/supplies", {"supply": supply})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post("/api/tasks/P02T1/runs")
        self.assertEqual(status, 201, data)
        status, data = self.editor.get(f"/api/runs/{data['run_id']}")
        state = next(s for s in data["result"]["match_states"] if s["supply_id"] == "P02S")
        self.assertEqual(state["state"], "pending_evidence")
        # 创建证据任务 → 提交结构化证据 → 审核通过
        status, data = self.editor.post("/api/tasks/P02T1/evidence-tasks",
                                        {"action_id": "DETECTION_REPORT",
                                         "supply_id": "P02S", "assignee": "甲",
                                         "due_date": "2026-09-20"})
        self.assertEqual(status, 201, data)
        with self.server.db.request():
            et = self.server.db.execute(
                "SELECT id FROM evidence_tasks WHERE task_id='P02T1' AND supply_id='P02S'"
            ).fetchone()
        et_id = et["id"]
        # 非法流转：open → passed 直接跳步 → 409（状态机拦截）
        status, data = self.editor.put(f"/api/tasks/P02T1/evidence-tasks/{et_id}",
                                       {"status": "passed", "evidence": {}})
        self.assertEqual(status, 409)
        # 完整证据提交（open→submitted）
        evidence = {"evidence_type": "第三方检测报告",
                    "source_ref": "报告编号 R-2026-001",
                    "attachment_ref": "https://example.invalid/r-001.pdf",
                    "issuing_body": "某检测机构",
                    "measured_date": "2026-09-01",
                    "valid_until": "2027-09-01",
                    "measured_fields": {"mfi_g_10min": 12.0, "moisture_pct": 0.3,
                                        "ash_pct": 3.0, "recycled_content_pct": 95.0}}
        status, data = self.editor.put(f"/api/tasks/P02T1/evidence-tasks/{et_id}",
                                       {"status": "submitted", "evidence": evidence})
        self.assertEqual(status, 200, data)
        # 审核通过但证据被清空 → 400（passed 前置校验强制字段完整）
        status, data = self.editor.put(f"/api/tasks/P02T1/evidence-tasks/{et_id}",
                                       {"status": "passed", "evidence": {}})
        self.assertEqual(status, 400)
        status, data = self.editor.put(f"/api/tasks/P02T1/evidence-tasks/{et_id}",
                                       {"status": "passed", "evidence": evidence,
                                        "verdict_reason": "检测报告完整有效"})
        self.assertEqual(status, 200, data)
        # 版本历史可回放
        status, data = self.editor.get("/api/tasks/P02T1/evidence-versions")
        self.assertEqual(status, 200)
        versions = data["evidence_versions"]
        self.assertGreaterEqual(len(versions), 1)
        self.assertEqual(versions[0]["status"], "passed")
        self.assertEqual(versions[0]["version_no"], 1)
        # 供应快照已更新
        status, data = self.editor.get("/api/tasks/P02T1/supplies")
        snap = next(s for s in data["supplies"] if s["supply_id"] == "P02S")
        self.assertTrue(snap["evidence_source"])
        self.assertEqual(snap["mfi_g_10min"], 12.0)
        # 重算后门槛变化：pending_evidence → eligible（8t 无单供资格，但状态为 eligible）
        status, data = self.editor.post("/api/tasks/P02T1/runs")
        self.assertEqual(status, 201, data)
        status, data = self.editor.get(f"/api/runs/{data['run_id']}")
        state2 = next(s for s in data["result"]["match_states"] if s["supply_id"] == "P02S")
        self.assertEqual(state2["state"], "eligible")

    def test_P06_stale_run_cannot_confirm(self):
        # 运行 → 改价 → 旧 run 确认被 409 阻止；重算后确认成功且生成 confirmed 预留
        self._make_task("P06T1", required=15.0)
        for supply in (dict(SUPPLIES[0], supply_id="P06A", available_mass_t=8.0),
                       dict(SUPPLIES[1], supply_id="P06B", available_mass_t=18.0)):
            status, data = self.editor.post("/api/tasks/P06T1/supplies", {"supply": supply})
            assert status == 201, data
        status, data = self.editor.post("/api/tasks/P06T1/runs")
        self.assertEqual(status, 201, data)
        run_id = data["run_id"]
        status, data = self.editor.get(f"/api/runs/{run_id}")
        valid_id = data["result"]["options"][0]["option_id"]
        # 改价（供给快照变化）
        status, data = self.editor.post("/api/tasks/P06T1/supplies",
                                        {"supply": dict(SUPPLIES[1], supply_id="P06B",
                                                        available_mass_t=18.0,
                                                        price_cny_per_t=3900.0)})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post("/api/tasks/P06T1/confirm",
                                        {"run_id": run_id, "option_id": valid_id,
                                         "reason": "尝试确认旧方案"})
        self.assertEqual(status, 409, data)
        self.assertIn("已过期", data["error"])
        # 重算 → 确认成功
        status, data = self.editor.post("/api/tasks/P06T1/runs")
        self.assertEqual(status, 201, data)
        run_id2 = data["run_id"]
        status, data = self.editor.get(f"/api/runs/{run_id2}")
        valid_id2 = next((o["option_id"] for o in data["result"]["options"]
                          if o.get("allocation") and o["kind"] != "baseline"), None)
        status, data = self.editor.post("/api/tasks/P06T1/confirm",
                                        {"run_id": run_id2, "option_id": valid_id2,
                                         "reason": "确认采购"})
        self.assertEqual(status, 201, data)
        with self.server.db.request():
            rows = self.server.db.execute(
                "SELECT COUNT(*) AS n FROM reservations WHERE task_id='P06T1' "
                "AND status='confirmed'").fetchone()
        self.assertGreaterEqual(rows["n"], 1)

    def test_P06_passport_deterministic_from_snapshot(self):
        # 同一历史运行重复导出护照，内容与哈希一致
        self._make_task("P06T2", required=15.0)
        for supply in (dict(SUPPLIES[0], supply_id="P06C", available_mass_t=8.0),
                       dict(SUPPLIES[1], supply_id="P06D", available_mass_t=18.0)):
            status, data = self.editor.post("/api/tasks/P06T2/supplies", {"supply": supply})
            assert status == 201, data
        status, data = self.editor.post("/api/tasks/P06T2/runs")
        run_id = data["run_id"]
        bodies = []
        for _ in range(2):
            status, body = self.editor.request(
                "GET", f"/api/tasks/P06T2/passport?run_id={run_id}")
            self.assertEqual(status, 200)
            bodies.append(body)
        self.assertEqual(bodies[0], bodies[1])
        import hashlib
        self.assertEqual(hashlib.sha256(json.dumps(bodies[0], ensure_ascii=False,
                                                   sort_keys=True).encode("utf-8")).hexdigest(),
                         hashlib.sha256(json.dumps(bodies[1], ensure_ascii=False,
                                                   sort_keys=True).encode("utf-8")).hexdigest())
        # 组合方案每批次完整排放项（最后一条）
        for option in bodies[0].get("options", []):
            if option.get("allocation") and option["kind"] == "combo" \
                    and option.get("option_id") in bodies[0].get("representatives", []):
                terms = option.get("emission_terms") or []
                self.assertEqual(len(terms), len(option["allocation"]))
                for entry in terms:
                    self.assertTrue(entry["terms"])



if __name__ == "__main__":
    unittest.main()
