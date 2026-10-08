"""v18 任务 2 端到端自审：工作台容错性（真实浏览器 + 干净环境）。

用法：.e2e-venv\\Scripts\\python tests\\e2e_v18.py（Windows，真实 Chrome headless）
产物：docs/自审第二轮-v18.md、docs/e2e-shots/v18-fault-tolerance.png。

覆盖：空库初始化 → 登录 → API 造数 → ①供给删除二次确认（取消不删/确认删除）→
②服务端会话失效后前端统一回登录页并提示 → ③撮合计算防重复提交（请求在途时按钮
禁用）→ ④网络故障友好提示（请求被拦截中断时显示可行动的错误说明）→
⑤大案例异步运行（≥16 供给自动 async=1 + 作业轮询至完成，v18 任务 3）→
console/pageerror 零错误。
说明：HTTP 4xx 属应用正常响应，只把真正的 JS 异常（pageerror）计为错误；
请求挂起/中断用 Playwright route 模拟，不依赖真实网络故障。
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

from e2e_v16 import (ApiClient, DEMAND_A, SUPPLY_S3, seed_tasks)  # 同源夹具（stdlib-only）

BASE = Path(__file__).resolve().parent.parent
SHOTS_DIR = BASE / "docs" / "e2e-shots"
REPORT_PATH = BASE / "docs" / "自审第二轮-v18.md"

if sys.platform == "win32":
    CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
else:
    CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
if not Path(CHROME).exists():
    alt = ("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
           if sys.platform == "win32"
           else "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    CHROME = alt if Path(alt).exists() else None

results = []


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


def launch_chrome_cdp(port):
    profile = tempfile.mkdtemp(prefix="pw-cdp-v18-")
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
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                page.on("console", lambda m: console_errors.append(m.text)
                        if m.type == "error" and "Failed to load resource" not in m.text
                        else None)
                page.on("pageerror", lambda e: console_errors.append(str(e)))
                run_suite(page, db, port, console_errors)
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


def run_suite(page, db, port, console_errors):
    url = f"http://127.0.0.1:{port}/"
    page.goto(url)
    page.wait_for_load_state("load")
    page.locator("#auth-username").fill("admin")
    page.locator("#auth-password").fill("admin-pass-123")
    page.locator("#btn-auth-submit").click()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

    ok, note = seed_tasks(port)
    record("API 造数", "T1/T2 与批次就绪", note if ok else "失败", ok, note)
    page.reload()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

    # —— ① 供给删除二次确认：取消（dismiss）不删 ——
    page.locator("[data-task-open='T1']").first.click()
    page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
    page.wait_for_timeout(800)
    page.locator("button[data-tab='supplies']").click()
    page.wait_for_selector("#tab-supplies:not([hidden])", timeout=5000)
    before = page.locator("#tab-supplies tbody tr").count()
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.locator("[data-del-supply]").first.click()
    page.wait_for_timeout(400)
    after_cancel = page.locator("#tab-supplies tbody tr").count()
    record("删除供给二次确认（取消）", "弹确认框且取消后批次仍在",
           f"dialog={len(dialogs)} 行数 {before}→{after_cancel}",
           len(dialogs) == 1 and after_cancel == before,
           dialogs[0][:40] if dialogs else "")
    # 确认（accept）→ 批次删除
    page.once("dialog", lambda d: d.accept())
    page.locator("[data-del-supply]").first.click()
    page.wait_for_timeout(800)
    after_ok = page.locator("#tab-supplies tbody tr").count()
    record("删除供给二次确认（确认）", "确认后批次被删除",
           f"行数 {before}→{after_ok}", after_ok == before - 1)
    page.locator("#btn-back").click()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

    # —— ② 服务端会话失效 → 前端统一回登录页并提示 ——
    with db.request():
        db.execute("DELETE FROM sessions")  # 模拟服务端会话过期/重启丢会话
        db.commit()
    page.locator("#btn-matching").click()  # 任一需登录的 API 调用都会触发统一处理
    page.wait_for_selector("#view-auth:not([hidden])", timeout=8000)
    hint = page.locator("#auth-hint").inner_text()
    record("会话过期统一处理", "回登录页并提示重新登录",
           hint[:30], "登录已过期" in hint)
    # 重新登录恢复现场
    page.locator("#auth-username").fill("admin")
    page.locator("#auth-password").fill("admin-pass-123")
    page.locator("#btn-auth-submit").click()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)

    # —— ③ 撮合计算防重复提交：请求在途时按钮禁用 ——
    page.locator("#btn-matching").click()
    page.wait_for_selector("#view-matching:not([hidden])", timeout=5000)
    page.locator(".matching-pick[data-tid='T1']").check()
    page.locator(".matching-pick[data-tid='T2']").check()
    held = []
    page.route("**/api/matching/preview", lambda r: held.append(r))  # 挂起在途请求
    page.locator("#btn-matching-run").click()
    page.wait_for_timeout(400)
    in_flight_disabled = page.locator("#btn-matching-run").is_disabled()
    record("防重复提交（请求在途）", "计算期间按钮禁用",
           f"disabled={in_flight_disabled}", in_flight_disabled)
    while held:  # 放行挂起请求，走正常流程
        held.pop().continue_()
    page.wait_for_selector("#matching-results table", timeout=30000)
    record("防重复提交（完成后恢复）", "完成后按钮恢复可用",
           f"disabled={page.locator('#btn-matching-run').is_disabled()}",
           not page.locator("#btn-matching-run").is_disabled())

    # —— ④ 网络故障友好提示：中断请求 → 可行动的错误说明 ——
    page.route("**/api/matching/preview", lambda r: r.abort())
    page.locator("#btn-matching-run").click()
    page.wait_for_selector("#matching-results .error", timeout=8000)
    msg = page.locator("#matching-results .error").inner_text()
    page.unroute("**/api/matching/preview")
    record("网络故障友好提示", "显示可行动的连接失败说明",
           msg[:40], "无法连接服务器" in msg)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS_DIR / "v18-fault-tolerance.png"), full_page=True)

    # —— ⑤ 大案例异步运行路径（≥16 供给自动 async=1 + 作业轮询，v18 任务 3） ——
    api = ApiClient(port)
    _s, body = api.request("POST", "/api/auth/login",
                           {"username": "admin", "password": "admin-pass-123"})
    api.csrf = (body or {}).get("csrf") or ""
    status, _d = api.request("POST", "/api/tasks",
                             {"demand": dict(DEMAND_A, demand_id="E2E-C",
                                             buyer_name="异步需方（演示）"),
                              "task_id": "T3"})
    assert status == 201
    for i in range(1, 18):  # 17 个供给批次（≥ 阈值 16）
        status, _d = api.request("POST", "/api/tasks/T3/supplies",
                                 {"supply": dict(SUPPLY_S3, supply_id=f"A{i:02d}")})
        assert status == 201
    page.reload()
    page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
    page.locator("[data-task-open='T3']").first.click()
    page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
    page.wait_for_timeout(900)
    page.locator("button[data-tab='compare']").click()
    page.wait_for_selector("#btn-run", timeout=10000)
    seen_urls = []
    page.route("**/api/tasks/T3/runs*", lambda r: (seen_urls.append(r.request.url),
                                                    r.continue_()))
    jobs_seen = []
    page.route("**/api/runs/jobs/**", lambda r: (jobs_seen.append(r.request.url),
                                                  r.continue_()))
    page.locator("#btn-run").click()
    page.wait_for_selector("#compare-results table", timeout=120000)
    record("大案例异步运行", "≥16 供给自动 async=1、经作业轮询至完成并出结果",
           f"async={'async=1' in ' '.join(seen_urls)} 轮询={len(jobs_seen)} 次",
           any("async=1" in u for u in seen_urls) and len(jobs_seen) >= 1)
    page.unroute("**/api/tasks/T3/runs*")
    page.unroute("**/api/runs/jobs/**")

    record("console 零错误", "无 JS 异常（pageerror/console error）",
           f"{len(console_errors)} 条",
           not console_errors, "；".join(console_errors[:3]))


def _write_report():
    lines = ["# 自审第二轮 · v18（工作台容错性端到端）", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；"
             f"浏览器：{CHROME}（headless，真实 Chromium 内核）；环境：干净临时目录 + 空数据库。", "",
             "## 用例与实测", "",
             "| 用例 | 预期 | 实测 | 结果 | 备注 |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['expected']} | {r['actual']} | "
                     f"{'✔' if r['ok'] else '✘'} | {r['note']} |")
    lines += ["", "## 截图", "",
              "- docs/e2e-shots/v18-fault-tolerance.png（容错提示整页）", "",
              "## 未覆盖项", "",
              "- Firefox/WebKit 未测（本机仅 Chrome/Edge，如实列出）。",
              "- 真实网络断开/服务崩溃场景以 Playwright route 模拟（挂起/中断），不依赖环境故障。",
              "", "> 本文件由 tests/e2e_v18.py 每次运行重写（活文档）。", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告：{REPORT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
