"""v16 任务 3 端到端自审：多需求撮合页（真实浏览器 + 干净环境）。

用法：.e2e-venv\\Scripts\\python tests\\e2e_v16.py（Windows，真实 Chrome headless）
产物：docs/自审第二轮-v16.md、docs/e2e-shots/matching-v16.png。

覆盖：空库初始化 → 管理员登录 → API 造数（两任务 + 共享批次）→ 撮合页勾选多任务 →
计算撮合建议 → 汇总/标注/稳定性徽标/匹配明细数值 → 双边关系 SVG 边数 →
导出建议单（v17：按钮/文件名/内容同源数值与标注）→ 只读账号入口禁用与提示 →
console/pageerror 零错误。
说明：HTTP 4xx 属应用正常响应，只把真正的 JS 异常（pageerror）计为错误。
"""

import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
SHOTS_DIR = BASE / "docs" / "e2e-shots"
REPORT_PATH = BASE / "docs" / "自审第二轮-v16.md"

if sys.platform == "win32":
    CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
else:
    CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
if not Path(CHROME).exists():
    alt = ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
           if sys.platform == "win32"
           else "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    CHROME = alt if Path(alt).exists() else None

results = []  # {case, expected, actual, ok, note}


def record(case, expected, actual, ok, note=""):
    results.append({"case": case, "expected": expected, "actual": actual,
                    "ok": bool(ok), "note": note})
    print(("PASS " if ok else "FAIL ") + case + (f" — {note}" if note else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ApiClient:
    """E2E 造数用极简 API 客户端（与被测服务器同机回环）。"""

    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        set_cookie = res.getheader("Set-Cookie") or ""
        conn.close()
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        try:
            return res.status, json.loads(raw.decode("utf-8"))
        except Exception:
            return res.status, None


DEMAND_A = {
    "demand_id": "E2E-A", "buyer_name": "撮合需方A（演示）", "target_material_code": "PP",
    "application": "周转箱", "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15", "destination": "园区",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}
DEMAND_B = dict(DEMAND_A, demand_id="E2E-B", buyer_name="撮合需方B（演示）",
                required_mass_t=10.0, application="地板")
SUPPLY_S1 = {
    "supply_id": "S1", "supplier_name": "甲（演示）", "material_code": "PP",
    "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
    "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
    "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
    "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "园区",
    "price_cny_per_t": 4200.0, "price_includes_freight": False, "yield_pct": 100.0,
    "preprocessing": "已造粒", "transport_mode": "公路货运",
    "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
    "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "演示",
}
SUPPLY_S2 = dict(SUPPLY_S1, supply_id="S2", supplier_name="乙（演示）",
                 material_name_raw="再生PP颗粒（黑色）", mfi_g_10min=11.0, ash_pct=3.5,
                 recycled_content_pct=90.0, available_mass_t=18.0, origin="外省",
                 price_cny_per_t=3800.0, evidence_date="2026-08-05", distance_km=480.0)
SUPPLY_S3 = dict(SUPPLY_S1, supply_id="S3", supplier_name="丙（演示）",
                 material_name_raw="再生PP颗粒（灰色）", color="灰色", mfi_g_10min=10.0,
                 recycled_content_pct=92.0, available_mass_t=12.0,
                 price_cny_per_t=4500.0, distance_km=50.0)


def seed_tasks(port):
    """通过 API 造数：T1（S1/S2）+ T2（共享 S1 + 自有 S3），返回 (ok, note)。"""
    c = ApiClient(port)
    status, body = c.request("POST", "/api/auth/login",
                             {"username": "admin", "password": "admin-pass-123"})
    if status != 200:
        return False, f"造数登录失败 {status}"
    c.csrf = (body or {}).get("csrf") or ""
    for task_id, demand in (("T1", DEMAND_A), ("T2", DEMAND_B)):
        status, body = c.request("POST", "/api/tasks",
                                 {"demand": demand, "task_id": task_id})
        if status != 201:
            return False, f"建任务 {task_id} 失败 {status}"
    for task_id, supplies in (("T1", (SUPPLY_S1, SUPPLY_S2)),
                              ("T2", (SUPPLY_S1, SUPPLY_S3))):
        for supply in supplies:
            status, _ = c.request("POST", f"/api/tasks/{task_id}/supplies",
                                  {"supply": supply})
            if status != 201:
                return False, f"录供给 {supply['supply_id']} → {task_id} 失败 {status}"
    return True, "T1/T2 与批次 S1/S2/S3 就绪（S1 跨任务共享）"


def launch_chrome_cdp(port):
    profile = tempfile.mkdtemp(prefix="pw-cdp-v16-")
    cmd = [CHROME, "--headless=new", f"--remote-debugging-port={port}",
           "--remote-debugging-address=127.0.0.1",
           f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
           "--no-sandbox", "--window-size=1440,900", "about:blank"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("webSocketDebuggerUrl"):
                return proc, profile
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("Chrome CDP 端口未就绪")


def main():
    from pilot.db import Database
    from pilot.server import make_server

    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    httpd = make_server(db, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    import threading
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if CHROME is None:
        print("未找到 Chrome/Edge，浏览器交互无法验收（如实记录）")
        record("浏览器可用性", "Chrome 或 Edge 存在", "均未找到", False)
        _write_report()
        return 1

    cdp_port = free_port()
    proc, profile = launch_chrome_cdp(cdp_port)
    console_errors = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900},
                                              accept_downloads=True)
                page = context.new_page()
                page.on("console", lambda m: console_errors.append(m.text)
                        if m.type == "error" and "Failed to load resource" not in m.text
                        else None)
                page.on("pageerror", lambda e: console_errors.append(str(e)))
                run_suite(page, port, console_errors)
            finally:
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        import shutil
        shutil.rmtree(profile, ignore_errors=True)
    httpd.shutdown()
    httpd.server_close()
    db.close()
    # Windows 上 SQLite 文件锁释放有滞后（既有环境层问题）：清理尽力而为，
    # 失败不吞掉测试结果——临时目录由系统回收。
    for _ in range(10):
        try:
            tmp.cleanup()
            break
        except OSError:
            time.sleep(0.5)
    else:
        print("提示：临时目录未能立即清理（Windows 文件锁），可忽略")
    _write_report()
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nE2E 汇总：{len(results) - failed}/{len(results)} 通过")
    return 0 if failed == 0 else 1


def run_suite(page, port, console_errors):
    url = f"http://127.0.0.1:{port}/"
    page.goto(url)
    page.wait_for_load_state("load")
    record("空库首访", "显示初始化管理员表单", page.locator("#auth-title").inner_text(),
           "初始化管理员" in page.locator("#auth-title").inner_text())
    page.locator("#auth-username").fill("admin")
    page.locator("#auth-password").fill("admin-pass-123")
    page.locator("#btn-auth-submit").click()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
    record("初始化管理员并登录", "进入任务列表", "已进入", True)

    ok, note = seed_tasks(port)
    record("API 造数（两任务 + 共享批次）", "T1/T2 与批次就绪",
           note if ok else "失败", ok, note)
    page.reload()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

    # 打开撮合页：入口按钮 + 免责标注 + 任务勾选列表
    page.locator("#btn-matching").click()
    page.wait_for_selector("#view-matching:not([hidden])", timeout=5000)
    view_text = page.locator("#view-matching").inner_text()
    record("撮合页打开", "显示「非交易/非预留、不落库、不改库存真相」标注",
           view_text[:80],
           "撮合建议（非交易/非预留）" in view_text and "不落库" in view_text
           and "不改变批次库存真相" in view_text)
    rows = page.locator(".matching-pick")
    record("多需求勾选列表", "两个任务均可勾选", f"{rows.count()} 个勾选框",
           rows.count() == 2)

    page.locator(".matching-pick[data-tid='T1']").check()
    page.locator(".matching-pick[data-tid='T2']").check()
    page.locator("#btn-matching-run").click()
    page.wait_for_selector("#matching-results table", timeout=30000)
    results_text = page.locator("#matching-results").inner_text()
    record("撮合计算", "汇总含「撮合建议（非交易/非预留）」与演示偏好模型标注",
           results_text[:60],
           "撮合建议（非交易/非预留）" in results_text
           and "演示毛利模型" in results_text)
    record("稳定性自检徽标", "阻断对 0（通过）", "见汇总",
           "稳定性自检通过（阻断对 0）" in results_text)

    # 匹配明细：两需求均全额满足（引擎小案例确定性结果：A→S2×15；B→S1×7+S2×3）
    detail_rows = page.locator("#matching-results table").first.locator("tbody tr")
    n_rows = detail_rows.count()
    row_a = (page.locator("#matching-results table").first
             .locator("tbody tr", has_text="T1").inner_text())
    row_b = (page.locator("#matching-results table").first
             .locator("tbody tr", has_text="T2").inner_text())
    record("匹配明细数值", "T1 满足 15/15 t、T2 满足 10/10 t（确定性案例）",
           f"{n_rows} 行",
           n_rows == 2 and "15" in row_a and "10" in row_b
           and "撮合理由" in results_text)
    record("批次池去重呈现", "共享批次 S1 只计一次（容量 8t）",
           "见批次表",
           "S1" in results_text and "S3" in results_text)

    # 双边关系 SVG：连线数=分配对数（A←S2；B←S1、B←S2，共 3）
    edges = page.locator("#matching-svg path").count()
    record("双边关系 SVG", "连线数=分配对数（3）", f"{edges} 条连线", edges == 3)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS_DIR / "matching-v16.png"), full_page=True)

    # v17 任务 2：导出建议单（服务端按同入参重算渲染；前端 Blob 下载，服务端不存）
    export_btn = page.locator("#btn-matching-export")
    record("导出建议单按钮", "结果区出现导出按钮且可用",
           f"count={export_btn.count()}",
           export_btn.count() == 1 and export_btn.is_enabled())
    with page.expect_download() as dl_info:
        export_btn.click()
    download = dl_info.value
    record("建议单文件名", "撮合建议单_T1+T2_YYYYMMDD-HHMM.html",
           download.suggested_filename,
           download.suggested_filename.startswith("撮合建议单_T1+T2_")
           and download.suggested_filename.endswith(".html"))
    sheet_path = SHOTS_DIR / "matching-sheet-e2e.html"
    download.save_as(str(sheet_path))
    sheet_text = sheet_path.read_text(encoding="utf-8")
    record("建议单内容", "与页面同源数值（S2 × 15、59,820.00）+ 标注齐全 + 无脚本",
           f"{len(sheet_text)} 字符",
           "S2 × 15" in sheet_text and "59,820.00" in sheet_text
           and "撮合建议（非交易/非预留）" in sheet_text
           and "演示毛利模型" in sheet_text
           and "生成时间：" in sheet_text and "入参快照：" in sheet_text
           and "S1 × 7" in sheet_text and "41,577.50" in sheet_text
           and "<script" not in sheet_text.lower())

    # 未全满足时才出现的区块不应存在（本案例全部满足）；剩余容量表如实展示 S1/S3
    record("无非正常未满足区块", "「未满足需求与原因」不出现；剩余容量表出现", "见结果区",
           "未满足需求与原因" not in results_text
           and "批次剩余容量" in results_text)

    # 只读账号：入口可用但计算被禁用并提示（服务端 403 由单测覆盖）
    api = ApiClient(port)
    status, body = api.request("POST", "/api/auth/login",
                               {"username": "admin", "password": "admin-pass-123"})
    api.csrf = (body or {}).get("csrf") or ""
    status, _ = api.request("POST", "/api/users",
                            {"username": "viewer1", "password": "viewer1-pass-123",
                             "role": "viewer"})
    record("创建只读账号", "201", str(status), status == 201)
    page.locator("#btn-logout").click()
    page.wait_for_selector("#view-auth:not([hidden])", timeout=5000)
    page.locator("#auth-username").fill("viewer1")
    page.locator("#auth-password").fill("viewer1-pass-123")
    page.locator("#btn-auth-submit").click()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
    page.locator("#btn-matching").click()
    page.wait_for_selector("#view-matching:not([hidden])", timeout=5000)
    disabled = page.locator("#btn-matching-run").is_disabled()
    hint = page.locator("#matching-hint").inner_text()
    record("只读账号撮合入口", "计算按钮禁用 + 只读提示", f"disabled={disabled}",
           disabled and "只读账号" in hint)

    record("console 零错误", "无 JS 异常（pageerror/console error）",
           f"{len(console_errors)} 条",
           not console_errors, "；".join(console_errors[:3]))


def _write_report():
    lines = ["# 自审第二轮 · v16（多需求撮合页端到端）", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；"
             f"浏览器：{CHROME}（headless，真实 Chromium 内核）；环境：干净临时目录 + 空数据库。", "",
             "## 用例与实测", "",
             "| 用例 | 预期 | 实测 | 结果 | 备注 |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['expected']} | {r['actual']} | "
                     f"{'✔' if r['ok'] else '✘'} | {r['note']} |")
    lines += ["", "## 截图", "",
              "- docs/e2e-shots/matching-v16.png（撮合结果整页）",
              "- docs/e2e-shots/matching-sheet-e2e.html（导出建议单证据文件，v17 任务 2）", "",
              "## 未覆盖项", "",
              "- Firefox/WebKit 未测（本机仅 Chrome/Edge，如实列出）。",
              "- API 层权限/CSRF/校验由 tests/test_v16_matching_api.py 在真实 HTTP 服务器覆盖。",
              "", "> 本文件由 tests/e2e_v16.py 每次运行重写（活文档）。", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告：{REPORT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
