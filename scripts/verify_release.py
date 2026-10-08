"""发行包独立安装验证：
- competition-demo：复制到临时目录 → 离线打开站点（真实 Chromium）→ 切换口径 → 校验下载护照。
- pilot-app：复制到临时目录 → 全新 venv（仅 requirements）→ init_admin → 起服务 →
  建任务/供供给/跑引擎/下载护照（经 HTTP）→ 备份恢复往返。
产物：docs/发行验证-v10.md（含各步实测与哈希核对）。
"""

import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "dist"
CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
REPORT = BASE / "docs" / "发行验证-v10.md"
PY = sys.executable

results = []


def record(case, expected, actual, ok, note=""):
    results.append({"case": case, "expected": expected, "actual": actual,
                    "ok": bool(ok), "note": note})
    print(("PASS " if ok else "FAIL ") + case + (f" — {note}" if note else ""))


def verify_hashes(pkg, files_txt):
    ok = True
    for line in files_txt.splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        f = Path(pkg) / rel.strip()
        if not f.exists():
            ok = False
            print(f"  缺失：{rel}")
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != digest:
            ok = False
            print(f"  哈希不一致：{rel}")
    return ok


def verify_demo():
    print("\n=== competition-demo 独立验证 ===")
    tmp = tempfile.mkdtemp(prefix="demo-verify-")
    copy = Path(tmp) / "competition-demo"
    shutil.copytree(DIST / "competition-demo", copy)
    record("演示包哈希核对", "FILES.txt 全部一致",
           "一致" if verify_hashes(copy, (copy / "FILES.txt").read_text(encoding="utf-8"))
           else "存在不一致",
           verify_hashes(copy, (copy / "FILES.txt").read_text(encoding="utf-8")))
    # 离线打开（file://）
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto((copy / "site" / "index.html").as_uri())
        verdict = page.locator(".verdict").first.inner_text()
        record("演示包离线打开（file://，独立路径）", "首屏结论出现", verdict[:30],
               "本次决策结论" in verdict)
        for _ in range(6):
            page.locator("#toggle-mode").click()
        record("演示包口径往返切换 6 次", "无异常、容器存在",
               page.locator("#mode-label").inner_text(),
               page.locator("#options-body").count() == 1)
        with page.expect_download() as dl:
            page.locator("#download-passport").click()
        saved = Path(tmp) / "demo-passport.json"
        dl.value.save_as(str(saved))
        doc = json.loads(saved.read_text(encoding="utf-8"))
        record("演示包护照下载", "Schema v2 + option_id",
               doc.get("schema_version"),
               doc.get("schema_version") == "2.0.0" and doc.get("options"))
        browser.close()
    # 动态再生成（独立路径跑 run.py）
    env = dict()
    out = subprocess.run(
        [PY, str(copy / "run.py"), "--industry", "再生PP", "--data-mode", "demo"],
        capture_output=True, text=True, timeout=120)
    record("演示包动态再生成 run.py", "输出报告/仪表盘/站点",
           (out.stdout or "").strip().splitlines()[-1:] if out.stdout else out.stderr[:100],
           out.returncode == 0 and (copy / "site" / "index.html").exists())
    shutil.rmtree(tmp, ignore_errors=True)


def verify_pilot():
    print("\n=== pilot-app 独立验证 ===")
    tmp = tempfile.mkdtemp(prefix="pilot-verify-")
    copy = Path(tmp) / "pilot-app"
    shutil.copytree(DIST / "pilot-app", copy)
    record("试点包哈希核对", "FILES.txt 全部一致",
           "一致" if verify_hashes(copy, (copy / "FILES.txt").read_text(encoding="utf-8"))
           else "存在不一致",
           verify_hashes(copy, (copy / "FILES.txt").read_text(encoding="utf-8")))
    # 全新 venv + 依赖安装（联网仅此一步）
    venv = Path(tmp) / "venv"
    subprocess.run([PY, "-m", "venv", str(venv)], check=True, timeout=180)
    pip = venv / "Scripts" / "pip.exe"
    subprocess.run([str(pip), "install", "-q", "-r", str(copy / "requirements.txt")],
                   check=True, timeout=300)
    record("全新 venv 安装依赖", "requirements.txt 安装成功", "venv + PyYAML + OR-Tools", True)
    py = venv / "Scripts" / "python.exe"
    # init admin
    out = subprocess.run([str(py), str(copy / "pilot" / "init_admin.py"),
                          "--username", "admin", "--password", "admin-pass-123",
                          "--db", str(copy / "pilot" / "data" / "pilot.sqlite3")],
                         capture_output=True, text=True, timeout=60)
    record("init_admin（无硬编码默认密码）", "管理员创建成功",
           (out.stdout or out.stderr).strip()[:40], out.returncode == 0)
    # 起服务（独立路径、独立端口）
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        [str(py), str(copy / "pilot" / "start_pilot.py"),
         "--host", "127.0.0.1", "--port", str(port),
         "--db", str(copy / "pilot" / "data" / "pilot.sqlite3")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/health")
        health = json.loads(conn.getresponse().read().decode("utf-8"))
        record("试点服务健康检查", "status=ok", str(health)[:60],
               health.get("status") == "ok")
        cookie = ""
        conn.request("POST", "/api/auth/login", body=json.dumps(
            {"username": "admin", "password": "admin-pass-123"}),
            headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        login = json.loads(res.read().decode("utf-8"))
        cookie = res.getheader("Set-Cookie").split(";")[0]
        record("独立路径登录", "登录成功", login.get("role"), login.get("role") == "admin")
        headers = {"Content-Type": "application/json", "Cookie": cookie,
                   "X-CSRF-Token": login["csrf"]}
        demand = {"demand_id": "V1", "buyer_name": "验证需方", "target_material_code": "PP",
                  "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
                  "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5,
                  "max_ash_pct": 5.0, "accepted_form": "颗粒", "accepted_color": "黑色",
                  "required_from": "2026-09-15", "due_date": "2026-10-15",
                  "destination": "验证园区", "baseline_virgin_price_cny_per_t": 5500.0,
                  "max_distance_km": 500.0}
        conn.request("POST", "/api/tasks", body=json.dumps({"demand": demand}),
                     headers=headers)
        created = json.loads(conn.getresponse().read().decode("utf-8"))
        record("建任务", "201", str(created)[:50], created.get("task_id") == "V1")
        supplies = [
            {"supply_id": "S1", "supplier_name": "甲", "material_code": "PP",
             "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
             "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
             "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
             "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "验证园区",
             "price_cny_per_t": 4200.0, "price_includes_freight": False, "yield_pct": 100.0,
             "preprocessing": "已造粒", "transport_mode": "公路货运",
             "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
             "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "验证"},
            {"supply_id": "S2", "supplier_name": "乙", "material_code": "PP",
             "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
             "color": "黑色", "grade": "注塑级", "mfi_g_10min": 11.0, "moisture_pct": 0.3,
             "ash_pct": 3.5, "recycled_content_pct": 90.0, "available_mass_t": 18.0,
             "available_from": "2026-09-08", "available_to": "2026-10-10", "origin": "外省",
             "price_cny_per_t": 3800.0, "price_includes_freight": False, "yield_pct": 100.0,
             "preprocessing": "已造粒", "transport_mode": "公路货运",
             "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 480.0,
             "evidence_source": "检测报告", "evidence_date": "2026-08-05", "source": "验证"},
        ]
        for supply in supplies:
            conn.request("POST", "/api/tasks/V1/supplies",
                         body=json.dumps({"supply": supply}), headers=headers)
            conn.getresponse().read()
        conn.request("POST", "/api/tasks/V1/runs", body="{}", headers=headers)
        run = json.loads(conn.getresponse().read().decode("utf-8"))
        record("引擎运行（独立路径）", "run_id 生成", str(run)[:50], "run_id" in run)
        conn.request("GET", f"/api/tasks/V1/passport?run_id={run['run_id']}",
                     headers={"Cookie": cookie})
        res = conn.getresponse()
        doc = json.loads(res.read().decode("utf-8"))
        record("护照下载（绑定 run_id）", "Schema v2",
               f"{doc.get('schema_version')} run_id={doc.get('run_id')}",
               doc.get("schema_version") == "2.0.0" and doc.get("run_id") == run["run_id"])
        # 备份恢复往返（独立路径）
        conn.request("POST", "/api/backup", body="{}", headers=headers)
        backup = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.request("DELETE", "/api/tasks/V1/supplies/S1", headers=headers)
        conn.getresponse().read()
        conn.request("POST", f"/api/backups/{backup['backup']}/restore", body="{}",
                     headers=headers)
        restored = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.request("GET", "/api/tasks/V1/supplies", headers={"Cookie": cookie})
        supplies_after = json.loads(conn.getresponse().read().decode("utf-8"))
        record("备份恢复往返（独立路径）", "恢复后供给数 2",
               f"{len(supplies_after.get('supplies', []))} 条 + {restored}",
               len(supplies_after.get("supplies", [])) == 2)
        conn.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    verify_demo()
    verify_pilot()
    lines = ["# 发行包独立验证 · ", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；"
             "验证方式：复制到全新临时目录，独立路径运行（不依赖原项目绝对路径）；"
             "试点包用全新 venv 仅装 requirements.txt。", "",
             "| 用例 | 预期 | 实测 | 结果 | 备注 |", "|---|---|---|---|---|"]
    for r in results:
        actual = str(r['actual'])[:60]
        # 报告为入库文档：实测输出中的本机用户目录泛化为占位符
        actual = re.sub(r"[A-Za-z]:\\{1,2}Users\\{1,2}[^\\]+", r"<本机用户目录>", actual)
        lines.append(f"| {r['case']} | {r['expected']} | {actual} | "
                     f"{'✔' if r['ok'] else '✘'} | {r['note']} |")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    failed = sum(1 for r in results if not r["ok"])
    print(f"\n发行验证：{len(results) - failed}/{len(results)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
