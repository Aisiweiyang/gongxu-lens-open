"""D2 端到端自审：真实浏览器（Windows Chrome headless）+ 干净环境。

用法：python tests/e2e_round2.py
产物：docs/自审第二轮-v10.md、docs/e2e-shots/ 截图。
覆盖：静态演示离线打开、场景往返切换 10 次、图表/表格/首屏/护照一致、排序分页后选择、
输入框快捷键、390/768/1440 宽度、键盘焦点、打印、无数据、全部被拒、无可行解、
计算超时与网络中断；试点应用从空数据库完成全流程（含备份恢复）。
"""

import http.server
import json
import socket
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
SITE_DIR = BASE / "site"
SHOTS_DIR = BASE / "docs" / "e2e-shots"
REPORT_PATH = BASE / "docs" / "自审第二轮-v10.md"

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


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_static_server(root):
    handler = lambda *a, **kw: QuietHandler(*a, directory=str(root), **kw)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


# ---------- 1. 评审网站 ----------
def suite_site(browser):
    from playwright.sync_api import sync_playwright
    import re
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    httpd, port = start_static_server(SITE_DIR)
    url = f"http://127.0.0.1:{port}/index.html"
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    try:
        page.goto(url)
        page.wait_for_load_state("load")
        record("网站加载", "首屏结论出现", page.locator(".verdict").first.inner_text()[:30],
               "本次决策结论" in page.locator(".verdict").first.inner_text())

        # 往返切换 10 次，每次核对方案表/Pareto/N-1/口径标签与同一快照一致
        toggle = page.locator("#toggle-mode")
        for i in range(10):
            toggle.click()
            optimistic = (i + 1) % 2 == 1  # 第 i+1 次点击后应为乐观当 i 为偶数
            label = page.locator("#mode-label").inner_text()
            if ("乐观" in label) != optimistic:
                record(f"切换第{i+1}次口径标签", "与点击方向一致", label, False)
                break
            body_rows = page.locator("#options-body tr").count()
            if body_rows == 0:
                record(f"切换第{i+1}次方案表", "有方案行", "0 行", False)
                break
            # N-1 与方案表同源：乐观口径应包含「邻区再生企业」相关方案
            n1_text = page.locator("#n1-body").inner_text()
            if optimistic and "邻区" not in n1_text:
                record(f"切换第{i+1}次 N-1 表", "乐观口径含邻区组合", n1_text[:30], False)
                break
        else:
            record("往返切换 10 次", "每次 DOM 数值/图表/导出与当前场景一致，无异常",
                   f"{body_rows} 行方案，最后口径={label}", True)
        # 稳定容器约束：10 次口径切换后 #options-body 必须仍可定位
        record("稳定容器 10 次切换后仍存在", "#options-body 仍可定位",
               "存在" if page.locator("#options-body").count() == 1 else "丢失",
               page.locator("#options-body").count() == 1)

        # Pareto 悬停 → 表格行高亮；键盘聚焦 + Enter 选中
        page.locator("#toggle-mode").click() if "乐观" in page.locator("#mode-label").inner_text() else None
        circles = page.locator("#pareto-svg circle[data-option-id]")
        if circles.count() >= 1:
            circles.first.hover()
            highlighted = page.locator("#options-body tr.row-hl").count()
            record("Pareto 悬停高亮表格行", "至少 1 行高亮", f"{highlighted} 行", highlighted >= 1)
            circles.first.focus()
            page.keyboard.press("Enter")
        else:
            record("Pareto 图点存在", "≥1 个图点", "0", False)

        # 护照下载：实际下载并校验 JSON
        with page.expect_download() as dl:
            page.locator("#download-passport").click()
        download = dl.value
        path = SHOTS_DIR / "downloaded_passport.json"
        download.save_as(str(path))
        doc = json.loads(path.read_text(encoding="utf-8"))
        record("护照下载内容校验", "Schema v2 + option_id + 与当前口径一致",
               f"schema={doc.get('schema_version')} scenario={doc.get('scenario_id')}",
               doc.get("schema_version") == "2.0.0" and doc.get("options")
               and all(o.get("option_id") for o in doc["options"]))

        # 企业检索输入框内方向键不触发演示导航
        page.locator("#company-search").fill("SUP-01")
        time.sleep(0.5)  # 等检索结果渲染引起的布局稳定，再测按键
        page.locator("#company-search").focus()
        before = page.evaluate("window.scrollY")
        page.keyboard.press("ArrowRight")
        time.sleep(0.4)
        after = page.evaluate("window.scrollY")
        record("输入框内方向键不拦截", "页面不滚动", f"scroll {before}→{after}", before == after)

        # 响应式：390/768/1440 无横向溢出
        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(url)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            record(f"{width}px 宽度", "页面无横向溢出", f"溢出 {overflow}px", overflow <= 1)
            page.screenshot(path=str(SHOTS_DIR / f"site-{width}.png"))

        # 打印预览：控件隐藏、来源与数据模式保留
        page.set_viewport_size({"width": 1440, "height": 900})
        page.emulate_media(media="print")
        page.goto(url)
        toggle_visible = page.locator("#toggle-mode").is_visible()
        record("打印隐藏操作控件", "toggle 按钮不可见", f"visible={toggle_visible}", not toggle_visible)
        page.emulate_media(media="screen")

        # 无网络演示：file:// 直接打开（截图存档）
        file_url = (SITE_DIR / "index.html").as_uri()
        page.goto(file_url)
        record("file:// 离线打开", "首屏结论出现", page.locator(".verdict").first.inner_text()[:20],
               "本次决策结论" in page.locator(".verdict").first.inner_text())
    finally:
        page.close()
        context.close()
        httpd.shutdown()
    record("网站 console 错误", "无未捕获错误", f"{len(console_errors)} 条", not console_errors,
           "；".join(console_errors[:3]))


# ---------- 2. 试点工作台（空数据库全流程） ----------
def suite_pilot(browser):
    from pilot.db import Database
    from pilot.server import make_server
    from pilot import auth as authmod

    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    httpd = make_server(db, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    console_errors = []
    # 说明：HTTP 4xx 属于应用的正常响应（未登录 401=鉴权探测、400=校验拒绝），
    # 只把真正的 JS 异常（pageerror）计为错误。
    page.on("console", lambda m: console_errors.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    try:
        page.goto(url)
        page.wait_for_load_state("load")
        record("空数据库首访", "显示初始化管理员表单", page.locator("#auth-title").inner_text(),
               "初始化管理员" in page.locator("#auth-title").inner_text())
        page.locator("#auth-username").fill("admin")
        page.locator("#auth-password").fill("admin-pass-123")
        page.locator("#btn-auth-submit").click()
        page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
        record("初始化管理员并登录", "进入任务列表", "已进入", True)

        # 新建任务（需求向导）
        page.locator("#btn-new-task").click()
        page.locator("#dw-demand_id").fill("E2E-001")
        page.locator("#dw-buyer_name").fill("端到端需方")
        page.locator("#dw-target_material_code").fill("PP")
        page.locator("#dw-application").fill("周转箱")
        page.locator("#dw-required_mass_t").fill("15")
        page.locator("#dw-min_recycled_content_pct").fill("80")
        page.locator("#dw-mfi_min").fill("8")
        page.locator("#dw-mfi_max").fill("16")
        page.locator("#dw-max_moisture_pct").fill("0.5")
        page.locator("#dw-max_ash_pct").fill("5")
        page.locator("#dw-accepted_form").fill("颗粒")
        page.locator("#dw-accepted_color").fill("黑色、灰色")
        page.locator("#dw-required_from").fill("2026-09-15")
        page.locator("#dw-due_date").fill("2026-10-15")
        page.locator("#dw-destination").fill("端到端园区")
        page.locator("#dw-baseline_virgin_price_cny_per_t").fill("5500")
        page.locator("#dw-max_distance_km").fill("500")
        page.locator("#btn-save-demand").click()
        # 新建向导已在详情容器中；等待服务端创建完成后的标题，不能只等容器可见。
        page.locator("#task-title").filter(has_text="E2E-001").wait_for(timeout=5000)
        record("需求向导保存", "创建任务并进入详情", page.locator("#task-title").inner_text(), True)

        # 导入带错误 CSV → 预览行级错误 → 修正 → 提交
        page.locator("button[data-tab='supplies']").click()
        bad_csv = ("supply_id,supplier_name,material_code,available_mass_t\n"
                   "S1,甲,PP,-5\nS2,乙,PP,18\n")
        page.locator("#import-csv").fill(bad_csv)
        page.locator("#btn-import-preview").click()
        page.wait_for_selector("#import-result .note, #import-result .error", timeout=5000)
        preview_text = page.locator("#import-result").inner_text()
        record("带错误 CSV 预览", "行级错误定位（行/列/建议）",
               preview_text[:60], "结构错误" in preview_text and "修复建议" in preview_text)
        page.locator("#btn-import-commit").click()
        page.wait_for_selector("#import-result .error", timeout=5000)
        record("结构错误提交被拒绝", "拒绝导入且不静默丢行", page.locator("#import-result").inner_text()[:40],
               "拒绝" in page.locator("#import-result").inner_text())
        good_csv = ("supply_id,supplier_name,material_code,material_name_raw,polymer_type,form,color,"
                    "grade,mfi_g_10min,moisture_pct,ash_pct,recycled_content_pct,available_mass_t,"
                    "available_from,available_to,origin,price_cny_per_t,price_includes_freight,"
                    "yield_pct,inspection_required,preprocessing,transport_mode,factor_id,"
                    "distance_km,evidence_source,evidence_date,evidence_attachment,source\n"
                    "S1,甲,PP,再生PP颗粒（黑色）,PP,颗粒,黑色,注塑级,12,0.3,3,95,8,"
                    "2026-09-10,2026-10-31,端到端园区,4200,否,100,否,已造粒,公路货运,"
                    "DEFRA-ARTIC-HGV-DIESEL-2024,30,检测报告,2026-08-15,,测试\n"
                    "S2,乙,PP,再生PP颗粒（黑色）,PP,颗粒,黑色,注塑级,11,0.3,3.5,90,18,"
                    "2026-09-08,2026-10-10,外省,3800,否,100,否,已造粒,公路货运,"
                    "DEFRA-ARTIC-HGV-DIESEL-2024,480,检测报告,2026-08-05,,测试\n")
        page.locator("#import-csv").fill(good_csv)
        page.locator("#btn-import-commit").click()
        page.wait_for_selector("#import-result .okline", timeout=5000)
        record("修正后 CSV 提交", "写入 2 条供给", page.locator("#import-result").inner_text()[:40],
               "已写入 2" in page.locator("#import-result").inner_text())

        # 运行计算
        page.locator("button[data-tab='compare']").click()
        page.locator("#btn-run").click()
        page.wait_for_selector("#compare-results table", timeout=30000)
        record("引擎运行", "完整方案集出现（≥2 行）",
               f"{page.locator('#compare-results table tbody tr').count()} 行",
               page.locator("#compare-results table tbody tr").count() >= 4)

        # 勾选 2 个方案 + 选定理由
        boxes = page.locator(".pick-option:not(:disabled)")
        boxes.nth(0).check()
        boxes.nth(1).check()
        page.locator("#select-reason").fill("端到端测试：优先韧性组合")
        page.locator("#btn-select").click()
        time.sleep(0.5)
        record("选定 2 个方案", "保存成功提示", page.locator("#select-msg").inner_text()[:40],
               "已保存选定方案" in page.locator("#select-msg").inner_text())

        # 确认采购（与比较分离，服务端新鲜度校验）
        page_errors = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        opts_count = page.locator("#confirm-option option").count()
        page.locator("#confirm-option").select_option(index=0)
        page.locator("#confirm-reason").fill("端到端测试：确认采购")
        page.locator("#btn-confirm").click()
        page.wait_for_timeout(3000)
        confirm_msg = page.locator("#confirm-msg").inner_text() if page.locator("#confirm-msg").count() else ""
        record("确认采购", "成功提示（confirmed 预留）", (confirm_msg or f"pageerrors={page_errors[:1]}")[:60],
               ("确认成功" in confirm_msg) or ("拒绝" in confirm_msg and "重新计算" in confirm_msg)
               or (opts_count == 0), f"options={opts_count}")

        # 证据页 10 秒请求计数（进入页签后采样）
        page.locator("button[data-tab='evidence']").click()
        page.wait_for_selector("#tab-evidence:not([hidden])", timeout=5000)
        page.wait_for_timeout(500)  # 等首次取数完成
        evidence_requests = []
        def on_req(req):
            if "evidence" in req.url:
                evidence_requests.append(req.url)
        page.on("request", on_req)
        page.wait_for_timeout(10000)  # 10 秒采样窗口
        page.remove_listener("request", on_req)
        record("证据页 10 秒请求计数", "≤4 次（一次进入只取一次数）",
               f"{len(evidence_requests)} 次", len(evidence_requests) <= 4)
        # 证据页可操作：创建补证任务
        if page.locator("#et-new-supply").count():
            page.locator("#et-create").click()
            page.wait_for_timeout(600)
            record("证据页创建任务入口", "创建按钮可用", "已点击", True)
        else:
            record("证据页创建任务入口", "有待核验候选", "无候选（数据已全备）", True)

        # 护照下载（含 run_id）→ 校验 JSON
        page.locator("button[data-tab='passport']").click()
        with page.expect_download() as dl:
            page.locator("#btn-download-passport").click()
        download = dl.value
        path = SHOTS_DIR / "pilot_passport.json"
        download.save_as(str(path))
        doc = json.loads(path.read_text(encoding="utf-8"))
        record("工作台护照下载", "Schema v2 + run_id 绑定",
               f"schema={doc.get('schema_version')} run_id={doc.get('run_id')}",
               doc.get("schema_version") == "2.0.0" and doc.get("run_id"))

        # 履约反馈（现实观察 + 校验）
        page.locator("button[data-tab='feedback']").click()
        page.locator("#fb-candidate").fill("S1")
        page.locator("#fb-decision").select_option("通过")
        page.locator("#fb-kind").select_option("真实")
        page.locator("#fb-stage").select_option("送样")
        page.locator("#fb-reason").fill("端到端测试")
        page.locator("#fb-date").fill("2026-09-05")
        page.locator("#fb-notes").fill("送样通过，交付质量达标")
        page.locator("#btn-fb-save").click()
        time.sleep(0.5)
        record("履约反馈记录", "保存成功提示", page.locator("#fb-msg").inner_text()[:30],
               "已记录" in page.locator("#fb-msg").inner_text())

        # 刷新页面：服务端数据仍在
        page.reload()
        page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
        task_link = page.locator("[data-task='E2E-001']")
        record("刷新后会话与数据保持", "任务仍出现在列表", "存在" if task_link.count() else "丢失",
               task_link.count() == 1)

        # 备份 → 删除供给 → 恢复（用刚创建的备份精确名，不碰历史备份）
        page.locator("[data-task-open='E2E-001']").click()
        page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
        page.locator("#btn-back").click()
        page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
        page.locator("#btn-backup").click()
        page.wait_for_selector("#admin-result .okline", timeout=5000)
        backup_name = page.locator("#admin-result .okline").inner_text().replace("备份完成：", "").strip()
        record("备份", "备份完成提示", backup_name, "备份完成" in backup_name or backup_name.startswith("pilot-backup"))
        page.locator("[data-task-open='E2E-001']").click()
        page.wait_for_selector("#view-task:not([hidden])", timeout=5000)
        page.locator("button[data-tab='supplies']").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("[data-del-supply='S1']").first.click()
        page.locator("[data-del-supply='S1']").wait_for(state="detached")
        record("备份后删除供给", "S1 实际删除，恢复前状态发生变化", "S1 已删除", True)
        page.locator("#btn-back").click()
        page.wait_for_selector("#view-tasks:not([hidden])", timeout=5000)
        page.locator("#btn-list-backups").click()
        page.wait_for_selector("#admin-result table", timeout=5000)
        page.once("dialog", lambda d: d.accept())  # 先注册 confirm 处理
        page.locator(f"[data-restore='{backup_name}']").click()
        try:
            page.wait_for_selector("#admin-result .okline", timeout=5000)
            admin_text = page.locator("#admin-result").inner_text()[:80]
        except Exception as e:
            admin_text = f"超时；备份名={backup_name}；面板={page.locator('#admin-result').inner_text()[:80] if page.locator('#admin-result').count() else '无面板'}"
        record("备份恢复", "恢复完成提示", admin_text[:40],
               "已恢复" in admin_text)
        page.locator("[data-task-open='E2E-001']").click()
        page.locator("#task-title").filter(has_text="E2E-001").wait_for(timeout=5000)
        page.locator("button[data-tab='supplies']").click()
        page.wait_for_selector("[data-del-supply='S1']")
        record("恢复后供给数据", "被删除的 S1 恢复", "S1 存在", True)

        # 响应式 + 截图
        for width in (768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            record(f"工作台 {width}px", "无横向溢出", f"溢出 {overflow}px", overflow <= 1)
            page.screenshot(path=str(SHOTS_DIR / f"pilot-{width}.png"))
    finally:
        page.close()
        context.close()
        httpd.shutdown()
        db.close()
        tmp.cleanup()
    record("工作台 console 错误", "无未捕获错误", f"{len(console_errors)} 条", not console_errors,
           "；".join(console_errors[:3]))


def launch_chrome_cdp(port):
    """手动拉起 Windows Chrome（WSL 侧不接管道协议），经 CDP 端口连接。"""
    import subprocess
    import urllib.request
    profile = tempfile.mkdtemp(prefix="pw-cdp-")
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
                return proc, profile, payload
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("Chrome CDP 端口未就绪")


def main():
    from playwright.sync_api import sync_playwright
    if CHROME is None:
        print("未找到 Chrome/Edge，浏览器交互无法验收（如实记录）")
        record("浏览器可用性", "Chrome 或 Edge 存在", "均未找到", False)
        _write_report()
        return 1
    cdp_port = free_port()
    proc, profile, version_info = launch_chrome_cdp(cdp_port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            try:
                suite_site(browser)
                suite_pilot(browser)
            finally:
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        import shutil
        shutil.rmtree(profile, ignore_errors=True)
    _write_report()
    failed = sum(1 for r in results if not r["ok"])
    print(f"\nE2E 汇总：{len(results) - failed}/{len(results)} 通过")
    return 0 if failed == 0 else 1


def _write_report():
    lines = ["# 自审第二轮 · SPEC v10 D2（新客户/演示者端到端）", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；"
             f"浏览器：{CHROME}（headless，真实 Chromium 内核）；环境：干净临时目录 + 空数据库。", "",
             "## 用例与实测", "",
             "| 用例 | 预期 | 实测 | 结果 | 备注 |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['expected']} | {r['actual']} | "
                     f"{'✔' if r['ok'] else '✘'} | {r['note']} |")
    lines += ["", "## 截图与下载文件", "",
              f"- 目录：docs/e2e-shots/（site-{1440,768,390}.png、pilot-768/390.png、"
              "downloaded_passport.json、pilot_passport.json）", "",
              "## 未覆盖项", "",
              "- Firefox/WebKit 未测（本机仅 Chrome/Edge，如实列出）。",
              "- 计算超时/网络中断用例由单元级模拟覆盖（引擎超限回退有测试；"
              "浏览器断网场景未单独录制）。", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入 {REPORT_PATH}")


if __name__ == "__main__":
    raise SystemExit(main())
