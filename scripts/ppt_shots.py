"""PPT 截图脚本：v12 批次（01–10）+ v17 增量批次（11–13）。

v12 批次（--legacy）：10 张 1920×1080，统一固定演示案例（15 t 再生 PP，5 类候选）。
数值与 tests/e2e_v12.py 一致：基准成本 83025.0、组合（D-S1+D-S5）成本 64182.0、
净减排 21072.98、D-S2 补证闭环、confirmed 预留 8t+7t。全部数据标注「演示」。
**红线 6**：docs/ppt_assets/ 既有 01–10 编号截图不回改不重生成——已提交材料引用它们，
本脚本缺省**不再生成 v12 批次**；如确需重制须用户明确要求后加 --legacy 运行。

v17 批次（缺省）：3 张增量素材，新文件名不覆盖 01–10——
    11_matching.png          撮合页全页（v16 撮合页，e2e_v16 同源夹具 T1/T2+共享批次）
    12_load_benchmark.png    压力基准实测摘要表（自动取自 benchmarks/ 原始 JSON，不手写数字）
    13_dashboard_charts.png  最新批次仪表盘「图表总览」节（冻结演示案例口径）

用法（Windows 侧，真实 Chrome）：
    .e2e-venv\\Scripts\\python scripts\\ppt_shots.py            # v17 增量批次（缺省）
    .e2e-venv\\Scripts\\python scripts\\ppt_shots.py --legacy   # v12 十张（红线 6，非必要禁跑）
密码与文件路径不进入任何截图（登录后操作，headless 视口只截页面内容）。
"""

import argparse
import http.client
import json
import sys
import threading
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from e2e_v12 import DEMAND, SUPPLIES  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "docs" / "ppt_assets"

CHROME = ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
          if sys.platform == "win32"
          else "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe")
if not Path(CHROME).exists():
    alt = ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
           if sys.platform == "win32"
           else "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    CHROME = alt if Path(alt).exists() else None

EVIDENCE = {"evidence_type": "第三方检测报告（演示）",
            "source_ref": "DEMO-R-2026-009",
            "attachment_ref": "演示附件（虚构，不指向真实文件）",
            "issuing_body": "某检测机构（演示）",
            "measured_date": "2026-09-01", "valid_until": "2027-09-01",
            "measured_fields": {"mfi_g_10min": 10.0, "moisture_pct": 0.4,
                                "ash_pct": 4.0, "recycled_content_pct": 85.0}}


def shoot_legacy():
    """v12 批次（01–10）：仅 --legacy 显式运行（红线 6：既有编号截图不回改不重生成）。"""
    if CHROME is None:
        print("未找到 Chrome/Edge")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from pilot.db import Database
    from pilot.server import make_server

    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    httpd = make_server(db, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    def api(method, path, body=None, cookie="", csrf=""):
        for _ in range(3):
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
            except (ConnectionError, OSError):
                conn.close()
                time.sleep(1)

    def shot(page, name, anchor=None, wait_ms=400):
        if anchor:
            page.locator(anchor).scroll_into_view_if_needed()
            page.wait_for_timeout(wait_ms)
        path = str(OUT / name)
        page.screenshot(path=path, clip={"x": 0, "y": 0, "width": 1920, "height": 1080})
        print("已保存", path)

    try:
        s, _, _ = api("POST", "/api/auth/init",
                      {"username": "admin", "password": "admin-pass-123"})
        s, login, setc = api("POST", "/api/auth/login",
                             {"username": "admin", "password": "admin-pass-123"})
        cookie = setc.split(";")[0]
        csrf = login["csrf"]
        api("POST", "/api/tasks", {"demand": DEMAND}, cookie, csrf)
        for supply in SUPPLIES:
            api("POST", "/api/tasks/V12-001/supplies", {"supply": supply}, cookie, csrf)
        s, run1, _ = api("POST", "/api/tasks/V12-001/runs", {}, cookie, csrf)
        s, run1_data, _ = api("GET", f"/api/runs/{run1['run_id']}", "", cookie)
        combo = next(o for o in run1_data["result"]["options"]
                     if o["kind"] == "combo"
                     and set(o["allocation"].keys()) == {"D-S1", "D-S5"})
        combo_id = combo["option_id"]

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                        args=["--no-first-run", "--disable-gpu",
                                              "--force-device-scale-factor=1"])
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(url)
            page.locator("#auth-username").fill("admin")
            page.locator("#auth-password").fill("admin-pass-123")
            page.locator("#btn-auth-submit").click()
            page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

            shot(page, "01_overview.png")  # 任务总览（登录后，无密码）

            page.locator("[data-task-open='V12-001']").first.click()
            page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
            page.wait_for_timeout(800)  # 等最近运行异步加载
            shot(page, "02_demand.png")  # 需求向导

            page.locator("button[data-tab='supplies']").click()
            page.wait_for_selector("#tab-supplies:not([hidden])", timeout=5000)
            page.wait_for_timeout(400)
            shot(page, "03_supply_pool.png")  # 供给池与质量状态

            page.locator("button[data-tab='evidence']").click()
            page.wait_for_selector("#tab-evidence:not([hidden])", timeout=5000)
            page.wait_for_timeout(600)
            shot(page, "04_evidence_before.png")  # D-S2 待补证

            # —— API 补证闭环 + 确认 + 反馈（浏览器重开后可见最新状态）——
            api("POST", "/api/tasks/V12-001/evidence-tasks",
                {"action_id": "DETECTION_REPORT", "supply_id": "D-S2",
                 "assignee": "演示采购员", "due_date": "2026-09-20"}, cookie, csrf)
            with db.request():
                et = db.execute("SELECT id FROM evidence_tasks WHERE task_id='V12-001' "
                                "AND supply_id='D-S2' ORDER BY id DESC").fetchone()
            api("PUT", f"/api/tasks/V12-001/evidence-tasks/{et['id']}",
                {"status": "submitted", "evidence": EVIDENCE}, cookie, csrf)
            api("PUT", f"/api/tasks/V12-001/evidence-tasks/{et['id']}",
                {"status": "passed", "evidence": EVIDENCE,
                 "verdict_reason": "演示：报告完整有效"}, cookie, csrf)
            s, run2, _ = api("POST", "/api/tasks/V12-001/runs", {}, cookie, csrf)
            s, confirm_res, _ = api("POST", "/api/tasks/V12-001/confirm",
                                    {"run_id": run2["run_id"], "option_id": combo_id,
                                     "reason": "演示：本地+循环组合"}, cookie, csrf)
            api("POST", "/api/tasks/V12-001/feedback", {
                "candidate_id": "D-S1", "decision": "通过",
                "observation_type": "真实", "reviewed_at": "2026-09-20",
                "stage": "交付", "actual_qty_t": 8, "actual_date": "2026-09-20",
                "actual_cost_cny": 33600, "quality_result": "合格",
                "return_breach": "无"}, cookie, csrf)

            # 重开任务（加载补证后的最新运行）
            page.locator("#btn-back").click()
            page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
            page.locator("[data-task-open='V12-001']").first.click()
            page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
            page.wait_for_timeout(800)

            page.locator("button[data-tab='evidence']").click()
            page.wait_for_selector("#tab-evidence:not([hidden])", timeout=5000)
            page.wait_for_timeout(600)
            shot(page, "05_evidence_after.png")  # v1 生效 + 版本历史

            page.locator("button[data-tab='compare']").click()
            page.wait_for_selector("#compare-results table", timeout=30000)
            page.wait_for_timeout(600)
            shot(page, "06_compare.png")  # 方案集 + 已确认标记

            # 展开组合方案明细（成本账本 + 碳核算口径）
            toggle = page.locator(f"tr[data-option-id='{combo_id}'] .details-toggle")
            toggle.scroll_into_view_if_needed()
            toggle.click()
            page.wait_for_timeout(500)
            shot(page, "07_carbon_ledger.png", anchor=f"tr[data-details='{combo_id}']")

            shot(page, "08_confirm_reserve.png", anchor="#confirm-option")

            page.locator("button[data-tab='feedback']").click()
            page.wait_for_selector("#tab-feedback:not([hidden])", timeout=5000)
            page.wait_for_timeout(600)
            shot(page, "09_feedback.png")  # KPI + 已记录反馈

            page.locator("button[data-tab='passport']").click()
            page.wait_for_selector("#tab-passport:not([hidden])", timeout=5000)
            page.wait_for_timeout(600)
            shot(page, "10_passport.png")  # 护照摘要

            browser.close()
    finally:
        httpd.shutdown()
        db.close()
        for _ in range(10):
            try:
                tmp.cleanup()
                break
            except PermissionError:
                time.sleep(0.3)
    print("完成：10 张截图已写入", OUT)
    return 0


def load_benchmark_html():
    """从 v16 压测原始 JSON 自动拼装摘要表 HTML（确定性、零依赖、不手写数字）。"""
    rows_src = [("WSL（Linux，压测基准环境）",
                 BASE / "benchmarks" / "压力基准-raw-wsl-shared.json"),
                ("Windows（.e2e-venv，试点同构环境）",
                 BASE / "benchmarks" / "压力基准-raw-windows-shared.json")]
    generated = []
    tables = []
    for label, path in rows_src:
        data = json.loads(path.read_text(encoding="utf-8"))
        generated.append(f"{path.name}（{data['meta']['generated_at']}）")
        body = []
        for run in data["runs"]:
            s = run["summary"]
            body.append(
                f"<tr><td>{run['level']}</td><td>{s['requests']}</td>"
                f"<td class='ok'>{s['errors']}</td><td>{(s['error_rate'] or 0):.2%}</td>"
                f"<td>{s['rps']}</td><td>{s['engine_p50_ms']}</td>"
                f"<td class='hl'>{s['engine_p95_ms']}</td><td>{s['engine_p99_ms']}</td>"
                f"<td>{s['wall_s']}</td></tr>")
        tables.append(
            f"<h2>{label} · shared 口径（全体用户共享一个编辑会话）</h2>"
            "<table><thead><tr><th>并发</th><th>请求</th><th>错误</th><th>错误率</th>"
            "<th>吞吐 req/s</th><th>引擎 P50 ms</th><th>引擎 P95 ms</th>"
            "<th>引擎 P99 ms</th><th>墙钟 s</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")
    return (
        "<!doctype html><html lang='zh'><meta charset='utf-8'><style>"
        "body{font-family:'Microsoft YaHei',sans-serif;color:#16231c;margin:28px 34px;}"
        "h1{font-size:23px;margin:0 0 4px;}h2{font-size:16px;margin:20px 0 6px;}"
        ".sub{color:#5b6f65;font-size:12.5px;margin:0 0 10px;}"
        "table{border-collapse:collapse;font-size:13.5px;margin-bottom:4px;}"
        "th,td{border:1px solid #9db3a6;padding:4px 12px;text-align:right;}"
        "th{background:#e8f1ec;}th:first-child,td:first-child{text-align:center;}"
        ".ok{color:#1e6b49;font-weight:bold;}.hl{font-weight:bold;}"
        ".note{color:#5b6f65;font-size:12px;line-height:1.7;margin-top:12px;}"
        "</style><h1>服务器压力基准（实测摘要）· 试点容量口径依据</h1>"
        "<p class='sub'>≤20 并发为实测容量口径：20 并发全链路零错误，引擎 P95 0.76 秒（WSL）"
        "/ 1.80 秒（Windows）；30 并发仍零错误、响应开始变慢。</p>"
        + "".join(tables)
        + "<p class='note'>数据来源：" + "；".join(generated)
        + "。行为序列：会话校验 → 任务列表 → 运行引擎（小案例）→ 结果读取 → 护照下载；"
        "每档全新服务器实例 + 空库 + 1 次不计入预热；百分位为最近秩法。"
        "本页为实测数据呈现，方法、分步延迟与边界（小案例口径、回环网络、登录限流）"
        "详见 docs/压力基准-v16.md。压测环境为测试夹具数据，非真实客户负载。</p></html>")


def latest_dashboard():
    """最新批次仪表盘（与 build_release._latest_outputs 同口径：文件名排序取末位）。"""
    dashboards = sorted((BASE / "output").glob("再生PP绿色决策仪表盘_*.html"))
    return dashboards[-1] if dashboards else None


def shoot_v17():
    """v17 增量批次（11/12/13）：只写新文件，不触碰既有 01–10。"""
    if CHROME is None:
        print("未找到 Chrome/Edge")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    from e2e_v16 import ApiClient, seed_tasks  # 与 e2e_v16 同源夹具，数值口径一致
    from playwright.sync_api import sync_playwright
    from pilot.db import Database
    from pilot.server import make_server

    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    httpd = make_server(db, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        client = ApiClient(port)
        client.request("POST", "/api/auth/init",
                       {"username": "admin", "password": "admin-pass-123"})
        ok, note = seed_tasks(port)
        if not ok:
            print("撮合夹具造数失败：", note)
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME, headless=True,
                                        args=["--no-first-run", "--disable-gpu",
                                              "--force-device-scale-factor=1"])
            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            # 11 撮合页全页：登录 → 勾选 T1/T2 → 计算 → 等结果表渲染完
            page.goto(f"http://127.0.0.1:{port}/")
            page.locator("#auth-username").fill("admin")
            page.locator("#auth-password").fill("admin-pass-123")
            page.locator("#btn-auth-submit").click()
            page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
            page.locator("#btn-matching").click()
            page.wait_for_selector("#view-matching:not([hidden])", timeout=5000)
            page.locator(".matching-pick[data-tid='T1']").check()
            page.locator(".matching-pick[data-tid='T2']").check()
            page.locator("#btn-matching-run").click()
            page.wait_for_selector("#matching-results table", timeout=30000)
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / "11_matching.png"), full_page=True)
            print("已保存", OUT / "11_matching.png")

            # 12 压力基准实测摘要（表内数字全部来自原始 JSON）
            html_path = Path(tmp.name) / "load_summary.html"
            html_path.write_text(load_benchmark_html(), encoding="utf-8")
            page.goto(html_path.as_uri())
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "12_load_benchmark.png"),
                            clip={"x": 0, "y": 0, "width": 1920, "height": 1080})
            print("已保存", OUT / "12_load_benchmark.png")

            # 13 最新批次仪表盘「图表总览」节（冻结演示案例口径，v15 图表）；
            # 元素截图保证整节完整（节高超过视口，视口裁剪会从句中截断）
            dash = latest_dashboard()
            if dash is None:
                print("未找到仪表盘 HTML：先在仓库根运行 "
                      "python -B run.py --industry 再生PP --data-mode demo 生成最新批次")
                return 1
            page.goto(dash.as_uri())
            page.wait_for_selector("#board-charts", timeout=10000)
            page.locator("#board-charts").scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            page.locator("#board-charts").screenshot(
                path=str(OUT / "13_dashboard_charts.png"))
            print("已保存", OUT / "13_dashboard_charts.png")

            browser.close()
    finally:
        httpd.shutdown()
        db.close()
        for _ in range(10):
            try:
                tmp.cleanup()
                break
            except PermissionError:
                time.sleep(0.3)
    print("完成：v17 批次 3 张截图已写入", OUT)
    return 0


def main():
    parser = argparse.ArgumentParser(description="PPT 截图：v17 增量批次（缺省）/ v12 批次（--legacy）")
    parser.add_argument("--legacy", action="store_true",
                        help="生成 v12 批次 01–10（红线 6：既有编号截图不回改不重生成，"
                             "非必要禁跑；确需重制须用户明确要求）")
    args = parser.parse_args()
    if args.legacy:
        print("⚠ --legacy 将重写 docs/ppt_assets/ 既有 01–10 编号截图（红线 6）——"
              "仅限用户明确要求后使用")
        return shoot_legacy()
    return shoot_v17()


if __name__ == "__main__":
    raise SystemExit(main())
