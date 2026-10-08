"""v20 任务 2：试点自检脚本最小回归（只测检查函数，不起服务）。"""

import tempfile
import unittest
from pathlib import Path

sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

from pilot.db import Database, now  # noqa: E402
from scripts.pilot_selfcheck import (check_database, check_project_files,  # noqa: E402
                                     check_python)


class TestPilotSelfcheck(unittest.TestCase):
    def test_01_python_version_check(self):
        ok, detail = check_python()
        self.assertTrue(ok)
        self.assertIn("Python", detail)

    def test_02_project_files_complete(self):
        ok, detail = check_project_files()
        self.assertTrue(ok, detail)

    def test_03_database_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 不存在 → FAIL 且提示初始化路径
            ok, detail = check_database(Path(tmp) / "none.sqlite3")
            self.assertFalse(ok)
            self.assertIn("不存在", detail)
            # 存在 → PASS 并报告 schema 版本与账号数
            db_path = Path(tmp) / "pilot.sqlite3"
            db = Database(db_path)
            with db.request():
                ts = now()
                db.execute(
                    "INSERT INTO users(username,salt,password_hash,role,active,"
                    "created_at,updated_at) VALUES('admin','s','h','admin',1,?,?)",
                    (ts, ts))
                db.commit()
            db.close()
            ok, detail = check_database(db_path)
            self.assertTrue(ok, detail)
            self.assertIn("账号 1", detail)


if __name__ == "__main__":
    unittest.main()
