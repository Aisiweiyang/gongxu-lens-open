"""v18 任务 3：大案例异步运行路径（POST /api/tasks/{id}/runs?async=1 + 作业轮询）回归。

通过真实 HTTP 服务器验证（与 test_v16_matching_api.py 同构夹具）：
① async 受理 202（job_id/status）+ audit 留痕（run.async）；② 轮询至 done 后凭
run_id 取得结果，且与同步路径的 result_json **逐位一致**（红线 3：异步只改调度
不改计算）；③ 单作业守卫：槽位被占时第二个 async 409，同步路径不受影响；
④ 未知作业 404；⑤ viewer 403 / CSRF；⑥ 失败路径如实呈现（注入异常 → failed +
错误文案 + 守卫释放）；⑦ 已结束作业清理上限（防内存无界）。
"""

import json
import time
import unittest

from tests.test_pilot import SUPPLIES, Client, PilotTestServer
from tests.test_v16_matching_api import DEMAND_A


class TestAsyncRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pilot.server as sm
        cls.sm = sm
        sm._login_attempts.clear()
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
        status, data = cls.editor.post(
            "/api/tasks", {"demand": DEMAND_A, "task_id": "T1"})
        assert status == 201, data
        for supply in SUPPLIES:
            status, data = cls.editor.post("/api/tasks/T1/supplies", {"supply": supply})
            assert status == 201, data

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        # 模块级作业表清零：用例间互不污染
        self.sm._ASYNC_JOBS.clear()
        self.sm._ASYNC_SLOT["active"] = None

    def tearDown(self):
        self.sm._ASYNC_JOBS.clear()
        self.sm._ASYNC_SLOT["active"] = None

    def async_run(self, task_id="T1", client=None):
        return (client or self.editor).post(
            "/api/tasks/" + task_id + "/runs?async=1", {})

    def wait_job(self, job_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body = self.editor.get("/api/runs/jobs/" + job_id)
            if status == 200 and body.get("status") in ("done", "failed"):
                return status, body
            time.sleep(0.2)
        raise AssertionError("作业轮询超时（60s）")

    def test_01_async_accepted_and_audited(self):
        status, body = self.async_run()
        self.assertEqual(status, 202)
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["job_id"])
        self.assertIn("重启即丢失", body["note"])  # 内存态语义如实告知
        with self.server.db.request():
            audit = self.server.db.execute(
                "SELECT event, entity FROM audit_log WHERE event='run.async'"
                " ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(audit)
        self.assertIn(body["task_id"], audit["entity"])
        _s, job = self.wait_job(body["job_id"])
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["run_id"])
        self.assertIsNotNone(job["elapsed_s"])

    def test_02_async_result_identical_to_sync(self):
        status, sync = self.editor.post("/api/tasks/T1/runs")
        self.assertEqual(status, 201)
        status, body = self.async_run()
        self.assertEqual(status, 202)
        _s, job = self.wait_job(body["job_id"])
        self.assertEqual(job["status"], "done")
        with self.server.db.request():
            row_sync = self.server.db.execute(
                "SELECT result_json FROM runs WHERE id=?", (sync["run_id"],)).fetchone()
            row_async = self.server.db.execute(
                "SELECT result_json FROM runs WHERE id=?", (job["run_id"],)).fetchone()
        # 红线 3：异步只是调度方式，引擎输出必须逐位一致
        self.assertEqual(row_sync["result_json"], row_async["result_json"])
        # 完成后照常走既有的 run 读取路径
        status, run = self.editor.get("/api/runs/" + job["run_id"])
        self.assertEqual(status, 200)
        self.assertEqual(run["run"]["id"], job["run_id"])

    def test_03_single_slot_guard(self):
        self.sm._ASYNC_SLOT["active"] = "fake-running-job"  # 直接管守卫：确定性
        try:
            status, body = self.async_run()
            self.assertEqual(status, 409)
            self.assertIn("已有大案例后台计算", body["error"])
            # 同步路径不受守卫影响
            status, _body = self.editor.post("/api/tasks/T1/runs")
            self.assertEqual(status, 201)
        finally:
            self.sm._ASYNC_SLOT["active"] = None

    def test_04_unknown_job_404(self):
        status, body = self.editor.get("/api/runs/jobs/deadbeefdeadbeef")
        self.assertEqual(status, 404)
        self.assertIn("作业不存在", body["error"])

    def test_05_viewer_and_csrf_rejected(self):
        status, data = self.viewer.post("/api/tasks/T1/runs?async=1", {})
        self.assertEqual(status, 403)
        fresh = Client(self.server.port)
        fresh.cookie = self.editor.cookie
        status, data = fresh.post("/api/tasks/T1/runs?async=1", {}, csrf=False)
        self.assertEqual(status, 403)
        self.assertIn("CSRF", data["error"])

    def test_06_failed_job_honest_and_releases_slot(self):
        handler_cls = self.server.httpd.RequestHandlerClass
        orig = handler_cls._execute_run

        def boom(self2, task_id, session, task_row, supply_rows):
            if task_id == "TF":
                raise ValueError("注入失败：引擎执行异常")
            return orig(self2, task_id, session, task_row, supply_rows)

        handler_cls._execute_run = boom
        try:
            status, _d = self.editor.post(
                "/api/tasks", {"demand": DEMAND_A, "task_id": "TF"})
            self.assertEqual(status, 201)
            status, _d = self.editor.post("/api/tasks/TF/supplies",
                                          {"supply": SUPPLIES[0]})
            self.assertEqual(status, 201)
            status, body = self.async_run("TF")
            self.assertEqual(status, 202)
            _s, job = self.wait_job(body["job_id"])
            self.assertEqual(job["status"], "failed")
            self.assertIn("注入失败", job["error"])
            self.assertIsNone(job["run_id"])
            # 失败后守卫已释放：等待 worker finally 落幕（避免竞态），再验证可再次受理
            deadline = time.time() + 5
            while self.sm._ASYNC_SLOT["active"] is not None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNone(self.sm._ASYNC_SLOT["active"])
            status, body = self.async_run()
            self.assertEqual(status, 202)
            _s, job = self.wait_job(body["job_id"])
            self.assertEqual(job["status"], "done")
        finally:
            handler_cls._execute_run = orig

    def test_07_finished_jobs_pruned(self):
        with self.sm._ASYNC_LOCK:
            now = time.time()
            for i in range(205):
                self.sm._ASYNC_JOBS[f"old{i:03d}x"] = {
                    "job_id": f"old{i:03d}x", "task_id": "T0", "status": "done",
                    "run_id": "r", "error": None, "elapsed_ms": 1.0,
                    "created_at": now - 1000 + i, "started_at": now - 1000 + i,
                    "finished_at": now - 900 + i}
        status, body = self.async_run()
        self.assertEqual(status, 202)
        self.assertLessEqual(len(self.sm._ASYNC_JOBS), self.sm._ASYNC_KEEP)
        _s, job = self.wait_job(body["job_id"])  # 本次作业未被清理
        self.assertEqual(job["status"], "done")


if __name__ == "__main__":
    unittest.main()
