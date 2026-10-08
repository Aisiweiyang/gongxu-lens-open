"""初始化管理员：仅当系统中没有任何账号时可用（无硬编码默认密码）。

用法：python pilot/init_admin.py --username admin --password <8位以上>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot import auth as authmod
from pilot.db import Database, now

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "pilot.sqlite3"


def main():
    parser = argparse.ArgumentParser(description="初始化试点应用管理员")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    policy_error = authmod.password_policy_error(args.password)
    if policy_error:
        print(f"错误：{policy_error}。", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    try:
        count = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if count > 0:
            print("错误：系统已有账号，初始化管理员仅允许在无任何账号时执行。", file=sys.stderr)
            return 1
        digest, salt = authmod.hash_password(args.password)
        db.execute(
            "INSERT INTO users(username,password_hash,salt,role,active,created_at,updated_at)"
            " VALUES(?,?,?,?,1,?,?)",
            (args.username, digest, salt, "admin", now(), now()))
        db.commit()
        db.audit("system", "auth.init_admin", f"user:{args.username}")
        print(f"管理员 {args.username} 已创建。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
