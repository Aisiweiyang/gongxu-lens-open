"""v12 自主验收：七阻断门的补齐用例。

新增覆盖（此前 P0 套件未直接锁定的场景）：
- G3 库存不超卖：释放/过期/confirmed 的回收语义（释放恢复可用、过期惰性回收、
  confirmed 不被自动过期误伤）；
- G6 会话重放：登出后旧 token 重放被拒；服务重启后数据持久（重启=新进程级服务实例）；
- 比较与确认分离：勾选比较方案不产生任何库存预留；
- 反馈 真实/模拟 分离存储；
- G7 基准不漂移：候选运输方式变化时基准排放恒定、项目侧变化。
"""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pilot.db import Database
from pilot.server import make_server

DEMAND = {
    "demand_id": "G1", "buyer_name": "测试需方", "target_material_code": "PP",
    "application": "周转箱", "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15", "destination": "园区",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}

SUPPLY = {"supply_id": "SX", "supplier_name": "甲", "material_code": "PP",
          "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
          "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
          "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
          "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "园区",
          "price_cny_per_t": 4200.0, "price_includes_freight": False, "yield_pct": 100.0,
          "preprocessing": "已造粒", "transport_mode": "公路货运",
          "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
          "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "测试"}


class GateServer:
    def __init__(self, db=None, path=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = path or Path(self.tmp.name) / "pilot.sqlite3"
        self.db = db or Database(self.path)
        self.httpd = make_server(self.db, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def cleanup(self):
        self.db.close()
        self.tmp.cleanup()


class GateClient:
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

    def get(self, path, csrf=True):
        return self.request("GET", path, csrf=csrf)

    def post(self, path, body=None, csrf=True):
        return self.request("POST", path, body or {}, csrf=csrf)


def setup_task(c, task_id, supply_id="SX", mass=8.0):
    status, data = c.post("/api/tasks", {"demand": dict(DEMAND, demand_id=task_id),
                                         "task_id": task_id})
    assert status == 201, data
    supply = dict(SUPPLY, supply_id=supply_id, available_mass_t=mass)
    status, data = c.post(f"/api/tasks/{task_id}/supplies", {"supply": supply})
    assert status == 201, data
    return supply_id


class TestV12GateAdditions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 登录限流是模块级全局计数（每 IP 900s 窗口 10 次，产品行为正确）：
        # 全量套件中前置套件的登录已占用窗口，本套件开始前清零，避免测试顺序干扰。
        import pilot.server as servermod
        servermod._login_attempts.clear()
        cls.server = GateServer()
        cls.c = GateClient(cls.server.port)
        status, _ = cls.c.post("/api/auth/init",
                               {"username": "admin", "password": "admin-pass-123"}, csrf=False)
        assert status == 200
        status, data = cls.c.post("/api/auth/login",
                                  {"username": "admin", "password": "admin-pass-123"}, csrf=False)
        assert status == 200
        cls.c.csrf = data["csrf"]

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.server.cleanup()

    def _reserve(self, task_id, batch, quantity, expires_at):
        status, data = self.c.post(f"/api/tasks/{task_id}/reservations",
                                   {"batch_id": batch, "quantity_t": quantity,
                                    "expires_at": expires_at})
        self.assertEqual(status, 201, data)
        status, rows = self.c.get(f"/api/tasks/{task_id}/reservations")
        rid = max(r["id"] for r in rows["reservations"])
        return rid

    def _availability(self, batch):
        status, data = self.c.get(f"/api/batches/{batch}/availability")
        self.assertEqual(status, 200)
        return data["available_mass_t"]

    def test_G3_release_restores_availability(self):
        batch = setup_task(self.c, "G3REL", supply_id="SX-REL")
        rid = self._reserve("G3REL", batch, 6.0, "2026-12-31")
        self.assertAlmostEqual(self._availability(batch), 2.0, places=3)
        status, data = self.c.post(f"/api/reservations/{rid}/release")
        self.assertEqual(status, 200, data)
        self.assertAlmostEqual(self._availability(batch), 8.0, places=3)

    def test_G3_expired_reservation_lazy_recovery(self):
        batch = setup_task(self.c, "G3EXP", supply_id="SX-EXP")
        rid = self._reserve("G3EXP", batch, 6.0, "2020-01-01")  # 已过期的预留
        self.assertAlmostEqual(self._availability(batch), 8.0, places=3)  # 惰性过期不占用
        with self.server.db.request():
            row = self.server.db.execute(
                "SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["status"], "expired")

    def test_G3_confirmed_reservation_never_auto_expires(self):
        batch = setup_task(self.c, "G3CONF", supply_id="SX-CONF")
        rid = self._reserve("G3CONF", batch, 5.0, "2026-12-31")
        status, data = self.c.post(f"/api/reservations/{rid}/confirm")
        self.assertEqual(status, 200, data)
        # 已确认占用量保持，且之后的预留只能拿到剩余 3t
        status, data = self.c.post(f"/api/tasks/G3CONF/reservations",
                                   {"batch_id": batch, "quantity_t": 4.0,
                                    "expires_at": "2026-12-31"})
        self.assertEqual(status, 409, data)
        self.assertAlmostEqual(self._availability(batch), 3.0, places=3)
        with self.server.db.request():
            row = self.server.db.execute(
                "SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["status"], "confirmed")

    def test_G6_replay_old_session_rejected_after_logout(self):
        other = GateClient(self.server.port)
        status, data = other.post("/api/auth/login",
                                  {"username": "admin", "password": "admin-pass-123"},
                                  csrf=False)
        self.assertEqual(status, 200)
        old_cookie, old_csrf = other.cookie, data["csrf"]
        other.csrf = data["csrf"]
        status, _ = other.post("/api/auth/logout")
        self.assertEqual(status, 200)
        replay = GateClient(self.server.port)
        replay.cookie, replay.csrf = old_cookie, old_csrf
        status, _ = replay.get("/api/tasks")
        self.assertEqual(status, 401, "登出后旧会话 token 重放必须 401")

    def test_restart_persistence(self):
        # 服务级重启（新 Database 对象 + 新 HTTP 服务器，同一数据文件）
        server2 = GateServer(db=None, path=self.server.path)
        try:
            c2 = GateClient(server2.port)
            status, data = c2.post("/api/auth/login",
                                   {"username": "admin", "password": "admin-pass-123"},
                                   csrf=False)
            self.assertEqual(status, 200)
            status, data = c2.get("/api/tasks")
            self.assertEqual(status, 200)
            ids = [t["id"] for t in data["tasks"]]
            self.assertIn("G3REL", ids, "重启后此前写入的任务仍存在")
        finally:
            server2.stop()
            server2.db.close()

    def test_compare_select_creates_no_reservation(self):
        batch = setup_task(self.c, "G3SEL", supply_id="SX-SEL")
        status, data = self.c.post("/api/tasks/G3SEL/runs")
        self.assertEqual(status, 201, data)
        run_id = data["run_id"]
        status, run_data = self.c.get(f"/api/runs/{run_id}")
        option_id = next(o["option_id"] for o in run_data["result"]["options"]
                         if o["kind"] == "baseline")
        status, data = self.c.post("/api/tasks/G3SEL/selections",
                                   {"run_id": run_id, "option_id": option_id,
                                    "reason": "演示比较"})
        self.assertEqual(status, 201, data)
        with self.server.db.request():
            n = self.server.db.execute(
                "SELECT COUNT(*) AS n FROM reservations WHERE task_id='G3SEL'").fetchone()["n"]
        self.assertEqual(n, 0, "比较勾选不得产生库存预留（确认才占用）")

    def test_feedback_real_simulated_separated(self):
        batch = setup_task(self.c, "G3FB", supply_id="SX-FB")
        for kind, decision in (("真实", "通过"), ("模拟", "补证")):
            status, data = self.c.post("/api/tasks/G3FB/feedback", {
                "candidate_id": batch, "decision": decision,
                "observation_type": kind, "reviewed_at": "2026-09-20",
                "stage": "交付", "actual_qty_t": 8, "actual_date": "2026-09-20",
                "actual_cost_cny": 33600, "quality_result": "合格",
                "return_breach": "无"})
            self.assertEqual(status, 201, data)
        status, data = self.c.get("/api/tasks/G3FB/feedback")
        kinds = sorted(r["observation_type"] for r in data["feedback"])
        self.assertEqual(kinds, sorted(["真实", "模拟"]), "真实与模拟分列存储、不混算")

    def test_feedback_invalid_dates_and_negative_values_rejected(self):
        batch = setup_task(self.c, "G3FBV", supply_id="SX-FBV")
        base = {"candidate_id": batch, "decision": "通过", "observation_type": "真实",
                "reviewed_at": "2026-09-20"}
        status, data = self.c.post("/api/tasks/G3FBV/feedback",
                                   dict(base, actual_date="2026/09/20"))
        self.assertEqual(status, 400, data)
        status, data = self.c.post("/api/tasks/G3FBV/feedback",
                                   dict(base, actual_qty_t=-1))
        self.assertEqual(status, 400, data)
        status, data = self.c.post("/api/tasks/G3FBV/feedback",
                                   dict(base, actual_cost_cny=-5))
        self.assertEqual(status, 400, data)
        status, data = self.c.post("/api/tasks/G3FBV/feedback",
                                   dict(base, actual_date="2026-09-20", actual_qty_t=8))
        self.assertEqual(status, 201, data)


class TestV12BaselineInvariant(unittest.TestCase):
    """G7 基准不漂移：候选运输方式变化只影响项目侧，基准排放恒定。"""

    def test_baseline_constant_when_candidate_transport_changes(self):
        from test_v10_regressions import make_demand, make_supply, make_factors
        from src.material import analyze_task
        factors = make_factors()
        factors["factors"]["RAIL-1"] = {
            "factor_id": "RAIL-1", "role": "transport", "stage": "transport",
            "material_code": "ANY", "value": 0.0200, "unit": "kgCO2e/(t·km)",
            "boundary": "TTW", "region": "中国", "year": "2025",
            "applies_to": ["铁路"], "source_name": "测试因子", "source_url": "",
            "version": 1, "grade": "权威"}

        def run_with(mode):
            supply = make_supply(transport_mode=mode, distance_km=300.0, factor_id="")
            return analyze_task(make_demand(), [supply], factors,
                                resolved_mode="local", requested_mode="local",
                                fallback_reason="", mode_note="")

        by_road = run_with("公路货运")
        by_rail = run_with("铁路")
        base_road = next(o["emissions_kgco2e"] for o in by_road["options"]
                         if o["kind"] == "baseline")
        base_rail = next(o["emissions_kgco2e"] for o in by_rail["options"]
                         if o["kind"] == "baseline")
        self.assertIsNotNone(base_road)
        self.assertAlmostEqual(base_road, base_rail, places=6,
                               msg="基准排放不得随候选运输方式漂移")
        proj_road = next(o for o in by_road["options"] if o["kind"] == "single")
        proj_rail = next(o for o in by_rail["options"] if o["kind"] == "single")
        self.assertIsNotNone(proj_road["emissions_kgco2e"])
        self.assertIsNotNone(proj_rail["emissions_kgco2e"])
        self.assertNotAlmostEqual(proj_road["emissions_kgco2e"],
                                  proj_rail["emissions_kgco2e"], places=2,
                                  msg="项目侧排放应随运输方式变化（否则说明解析失效）")
        self.assertLess(proj_rail["emissions_kgco2e"], proj_road["emissions_kgco2e"])


if __name__ == "__main__":
    unittest.main()
