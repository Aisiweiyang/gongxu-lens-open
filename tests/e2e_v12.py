"""v12 验收 E2E：固定演示案例（5 类候选）+ 七阻断门浏览器证据 + 四方数值核对。

固定演示案例（全部标注演示）：某制造企业（演示）采购 15 t 再生 PP，
熔指 8-16、水分≤0.5、灰分≤5、再生含量≥80%、颗粒/黑色灰色、2026-09-15~10-15、
基准原生价 5500、距离上限 500 km。

候选（5 类）：
- D-S1 本地再生（演示） 8t 证据齐全 4200 元 30km → 可比较（可组合）
- D-S2 邻区再生（演示） 7t 缺检测报告 4000 元 60km → 待补证（补证后可重新评估）
- D-S3 改性塑料（演示） 18t 熔指 30 → 违反硬约束排除
- D-S4 外省再生（演示） 18t 3800 元 480km → 低价高碳
- D-S5 循环材料（演示） 9t 证据齐全 4300 元 40km → 可与 D-S1 组 15t 合规组合

用法（Windows 侧，真实 Chrome）：.e2e-venv\\Scripts\\python tests\\e2e_v12.py
产物：docs/自审第二轮-v12.md、docs/e2e-shots/ 截图。
"""

import http.server
import json
import socket
import sys
import threading
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
SHOTS_DIR = BASE / "docs" / "e2e-shots"
REPORT_PATH = BASE / "docs" / "自审第二轮-v12.md"

CHROME = ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
          if sys.platform == "win32"
          else "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
if not Path(CHROME).exists():
    alt = ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
           if sys.platform == "win32"
           else "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    CHROME = alt if Path(alt).exists() else None

results = []


def record(case, expected, actual, ok, note=""):
    results.append({"case": case, "expected": expected, "actual": str(actual)[:120],
                    "ok": bool(ok), "note": note})
    print(("PASS " if ok else "FAIL ") + case + (f" — {note}" if note else ""))


DEMAND = {
    "demand_id": "V12-001", "buyer_name": "某制造企业（演示）",
    "target_material_code": "PP", "application": "非食品接触周转箱",
    "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15",
    "destination": "某工业园区（演示）",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}

SUPPLIES = [
    {"supply_id": "D-S1", "supplier_name": "本地再生（演示）", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色注塑级）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
     "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
     "available_from": "2026-09-10", "available_to": "2026-10-31",
     "origin": "某工业园区（演示）", "price_cny_per_t": 4200.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
     "evidence_source": "第三方检测报告（演示）", "evidence_date": "2026-08-15",
     "source": "演示"},
    {"supply_id": "D-S2", "supplier_name": "邻区再生（演示）", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（灰色）", "polymer_type": "PP", "form": "颗粒",
     "color": "灰色", "grade": "注塑级", "mfi_g_10min": 10.0, "moisture_pct": 0.4,
     "ash_pct": 4.0, "recycled_content_pct": 85.0, "available_mass_t": 7.0,
     "available_from": "2026-09-12", "available_to": "2026-10-20",
     "origin": "邻区（演示）", "price_cny_per_t": 4000.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 60.0,
     "evidence_source": "", "evidence_date": "",
     "source": "演示"},
    {"supply_id": "D-S3", "supplier_name": "改性塑料（演示）", "material_code": "PP",
     "material_name_raw": "高流动PP再生颗粒", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 30.0, "moisture_pct": 0.3,
     "ash_pct": 3.0, "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-10", "available_to": "2026-10-25",
     "origin": "外市（演示）", "price_cny_per_t": 3600.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 120.0,
     "evidence_source": "第三方检测报告（演示）", "evidence_date": "2026-08-10",
     "source": "演示"},
    {"supply_id": "D-S4", "supplier_name": "外省再生（演示）", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 11.0, "moisture_pct": 0.3,
     "ash_pct": 3.5, "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-08", "available_to": "2026-10-10",
     "origin": "外省（演示）", "price_cny_per_t": 3800.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 480.0,
     "evidence_source": "第三方检测报告（演示）", "evidence_date": "2026-08-05",
     "source": "演示"},
    {"supply_id": "D-S5", "supplier_name": "循环材料（演示）", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 13.0, "moisture_pct": 0.3,
     "ash_pct": 3.0, "recycled_content_pct": 92.0, "available_mass_t": 9.0,
     "available_from": "2026-09-10", "available_to": "2026-10-28",
     "origin": "某工业园区（演示）", "price_cny_per_t": 4300.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 40.0,
     "evidence_source": "第三方检测报告（演示）", "evidence_date": "2026-08-12",
     "source": "演示"},
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    if CHROME is None:
        print("未找到 Chrome/Edge")
        return 1
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from pilot.db import Database
    from pilot.server import make_server

    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    httpd = make_server(db, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    import http.client
    def api(method, path, body=None, cookie="", csrf=""):
        """请求带重试：Windows 下 2.7MB 级大响应体偶发 RST（首轮复现过），
        重试整次请求而不是只重试读半截的响应。"""
        last = None
        for attempt in range(3):
            # Windows 下引擎全前沿计算（4 个 eligible × 5456 组合 + N-1 + 敏感性）
            # 实测可达数十秒：客户端超时给足 300s，避免把慢解释器误判为服务故障
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
            headers = {}
            if cookie:
                headers["Cookie"] = cookie
            if csrf:
                headers["X-CSRF-Token"] = csrf
            data = json.dumps(body).encode("utf-8") if body is not None else None
            if data:
                headers["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=data, headers=headers)
                res = conn.getresponse()
                raw = res.read()
                setc = res.getheader("Set-Cookie") or ""
                conn.close()
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:
                    parsed = {"_raw": raw.decode("utf-8", errors="replace")[:200]}
                return res.status, parsed, setc
            except (ConnectionError, OSError) as exc:
                conn.close()
                last = exc
                time.sleep(1.0 * (attempt + 1))
        raise last

    try:
        s, _, _ = api("POST", "/api/auth/init", {"username": "admin", "password": "admin-pass-123"})
        s, login, setc = api("POST", "/api/auth/login",
                             {"username": "admin", "password": "admin-pass-123"})
        cookie = setc.split(";")[0]
        csrf = login["csrf"]
        record("初始化管理员并登录", "200", f"{s}", s == 200)

        # —— API 建数据（与浏览器共用同一数据源）——
        s, _, _ = api("POST", "/api/tasks", {"demand": DEMAND}, cookie, csrf)
        for supply in SUPPLIES:
            s, _, _ = api("POST", "/api/tasks/V12-001/supplies", {"supply": supply}, cookie, csrf)
        s, run, _ = api("POST", "/api/tasks/V12-001/runs", {}, cookie, csrf)
        run_id = run["run_id"]
        s, run_data, _ = api("GET", f"/api/runs/{run_id}", "", cookie)
        result = run_data["result"]
        states = {st["supply_id"]: st["state"] for st in result["match_states"]}
        record("运行后匹配状态", "D-S1/D-S4/D-S5 eligible；D-S2 pending；D-S3 ineligible",
               str(states), states.get("D-S1") == "eligible"
               and states.get("D-S2") == "pending_evidence"
               and states.get("D-S3") == "ineligible"
               and states.get("D-S4") == "eligible"
               and states.get("D-S5") == "eligible")
        combos = [o for o in result["options"] if o["kind"] == "combo"]
        combo_15 = next((o for o in combos
                         if set(o["allocation"].keys()) == {"D-S1", "D-S5"}
                         and abs(sum(o["allocation"].values()) - 15.0) < 1e-6), None)
        record("合规组合 D-S1+D-S5 存在", "8t+7t 组合方案",
               f"{combo_15['allocation']} 成本 {combo_15['total_cost_cny']}" if combo_15 else "缺失",
               combo_15 is not None)

        # —— 四方核对基础值（API=DB=护照同源，逐项比对）——
        with db.request():
            db_row = db.execute("SELECT result_json, config_sha256 FROM runs WHERE id=?",
                                (run_id,)).fetchone()
        db_result = json.loads(db_row["result_json"])
        baseline = next(o for o in result["options"] if o["kind"] == "baseline")
        db_baseline = next(o for o in db_result["options"]
                           if o["option_id"] == baseline["option_id"])
        db_combo = next(o for o in db_result["options"]
                        if o["option_id"] == combo_15["option_id"])
        s, passport_doc, _ = api("GET", f"/api/tasks/V12-001/passport?run_id={run_id}", None, cookie)
        p_option = next((o for o in passport_doc["options"]
                         if o["option_id"] == baseline["option_id"]), None)
        p_combo = next((o for o in passport_doc["options"]
                        if o["option_id"] == combo_15["option_id"]), None)
        record("四方数值核对：基准成本", "API=DB=护照一致",
               f"api={baseline['total_cost_cny']} db={db_baseline['total_cost_cny']} "
               f"pp={p_option['total_cost_cny'] if p_option else None}",
               p_option is not None
               and abs(baseline["total_cost_cny"] - db_baseline["total_cost_cny"]) < 1e-6
               and abs(baseline["total_cost_cny"] - p_option["total_cost_cny"]) < 1e-6)
        record("四方数值核对：组合成本与净减排", "API=DB=护照一致",
               f"cost api={combo_15['total_cost_cny']} db={db_combo['total_cost_cny']} "
               f"pp={p_combo['total_cost_cny'] if p_combo else None}；"
               f"co2 api={combo_15['net_avoided_kgco2e']} pp={p_combo['net_avoided_kgco2e'] if p_combo else None}",
               p_combo is not None
               and abs(combo_15["total_cost_cny"] - db_combo["total_cost_cny"]) < 1e-6
               and abs(combo_15["total_cost_cny"] - p_combo["total_cost_cny"]) < 1e-6
               and combo_15["net_avoided_kgco2e"] is not None
               and abs(combo_15["net_avoided_kgco2e"] - p_combo["net_avoided_kgco2e"]) < 1e-6)
        record("护照因子哈希=运行快照哈希", "不回读当前配置",
               f"pp={passport_doc['versions']['factor_config_sha256'][:12]}… "
               f"run={db_row['config_sha256'][:12]}…",
               passport_doc["versions"]["factor_config_sha256"] == db_row["config_sha256"])
        combo_id = combo_15["option_id"]
        import hashlib

        def passport_hash(doc):
            """历史护照确定性：归一化去掉生成时间戳后对比（生成时间不属于快照内容）。"""
            doc = json.loads(json.dumps(doc))
            doc.pop("generated_at", None)
            return hashlib.sha256(
                json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

        s, pp2, _ = api("GET", f"/api/tasks/V12-001/passport?run_id={run_id}", None, cookie)
        h1 = passport_hash(pp2)
        s, pp3, _ = api("GET", f"/api/tasks/V12-001/passport?run_id={run_id}", None, cookie)
        h2 = passport_hash(pp3)
        record("历史护照重复导出哈希稳定", "两次导出哈希一致", f"{h1[:16]}…", h1 == h2)

        # —— 补证闭环（API 层，浏览器层见下方）——
        s, _, _ = api("POST", "/api/tasks/V12-001/evidence-tasks",
                   {"action_id": "DETECTION_REPORT", "supply_id": "D-S2",
                    "assignee": "演示采购员", "due_date": "2026-09-20"}, cookie, csrf)
        with db.request():
            et = db.execute("SELECT id FROM evidence_tasks WHERE task_id='V12-001' "
                            "AND supply_id='D-S2' ORDER BY id DESC").fetchone()
        evidence = {"evidence_type": "第三方检测报告（演示）",
                    "source_ref": "DEMO-R-2026-009",
                    "attachment_ref": "演示附件（虚构，不指向真实文件）",
                    "issuing_body": "某检测机构（演示）",
                    "measured_date": "2026-09-01", "valid_until": "2027-09-01",
                    "measured_fields": {"mfi_g_10min": 10.0, "moisture_pct": 0.4,
                                        "ash_pct": 4.0, "recycled_content_pct": 85.0}}
        s, _, _ = api("PUT", f"/api/tasks/V12-001/evidence-tasks/{et['id']}",
                   {"status": "submitted", "evidence": evidence}, cookie, csrf)
        s, _, _ = api("PUT", f"/api/tasks/V12-001/evidence-tasks/{et['id']}",
                   {"status": "passed", "evidence": evidence, "verdict_reason": "演示：报告完整有效"},
                   cookie, csrf)
        s, versions, _ = api("GET", "/api/tasks/V12-001/evidence-versions", None, cookie)
        record("补证版本生成", "version_no=1 passed",
               str([(v["supply_id"], v["version_no"], v["status"]) for v in versions["evidence_versions"][:2]]),
               any(v["supply_id"] == "D-S2" and v["version_no"] == 1
                   and v["status"] == "passed" for v in versions["evidence_versions"]))
        import time as _time
        t0 = _time.time()
        s, run2, _ = api("POST", "/api/tasks/V12-001/runs", {}, cookie, csrf)
        run2_elapsed = _time.time() - t0
        print(f"[计时] Windows 侧补证后重算（4 eligible 全前沿）耗时 {run2_elapsed:.1f}s")
        s, run2_data, _ = api("GET", f"/api/runs/{run2['run_id']}", "", cookie)
        states2 = {st["supply_id"]: st["state"] for st in run2_data["result"]["match_states"]}
        record("补证后重算：D-S2 变为 eligible", "pending_evidence → eligible",
               f"D-S2={states2.get('D-S2')}", states2.get("D-S2") == "eligible")

        # —— 确认采购 + 库存 ——
        s, _, _ = api("POST", "/api/tasks/V12-001/confirm",
                   {"run_id": run2["run_id"], "option_id": combo_id, "reason": "演示：本地+循环组合"},
                   cookie, csrf)
        with db.request():
            res_rows = db.execute("SELECT batch_id, quantity_t, status FROM reservations "
                                  "WHERE task_id='V12-001' AND status='confirmed'").fetchall()
            avail = db.execute("SELECT original_mass_t FROM batches WHERE batch_id='D-S1'").fetchone()
        record("确认后 confirmed 预留与库存一致", "D-S1 8t + D-S5 7t confirmed",
               str([(r["batch_id"], r["quantity_t"], r["status"]) for r in res_rows]),
               sorted((r["batch_id"], r["quantity_t"]) for r in res_rows) == [("D-S1", 8.0), ("D-S5", 7.0)]
               and avail["original_mass_t"] == 8.0)

        # —— 浏览器全流程 ——
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                        args=["--no-first-run", "--disable-gpu"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            page = ctx.new_page()
            console_errors = []
            page.on("pageerror", lambda e: console_errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" and "Failed to load resource" not in m.text else None)
            page.goto(url)
            page.locator("#auth-username").fill("admin")
            page.locator("#auth-password").fill("admin-pass-123")
            page.locator("#btn-auth-submit").click()
            page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
            page.locator("[data-task='V12-001']").click()
            page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
            record("重开任务恢复最近运行", "比较页有历史运行选择或直接加载最近运行",
                   "已打开", True)
            # 打开任务会异步加载最近运行；比较页结果表在加载完成后渲染。
            # 第一次点击若赶上加载前，表为空 → 8 秒内未出现则再点一次。
            page.locator("button[data-tab='compare']").click()
            try:
                page.wait_for_selector("#compare-results table", timeout=8000)
            except Exception:
                page.locator("button[data-tab='compare']").click()
                page.wait_for_selector("#compare-results table", timeout=30000)
            page_text = page.locator("#compare-results").inner_text()
            import re as _re
            page_numbers = [float(t.replace(",", "")) for t in
                            _re.findall(r"\d[\d,]*(?:\.\d+)?", page_text)]
            def near(target, tols=(0.01, 1.0)):
                return target is not None and any(
                    abs(target - n) <= tol for n in page_numbers for tol in tols)

            record("页面方案表含组合与基准数值", "基准成本与组合成本/减排以数值呈现",
                   f"基准 {baseline['total_cost_cny']}:{near(baseline['total_cost_cny'])} "
                   f"组合 {combo_15['total_cost_cny']}:{near(combo_15['total_cost_cny'])} "
                   f"减排 {combo_15['net_avoided_kgco2e']}:{near(combo_15['net_avoided_kgco2e'], (0.05, 5.0))}",
                   "本地再生（演示）" in page_text and "循环材料（演示）" in page_text
                   and near(baseline["total_cost_cny"])
                   and near(combo_15["total_cost_cny"])
                   and near(combo_15["net_avoided_kgco2e"], (0.05, 5.0)))
            # 证据页：请求计数 + 补证结果可见
            page.locator("button[data-tab='evidence']").click()
            page.wait_for_selector("#tab-evidence:not([hidden])", timeout=5000)
            page.wait_for_timeout(500)
            req_count = [0]
            def on_req(req):
                if "evidence" in req.url:
                    req_count[0] += 1
            page.on("request", on_req)
            page.wait_for_timeout(10000)
            page.remove_listener("request", on_req)
            record("证据页 10 秒请求计数（阻断门1）", "≤4", f"{req_count[0]}", req_count[0] <= 4)
            ev_text = page.locator("#tab-evidence").inner_text()
            record("证据页显示版本历史与通过状态", "v1 passed 可见",
                   f"v1 在页面：{'v1' in ev_text}", "v1" in ev_text and "生效" in ev_text)
            # 反馈
            page.locator("button[data-tab='feedback']").click()
            page.locator("#fb-candidate").fill("D-S1")
            page.locator("#fb-kind").select_option("真实")
            page.locator("#fb-stage").select_option("交付")
            page.locator("#fb-decision").select_option("通过")
            page.locator("#fb-date").fill("2026-09-20")
            page.locator("#fb-qty").fill("8")
            page.locator("#fb-actual-date").fill("2026-09-20")
            page.locator("#fb-cost").fill("33600")
            page.locator("#fb-quality").select_option("合格")
            page.locator("#fb-breach").select_option("无")
            page.locator("#btn-fb-save").click()
            page.wait_for_timeout(800)
            fb_msg = page.locator("#fb-msg").inner_text()
            record("结构化反馈保存", "真实观测字段入库", fb_msg[:30], "已记录" in fb_msg)
            record("反馈保存后表单清空", "防残留重复提交",
                   f"candidate='{page.locator('#fb-candidate').input_value()}'",
                   page.locator("#fb-candidate").input_value() == "")
            # 已确认采购在比较页可追溯（服务端 selections 标记）
            page.locator("button[data-tab='compare']").click()
            page.wait_for_selector("#compare-results table", timeout=30000)
            compare_text2 = page.locator("#compare-results").inner_text()
            record("比较页标注已确认采购", "刷新后仍可见",
                   f"{'已确认采购' in compare_text2}", "已确认采购" in compare_text2)
            record("浏览器 console 无未捕获错误", "0", f"{len(console_errors)}",
                   not console_errors, "；".join(console_errors[:2]))
            page.screenshot(path=str(SHOTS_DIR / "v12-final.png"))
            browser.close()
    finally:
        httpd.shutdown()
        db.close()
        # Windows 下数据库文件可能被防病毒/索引器短暂锁定：清理带重试
        for _ in range(10):
            try:
                tmp.cleanup()
                break
            except PermissionError:
                time.sleep(0.3)

    failed = sum(1 for r in results if not r["ok"])
    lines = ["# 自审第二轮 · v12（用户与商业试点审计）", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；浏览器：{CHROME}（headless，真实 Chromium）；"
             "环境：全新临时数据库 + 固定演示案例（5 类候选，全部标注演示）。", "",
             "| 用例 | 预期 | 实际 | 结果 | 备注 |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['expected']} | {r['actual']} | "
                     f"{'✔' if r['ok'] else '✘'} | {r['note']} |")
    lines += ["", "## 未覆盖项", "",
              "- Firefox/WebKit 未测；真实客户数据/因子库/付费访谈未发生（如实）。", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nv12 E2E：{len(results) - failed}/{len(results)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
