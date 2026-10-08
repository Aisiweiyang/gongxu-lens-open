"""三端改版前基线截图（v23 任务 3）：site 1440/768/390 + 离线仪表盘 + 试点工作台。

用法：.e2e-venv\\Scripts\\python scripts\\shots_v23_baseline.py（Windows，真实 Chrome headless）
前置：先运行 run.py --industry 再生PP --data-mode demo 生成 output/ 最新批次。
产物：docs/e2e-shots/v23-baseline/*.png（改版前后对比存档，不入发行白名单）。
"""

import http.server
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "docs" / "e2e-shots" / "v23-baseline"

if sys.platform == "win32":
    CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
else:
    CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def launch_chrome_cdp(port):
    profile = tempfile.mkdtemp(prefix="pw-cdp-v23base-")
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


def latest_output(prefix):
    hits = sorted((BASE / "output").glob(prefix + "*.html"))
    if not hits:
        raise RuntimeError(f"output/ 无 {prefix}*.html，请先运行 run.py")
    return hits[-1]


def shoot(page, url, path, width, height=900, full=True, wait=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url)
    page.wait_for_load_state("load")
    page.wait_for_timeout(wait)
    page.screenshot(path=str(path), full_page=full)
    print("saved", path.name)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dashboard = latest_output("再生PP绿色决策仪表盘_")

    static_port = free_port()
    handler = lambda *a, **kw: QuietHandler(*a, directory=str(BASE), **kw)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", static_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from pilot.db import Database
    from pilot.server import make_server
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / "pilot.sqlite3")
    pilot_httpd = make_server(db, host="127.0.0.1", port=0)
    pilot_port = pilot_httpd.server_address[1]
    threading.Thread(target=pilot_httpd.serve_forever, daemon=True).start()

    cdp_port = free_port()
    proc, profile = launch_chrome_cdp(cdp_port)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                site = f"http://127.0.0.1:{static_port}/site/index.html"
                shoot(page, site, OUT_DIR / "site-1440.png", 1440)
                shoot(page, site, OUT_DIR / "site-768.png", 768)
                shoot(page, site, OUT_DIR / "site-390.png", 390, height=800)
                shoot(page, dashboard.as_uri(), OUT_DIR / "dashboard-1440.png", 1440)

                # 工作台：登录页 → 登录（空库首登即初始化管理员）→ 任务列表
                pilot = f"http://127.0.0.1:{pilot_port}/"
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(pilot)
                page.wait_for_load_state("load")
                page.wait_for_timeout(600)
                page.screenshot(path=str(OUT_DIR / "pilot-login-1440.png"), full_page=True)
                print("saved pilot-login-1440.png")
                page.locator("#auth-username").fill("admin")
                page.locator("#auth-password").fill("admin-pass-123")
                page.locator("#btn-auth-submit").click()
                page.wait_for_selector("#view-tasks:not([hidden])", timeout=8000)
                page.wait_for_timeout(600)
                page.screenshot(path=str(OUT_DIR / "pilot-tasks-1440.png"), full_page=True)
                print("saved pilot-tasks-1440.png")
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
    pilot_httpd.shutdown()
    httpd.shutdown()
    db.close()
    for _ in range(10):
        try:
            tmp.cleanup()
            break
        except OSError:
            time.sleep(0.5)
    print("基线截图完成：", OUT_DIR)


if __name__ == "__main__":
    main()
