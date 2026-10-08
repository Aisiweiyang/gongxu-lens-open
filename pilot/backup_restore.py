"""试点数据备份与恢复 CLI（在线备份 API；恢复前自动校验备份文件合法性）。

用法：
  python pilot/backup_restore.py backup --db <db路径>
  python pilot/backup_restore.py restore --db <db路径> --source <备份文件>
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot.db import Database

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "pilot.sqlite3"
BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def main():
    parser = argparse.ArgumentParser(description="试点数据备份/恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    backup_p = sub.add_parser("backup")
    backup_p.add_argument("--db", default=str(DEFAULT_DB))
    restore_p = sub.add_parser("restore")
    restore_p.add_argument("--db", default=str(DEFAULT_DB))
    restore_p.add_argument("--source", required=True)
    args = parser.parse_args()

    db = Database(Path(args.db))
    try:
        if args.command == "backup":
            import time
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            name = f"pilot-backup-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
            target = BACKUP_DIR / name
            db.backup_to(target)
            db.audit("system", "backup.create", f"backup:{name}")
            print(f"备份完成：{target}")
            return 0
        source = Path(args.source)
        if not re.fullmatch(r"pilot-backup-[\d-]+\.sqlite3", source.name):
            print("错误：备份文件名必须形如 pilot-backup-YYYYMMDD-HHMMSS.sqlite3", file=sys.stderr)
            return 2
        if not source.exists():
            print(f"错误：备份文件不存在：{source}", file=sys.stderr)
            return 2
        try:
            db.restore_from(source)
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        db.audit("system", "backup.restore", f"backup:{source.name}")
        print(f"已从 {source} 恢复。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
