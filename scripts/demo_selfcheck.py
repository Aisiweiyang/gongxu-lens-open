"""答辩前演示环境一键自检：双发行包完整性 + 演示链路关键点。

用法：python scripts\\demo_selfcheck.py（零依赖 stdlib，只读不改任何文件）
检查项：解释器版本、双包 VERSION/FILES 哈希、站点关键标记、Chrome 可用性、
8765 端口占用、output 最新批次。浏览器层行为（console 零错误等）由 tests/
e2e_*.py 覆盖，本脚本不重复。
产物：docs/演示自检-报告.md（每次运行重写）。
"""

import hashlib
import re
import socket
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent.parent
REPORT = BASE / "docs" / "演示自检-报告.md"
PACKAGES = [("演示包", BASE / "dist" / "competition-demo"),
            ("试点包", BASE / "dist" / "pilot-app")]
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]
results = []


def check(case, ok, note=""):
    results.append((case, bool(ok), note))
    print(("PASS " if ok else "FAIL ") + case + (f" — {note}" if note else ""))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_package(name, root):
    if not root.exists():
        check(f"{name} 存在", False, str(root))
        return
    ver = (root / "VERSION.txt")
    check(f"{name} VERSION.txt", ver.exists(),
          ver.read_text(encoding="utf-8", errors="replace").splitlines()[0] if ver.exists() else "")
    files = root / "FILES.txt"
    if not files.exists():
        check(f"{name} FILES.txt", False, "缺失")
        return
    bad, total = [], 0
    for line in files.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        total += 1
        target = root / rel
        if not target.exists() or sha256(target) != digest:
            bad.append(rel)
    check(f"{name} 哈希核对（{total} 文件）", not bad, "全部一致" if not bad else f"不一致：{bad[:3]}")


def main():
    major = sys.version_info
    check(f"Python 解释器 ≥3.12（当前 {major.major}.{major.minor}.{major.micro}）",
          (major.major, major.minor) >= (3, 12))

    site = BASE / "site" / "index.html"
    if site.exists():
        head = site.read_bytes()[:20000].decode("utf-8", errors="replace")
        check("评委网站存在且为品牌化当前版",
              "<title>供需透镜" in head and "data:image/svg+xml" in head)
    else:
        check("评委网站存在", False, "site/index.html 缺失")

    chrome = next((p for p in CHROME_CANDIDATES if p.exists()), None)
    check("Chrome/Edge 可用（E2E 与截图依赖）", chrome is not None, str(chrome) if chrome else "未找到")

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8765))
        check("8765 端口空闲（可启动演示网站/工作台）", True)
    except OSError:
        check("8765 端口空闲（可启动演示网站/工作台）", False, "被占用：结束旧进程或换 --port")
    finally:
        s.close()

    outputs = sorted((BASE / "output").glob("再生PP绿色决策仪表盘_*.html"))
    check("output/ 存在仪表盘批次（13 号截图与打包取件依赖）", bool(outputs),
          outputs[-1].name if outputs else "缺失；运行 run.py --data-mode demo 生成")

    for name, root in PACKAGES:
        check_package(name, root)

    lines = ["# 演示环境自检 · 答辩前快查", "",
             f"执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；零依赖只读检查，"
             "浏览器层行为由 tests/e2e_*.py 覆盖。", "",
             "| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    for case, ok, note in results:
        lines.append(f"| {case} | {'✔' if ok else '✘'} | {note} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n演示自检：{len(results) - failed}/{len(results)} 通过；报告：{REPORT.name}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
