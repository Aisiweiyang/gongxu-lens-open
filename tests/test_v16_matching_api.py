"""v16 任务 3：多需求撮合预览 API（只读试算，不落库）回归。

通过真实 HTTP 服务器端到端验证（与 test_pilot.py 同构）：
① 角色权限（viewer 403）；② CSRF；③ 入参校验（空列表/超量勾选/重复/非字符串/任务
不存在/无供给）；④ 确定性（同请求两次，preview/reasons/batches/svg 逐位一致）；
⑤ 容量不超卖（批次分配合计 ≤ 实时可用量）与稳定性自检阻断对 = 0（引擎性质在
试点口径下成立）；⑥ 跨任务共享批次去重（同批次被两任务引用只计一次容量）；
⑦ 即算即返不落库（runs 表计数不变）+ audit 留痕；⑧ 响应标注「撮合建议（非交易/
非预留）」。
"""

import unittest

from tests.test_pilot import SUPPLIES, Client, PilotTestServer

# 两个不同需求（跨任务撮合需要 ≥2 个任务）
DEMAND_A = {
    "demand_id": "DM-A", "buyer_name": "撮合需方A（测试）", "target_material_code": "PP",
    "application": "周转箱", "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15", "destination": "园区",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}
DEMAND_B = dict(DEMAND_A, demand_id="DM-B", buyer_name="撮合需方B（测试）",
                required_mass_t=10.0, application="地板")
# T2 的自有批次（与共享批次 S1 区分）
SUPPLY_S3 = {
    "supply_id": "S3", "supplier_name": "丙", "material_code": "PP",
    "material_name_raw": "再生PP颗粒（灰色）", "polymer_type": "PP", "form": "颗粒",
    "color": "灰色", "grade": "注塑级", "mfi_g_10min": 10.0, "moisture_pct": 0.3,
    "ash_pct": 3.0, "recycled_content_pct": 92.0, "available_mass_t": 12.0,
    "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "园区",
    "price_cny_per_t": 4500.0, "price_includes_freight": False, "yield_pct": 100.0,
    "preprocessing": "已造粒", "transport_mode": "公路货运",
    "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 50.0,
    "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "测试",
}


class TestMatchingPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pilot.server as _servermod
        _servermod._login_attempts.clear()  # 模块级限流计数：消除套件间顺序依赖
        cls.server = PilotTestServer()
        cls.admin = Client(cls.server.port)
        status, data = cls.admin.post("/api/auth/init",
                                      {"username": "admin", "password": "admin-pass-123"},
                                      csrf=False)
        assert status == 200, data
        status, data = cls.admin.post("/api/auth/login",
                                      {"username": "admin", "password": "admin-pass-123"})
        cls.admin.csrf = data["csrf"]
        for username, role in (("editor1", "editor"), ("viewer1", "viewer")):
            status, data = cls.admin.post(
                "/api/users", {"username": username, "password": f"{username}-pass-123",
                               "role": role})
            assert status == 201, data
        cls.editor = Client(cls.server.port)
        status, data = cls.editor.post("/api/auth/login",
                                       {"username": "editor1", "password": "editor1-pass-123"})
        cls.editor.csrf = data["csrf"]
        cls.viewer = Client(cls.server.port)
        status, data = cls.viewer.post("/api/auth/login",
                                       {"username": "viewer1", "password": "viewer1-pass-123"})
        cls.viewer.csrf = data["csrf"]
        # 夹具：T1（供给 S1/S2）与 T2（供给 S1 共享批次 + S3 自有）
        for task_id, demand in (("T1", DEMAND_A), ("T2", DEMAND_B)):
            status, data = cls.editor.post(
                "/api/tasks", {"demand": demand, "task_id": task_id})
            assert status == 201, data
        for supply in SUPPLIES:
            status, data = cls.editor.post("/api/tasks/T1/supplies", {"supply": supply})
            assert status == 201, data
        status, data = cls.editor.post("/api/tasks/T2/supplies",
                                       {"supply": SUPPLIES[0]})  # 同 supply_id → 共享批次 S1
        assert status == 201, data
        status, data = cls.editor.post("/api/tasks/T2/supplies", {"supply": SUPPLY_S3})
        assert status == 201, data

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def preview(self, task_ids=("T1", "T2"), client=None):
        return (client or self.editor).post(
            "/api/matching/preview", {"task_ids": list(task_ids)})

    def test_01_viewer_forbidden(self):
        # 只读角色没有任何写接口（角色校验集中在路由层，撮合也不例外）
        status, data = self.viewer.post("/api/matching/preview",
                                        {"task_ids": ["T1"]})
        self.assertEqual(status, 403)
        self.assertIn("权限不足", data["error"])

    def test_02_csrf_enforced(self):
        fresh = Client(self.server.port)
        fresh.cookie = self.editor.cookie
        status, data = fresh.post("/api/matching/preview",
                                  {"task_ids": ["T1"]}, csrf=False)
        self.assertEqual(status, 403)
        self.assertIn("CSRF", data["error"])

    def test_03_input_validation(self):
        cases = [
            ([], 400, "非空"),
            (["T1", "T1"], 400, "重复"),
            ([str(i) for i in range(51)], 400, "50"),
            ([123], 400, "字符串"),
            (["不存在任务"], 404, "不存在"),
        ]
        for task_ids, expected, keyword in cases:
            status, data = self.editor.post("/api/matching/preview",
                                            {"task_ids": task_ids})
            self.assertEqual(status, expected, msg=f"case {task_ids[:2]}")
            self.assertIn(keyword, data["error"], msg=f"case {task_ids[:2]}")
        # 缺键
        status, data = self.editor.post("/api/matching/preview", {})
        self.assertEqual(status, 400)

    def test_04_task_without_supplies_rejected(self):
        status, data = self.editor.post(
            "/api/tasks", {"demand": dict(DEMAND_A, demand_id="DM-C"),
                           "task_id": "T3"})
        self.assertEqual(status, 201)
        status, data = self.preview(["T3"])
        self.assertEqual(status, 400)
        self.assertIn("无供给批次", data["error"])

    def test_05_preview_ok_and_annotations(self):
        status, data = self.preview()
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        # 标注口径（红线 1/7）：撮合建议非交易非预留；演示偏好模型如实标注
        self.assertIn("撮合建议（非交易/非预留）", data["disclaimers"][0])
        self.assertIn("不落库", data["disclaimers"][0])
        self.assertIn("演示毛利模型", data["disclaimers"][1])
        self.assertIn("撮合建议（非交易/非预留）", data["preview"]["note"])
        # 逐需求撮合理由：每个参与需求都有解释
        self.assertEqual(set(data["reasons"]), {"T1", "T2"})
        # 双边关系 SVG 存在且标注语义
        self.assertIn("matching-svg", data["svg"])
        self.assertIn("连线宽度=分配量", data["svg"])

    def test_06_engine_properties_no_oversell_stable(self):
        status, data = self.preview()
        self.assertEqual(status, 200)
        preview = data["preview"]
        # 容量不超卖：任一批次的分配合计 ≤ 该批次实时容量
        capacity = {b["batch_id"]: b["capacity_t"] for b in data["batches"]}
        used = {bid: 0.0 for bid in capacity}
        for match in preview["matches"]:
            for bid, mass in match["allocations"].items():
                used[bid] = used.get(bid, 0.0) + mass
        for bid, total in used.items():
            self.assertLessEqual(total, capacity[bid] + 1e-6,
                                 msg=f"批次 {bid} 超卖：{total} > {capacity[bid]}")
        # 稳定性自检阻断对 = 0（非零即实现缺陷）
        self.assertEqual(preview["stability"]["blocking_pairs"], 0)
        # 分配量与满足量一致（试点透传不改动引擎输出）
        for match in preview["matches"]:
            self.assertAlmostEqual(sum(match["allocations"].values()),
                                   match["satisfied_t"], places=3)

    def test_07_shared_batch_dedup(self):
        # S1 被两任务引用：批次池只计一次，referenced_by_tasks 如实记录
        status, data = self.preview()
        self.assertEqual(status, 200)
        batch_ids = [b["batch_id"] for b in data["batches"]]
        self.assertEqual(sorted(batch_ids), ["S1", "S2", "S3"])
        s1 = [b for b in data["batches"] if b["batch_id"] == "S1"][0]
        self.assertEqual(s1["referenced_by_tasks"], ["T1", "T2"])
        self.assertEqual(s1["capacity_t"], 8.0)  # 声明量，未被双任务重复计入

    def test_08_deterministic(self):
        status1, data1 = self.preview()
        status2, data2 = self.preview()
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        for key in ("preview", "reasons", "demands", "batches", "svg", "disclaimers"):
            self.assertEqual(data1[key], data2[key], msg=f"字段 {key} 两次结果不一致")

    def test_09_not_persisted_but_audited(self):
        with self.server.db.request():
            before = self.server.db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        status, data = self.preview()
        self.assertEqual(status, 200)
        with self.server.db.request():
            after = self.server.db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            audit = self.server.db.execute(
                "SELECT entity, event, after_json FROM audit_log"
                " WHERE event='matching.preview' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(before, after)  # 即算即返：不产生运行快照
        self.assertIsNotNone(audit)
        self.assertEqual(audit["entity"], "tasks:T1,T2")
        self.assertIn('"matches"', audit["after_json"] or "")


if __name__ == "__main__":
    unittest.main()
