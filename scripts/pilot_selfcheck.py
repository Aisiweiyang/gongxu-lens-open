"""试点部署自检（v20 任务 2）：零依赖，供交付/远程支持快速定位环境问题。

检查项：解释器版本、项目文件完整性、数据库存在与 schema 版本、账号数、
备份目录、磁盘余量、（可选）健康端点。只报告事实，不做修复、不改任何数据。

用法：
    python scripts/pilot_selfcheck.py                       # 全项检查（健康端点跳过）
    python scripts/pilot_selfcheck.py --port 8765           # 附带健康端点探测
    python scripts/pilot_selfcheck.py --db 自定义路径.sqlite3
退出码：0=全部通过，1=存在失败项（供脚本化使用）。
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT / "pilot" / "data" / "pilot.sqlite3"  # 与 start_pilot 默认一致
BACKUP_DIR = PROJECT / "pilot" / "backups"
MIN_PY = (3, 10)
REQUIRED_FILES = (
    "pilot/start_pilot.py", "pilot/server.py", "pilot/db.py", "pilot/auth.py",
    "pilot/static/workbench.html", "pilot/static/app.js", "pilot/static/style.css",
    "src/material.py", "src/ledger.py", "src/matching_sheet.py",
    "config/material_emission_factors.yaml",
)


def check_python():
    ok = sys.version_info[:2] >= MIN_PY
    return ok, f"Python {sys.version.split()[0]}（要求 ≥ {MIN_PY[0]}.{MIN_PY[1]}）"


def check_project_files():
    missing = [f for f in REQUIRED_FILES if not (PROJECT / f).exists()]
    return not missing, ("项目文件完整" if not missing else f"缺失：{missing}")


def check_database(db_path):
    p = Path(db_path)
    if not p.exists():
        return False, (f"数据库不存在：{p}（首次部署请启动服务并在页面初始化管理员，"
                       "或用 pilot/init_admin.py）")
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        hint = ("WSL 访问 Windows 盘上的 WAL 库常见此错——请在部署机原生环境运行自检"
                if "I/O" in str(exc) or "disk" in str(exc).lower() else
                "文件可能损坏、被占用或不是本系统的库")
        return False, f"数据库无法只读打开（{exc}）——{hint}：{p}"
    return True, (f"schema=v{row[0] if row else '?'} · 账号 {users} · 任务 {tasks} · "
                  f"运行快照 {runs}")


def check_backups(backup_dir=BACKUP_DIR):
    d = Path(backup_dir)
    if not d.is_dir():
        return True, "尚无备份目录（建议启动后创建首个备份）"
    n = len(list(d.glob("*")))
    return True, f"备份文件 {n} 个（{d}）"


def check_disk(path, min_gb=1.0):
    free = shutil.disk_usage(path).free / 1024 ** 3
    return free >= min_gb, f"剩余 {free:.1f} GB（阈值 {min_gb:.0f} GB）"


def check_health(port, host="127.0.0.1", timeout=3):
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/api/health")
        res = conn.getresponse()
        body = res.read().decode("utf-8", "replace")
        conn.close()
        ok = res.status == 200 and '"ok"' in body.replace(" ", "")
        return ok, f"HTTP {res.status} {body[:80]}"
    except OSError as exc:
        return False, f"不可达（{exc.__class__.__name__}）：服务未启动或端口不对"


def main(argv=None):
    parser = argparse.ArgumentParser(description="供需透镜试点部署自检（只读，不做修复）")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--port", type=int, default=None,
                        help="如服务正在运行，附带探测 /api/health")
    args = parser.parse_args(argv)

    checks = [
        ("解释器", *check_python()),
        ("项目文件", *check_project_files()),
        ("数据库", *check_database(args.db)),
        ("备份", *check_backups()),
        ("磁盘", *check_disk(PROJECT)),
    ]
    if args.port is not None:
        checks.append(("健康端点", *check_health(args.port)))

    failed = 0
    print("供需透镜试点自检（只读）：")
    for name, ok, detail in checks:
        failed += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}：{detail}")
    print(f"结果：{len(checks) - failed}/{len(checks)} 通过"
          + ("（存在失败项，见上）" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
