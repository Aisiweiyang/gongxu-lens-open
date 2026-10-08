"""v17 任务 2：撮合建议单导出（POST /api/matching/sheet，只读不落库）回归。

通过真实 HTTP 服务器端到端验证（与 test_v16_matching_api.py 同构夹具）：
① 角色权限（viewer 403）与 CSRF；② 入参校验与 preview 同规；
③ 响应为独立 HTML（text/html、无外部资源、无脚本）；④ 内容与预览 API 数值
逐位一致（满足量/成本/分配明细/撮合理由，同一格式化口径）；⑤ 标注齐全
（非交易非预留/演示毛利模型/生成时间/入参快照）；⑥ 恶意文本全部转义（XSS 面）；
⑦ 即算即返不落库（runs 计数不变）+ audit 留痕（matching.sheet）；
⑧ 渲染纯函数确定性（同输入同字节，时间戳显式传入）。
"""

import http.client
import json
import unittest

from src.matching_sheet import render_matching_sheet
from tests.test_pilot import SUPPLIES, Client, PilotTestServer
from tests.test_v16_matching_api import DEMAND_A, DEMAND_B, SUPPLY_S3

HOSTILE = '<img src=x onerror="alert(1)">'


def raw_post(client, path, body):
    """带响应头的 POST（tests.test_pilot.Client 不透出头）：返回 (status, headers, text)。"""
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=60)
    headers = {"Content-Type": "application/json"}
    if client.cookie:
        headers["Cookie"] = client.cookie
    if client.csrf:
        headers["X-CSRF-Token"] = client.csrf
    conn.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                 headers=headers)
    res = conn.getresponse()
    raw = res.read()
    out_headers = {k.lower(): v for k, v in res.getheaders()}
    conn.close()
    return res.status, out_headers, raw.decode("utf-8", errors="replace")


class TestMatchingSheet(unittest.TestCase):
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
        # 与 v16 撮合 API 套件同构夹具：T1（S1/S2）+ T2（共享 S1 + 自有 S3）
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

    def sheet(self, task_ids=("T1", "T2"), client=None):
        return raw_post(client or self.editor, "/api/matching/sheet",
                        {"task_ids": list(task_ids)})

    def preview(self, task_ids=("T1", "T2")):
        return self.editor.post("/api/matching/preview", {"task_ids": list(task_ids)})

    def test_01_viewer_forbidden_and_csrf(self):
        status, data = self.viewer.post("/api/matching/sheet", {"task_ids": ["T1"]})
        self.assertEqual(status, 403)
        self.assertIn("权限不足", data["error"])
        fresh = Client(self.server.port)
        fresh.cookie = self.editor.cookie
        status, data = fresh.post("/api/matching/sheet",
                                  {"task_ids": ["T1"]}, csrf=False)
        self.assertEqual(status, 403)
        self.assertIn("CSRF", data["error"])

    def test_02_input_validation_same_as_preview(self):
        for task_ids, expected, keyword in (([], 400, "非空"),
                                            (["T1", "T1"], 400, "重复"),
                                            (["不存在任务"], 404, "不存在")):
            status, data = self.editor.post("/api/matching/sheet",
                                            {"task_ids": task_ids})
            self.assertEqual(status, expected, msg=f"case {task_ids[:2]}")
            self.assertIn(keyword, data["error"], msg=f"case {task_ids[:2]}")
        status, data = self.editor.post("/api/matching/sheet", {})
        self.assertEqual(status, 400)

    def test_03_html_response_shape(self):
        status, headers, html = self.sheet()
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertIn("inline", headers.get("content-disposition", ""))
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<title>撮合建议单", html)
        # 零外部资源、零脚本（打印友好 + 与护照同一渲染纪律）
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        # 四个章节齐全（含未满足/剩余容量的空态说明）
        for section in ("一、匹配明细", "二、未满足需求与原因", "三、批次剩余容量",
                        "四、双边关系视图"):
            self.assertIn(section, html)

    def test_04_values_match_preview(self):
        status, data = self.preview()
        self.assertEqual(status, 200)
        _status, _headers, html = self.sheet()
        for match in data["preview"]["matches"]:
            did = match["demand_id"]
            self.assertIn(f">{match['satisfied_t']:g}</td>", html)
            self.assertIn(f"{match['proposed_cost_cny']:,.2f}", html)
            self.assertIn(did, html)
            for bid, mass in match["allocations"].items():
                self.assertIn(f"{bid} × {mass:g}", html)
            # 撮合理由逐字一致（服务端同一字段透传）
            self.assertIn(data["reasons"][did], html.replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">")
                          .replace("&quot;", '"').replace("&#39;", "'"))
        for demand in data["demands"]:
            self.assertIn(demand["buyer_name"], html)
        for batch in data["batches"]:
            self.assertIn(batch["batch_id"], html)
        for bid, mass in data["preview"]["leftover_capacity_t"].items():
            self.assertIn(f">{mass:g}</td>", html)
        # 双边关系 SVG 与预览响应同源
        self.assertIn("matching-svg", html)
        self.assertIn("连线宽度=分配量", html)

    def test_05_annotations_complete(self):
        _status, data = self.preview()
        _s, _h, html = self.sheet()
        self.assertIn("撮合建议（非交易/非预留）", html)
        self.assertIn("演示毛利模型", html)
        self.assertIn("不落库", html)
        self.assertIn("生成时间：", html)
        self.assertIn("入参快照：", html)
        self.assertIn("勾选任务 T1、T2", html)
        self.assertIn("Gale-Shapley 容量变体", html)
        self.assertIn("阻断对 0", html)
        for disclaimer in data["disclaimers"]:
            self.assertIn(disclaimer, html)

    def test_06_hostile_text_escaped(self):
        # 恶意构造：需方名与供应商名带注入向量；校验放行任意文本，输出必须全转义
        status, data = self.editor.post(
            "/api/tasks", {"demand": dict(DEMAND_A, demand_id="DM-X",
                                          buyer_name="需方" + HOSTILE + "（测试）"),
                           "task_id": "TX"})
        self.assertEqual(status, 201, data)
        status, data = self.editor.post(
            "/api/tasks/TX/supplies",
            {"supply": dict(SUPPLY_S3, supply_id="SX",
                            supplier_name="供应商" + HOSTILE)})
        self.assertEqual(status, 201, data)
        status, _headers, html = self.sheet(["TX"])
        self.assertEqual(status, 200)
        # 原始注入标签不得出现（转义后以 &lt;img 文本形式呈现；onerror 只是普通文本）
        self.assertNotIn("<img", html)
        self.assertNotIn('onerror="alert(1)"', html)
        self.assertIn("&lt;img", html)
        # 需求行与批次表都经转义渲染（需方名/供应商名可见且无害）
        self.assertIn("需方" + "&lt;img", html)

    def test_07_not_persisted_but_audited(self):
        with self.server.db.request():
            before = self.server.db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        status, _headers, _html = self.sheet()
        self.assertEqual(status, 200)
        with self.server.db.request():
            after = self.server.db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            audit = self.server.db.execute(
                "SELECT entity, event, after_json FROM audit_log"
                " WHERE event='matching.sheet' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(before, after)  # 只读不落库：建议单导出不产生运行快照
        self.assertIsNotNone(audit)
        self.assertEqual(audit["entity"], "tasks:T1,T2")

    def test_08_pure_renderer_deterministic(self):
        payload = {
            "ok": True,
            "preview": {
                "matches": [{"demand_id": "T1", "allocations": {"S2": 15.0},
                             "satisfied_t": 15.0, "remaining_t": 0.0,
                             "proposed_cost_cny": 59820.0, "cost_problems": [],
                             "note": "撮合建议（非交易/非预留）"}],
                "unmatched": [{"demand_id": "T2", "remaining_t": 2.0, "reason": "容量不足"}],
                "leftover_capacity_t": {"S1": 1.0},
                "stability": {"blocking_pairs": 0, "checked_pairs": [],
                              "algorithm": "Gale-Shapley 容量变体（hospital-residents）"},
                "note": "撮合建议（非交易/非预留）；供给侧偏好为演示毛利模型",
            },
            "reasons": {"T1": "按需求侧偏好分配 1 个批次"},
            "demands": [{"task_id": "T1", "buyer_name": "需方A（演示）",
                         "required_mass_t": 15.0, "target_material_code": "PP"}],
            "batches": [{"batch_id": "S1", "supplier_name": "甲（演示）",
                         "material_code": "PP", "capacity_t": 8.0,
                         "referenced_by_tasks": ["T1"]}],
            "svg": "<svg id='matching-svg'></svg>",
            "elapsed_ms": 3.2,
            "disclaimers": ["标注一", "标注二"],
        }
        html1 = render_matching_sheet(payload, "2026-09-12 12:00:00")
        html2 = render_matching_sheet(payload, "2026-09-12 12:00:00")
        self.assertEqual(html1, html2)  # 同输入同字节（时间戳为显式入参）
        self.assertNotEqual(render_matching_sheet(payload, "2026-09-12 12:00:01"), html1)
        self.assertIn("S2 × 15", html1)
        self.assertIn("59,820.00", html1)
        self.assertIn("容量不足", html1)
        self.assertIn("标注一", html1)


if __name__ == "__main__":
    unittest.main()
