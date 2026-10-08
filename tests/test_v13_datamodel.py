"""v13 数据模型回归：SCHEMA v4→v5 迁移（supplies/batches.external_id + relations 三元组）。

覆盖：四组迁移 + 兼容回归：
1. 全新库直建 v5；
2. v4 老库自动升级且既有数据逐行保留、external_id 缺省为空串；
3. 重复打开/重复迁移幂等；
4. 回滚演练：升级前备份 → 升级 → 恢复回 v4 → 再打开自动重新升级；
5. 旧 CSV（无 external_id 列）导入兼容、新 CSV 可选列透传；
6. supply payload 往返稳定（含/不含 external_id）。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pilot import db as pilot_db
from pilot.api import supply_from_payload, supply_to_payload
from src.material import load_supplies_csv

# v4 冻结快照（v13 变更前的 SCHEMA_SQL 原样副本，迁移测试的事实基准）
V4_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','editor','viewer')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY,
  supply_json TEXT NOT NULL,
  original_mass_t REAL NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  demand_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  supply_id TEXT NOT NULL,
  batch_id TEXT NOT NULL DEFAULT '',
  supply_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (task_id, supply_id)
);
CREATE TABLE IF NOT EXISTS evidence_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  action_id TEXT NOT NULL,
  supply_id TEXT NOT NULL,
  assignee TEXT NOT NULL DEFAULT '',
  due_date TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','submitted','passed','rejected','resubmit','closed')),
  evidence_ref TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  verdict_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  supply_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  evidence_type TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT '',
  attachment_ref TEXT NOT NULL DEFAULT '',
  issuing_body TEXT NOT NULL DEFAULT '',
  measured_date TEXT NOT NULL DEFAULT '',
  valid_until TEXT NOT NULL DEFAULT '',
  measured_fields_json TEXT NOT NULL DEFAULT '{}',
  submitted_by INTEGER,
  reviewed_by INTEGER,
  reviewed_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'passed' CHECK (status IN ('passed','superseded')),
  reject_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (task_id, supply_id, version_no)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  scenario_id TEXT NOT NULL,
  result_json TEXT NOT NULL,
  input_snapshot_json TEXT NOT NULL DEFAULT '{}',
  config_sha256 TEXT NOT NULL,
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  run_id TEXT NOT NULL REFERENCES runs(id),
  option_id TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  selected_by INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  scenario_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('通过','否决','补证')),
  reason_code TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL,
  rule_version TEXT NOT NULL DEFAULT '',
  observation_type TEXT NOT NULL DEFAULT '真实' CHECK (observation_type IN ('真实','模拟')),
  stage TEXT NOT NULL DEFAULT '',
  actual_qty_t REAL,
  actual_date TEXT NOT NULL DEFAULT '',
  actual_cost_cny REAL,
  quality_result TEXT NOT NULL DEFAULT '',
  return_breach TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  batch_id TEXT NOT NULL REFERENCES batches(batch_id),
  quantity_t REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','confirmed','released','cancelled','expired')),
  expires_at TEXT,
  run_id TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  event TEXT NOT NULL,
  entity TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL
);
"""

TS = "2026-09-06T10:00:00+0800"


def make_v4_db(path):
    """构造一个真实的 v4 老库（含用户/任务/供给/批次样本数据）。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(V4_SCHEMA_SQL)
    conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','4')")
    conn.execute("INSERT INTO users(username,password_hash,salt,role,active,created_at,updated_at)"
                 " VALUES('tester','h','s','admin',1,?,?)", (TS, TS))
    conn.execute("INSERT INTO tasks(id,case_id,demand_json,status,created_by,created_at,updated_at)"
                 " VALUES('T-1','case-1','{}','draft',1,?,?)", (TS, TS))
    supply_json = '{"supply_id":"D-S1","available_mass_t":8.0,"price_cny_per_t":4200.0}'
    conn.execute("INSERT INTO supplies(task_id,supply_id,batch_id,supply_json,created_at,updated_at)"
                 " VALUES('T-1','D-S1','D-S1',?,?,?)", (supply_json, TS, TS))
    conn.execute("INSERT INTO batches(batch_id,supply_json,original_mass_t,created_at,updated_at)"
                 " VALUES('D-S1',?,8.0,?,?)", (supply_json, TS, TS))
    conn.commit()
    conn.close()
    return supply_json


def table_columns(path, table):
    conn = sqlite3.connect(str(path))
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    conn.close()
    return cols


def meta_version(path):
    conn = sqlite3.connect(str(path))
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    conn.close()
    return row[0] if row else None


class V13DataModelMigration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_01_fresh_db_is_v5_with_new_fields(self):
        """全新库：直建 v5，新列/新表就位，关系三元组幂等可用。"""
        path = self.dir / "fresh.sqlite3"
        db = pilot_db.Database(path)
        self.assertEqual(meta_version(path), "5")
        self.assertIn("external_id", table_columns(path, "supplies"))
        self.assertIn("external_id", table_columns(path, "batches"))
        self.assertEqual(db.list_relations(), [])
        db.add_relation("91110000XXXXXXX001", "D-S1", "supplies")
        db.add_relation("91110000XXXXXXX001", "D-S1", "supplies")  # 幂等
        db.add_relation("D-S1", "D-S2", "substitutes")
        rows = db.list_relations()
        self.assertEqual(len(rows), 2)
        self.assertEqual(db.list_relations(from_id="91110000XXXXXXX001"),
                         [{"from_id": "91110000XXXXXXX001", "to_id": "D-S1",
                           "rel_type": "supplies", "created_at": rows[0]["created_at"]}])
        self.assertEqual(len(db.list_relations(rel_type="substitutes")), 1)
        db.close()

    def test_02_v4_database_upgrades_and_preserves_data(self):
        """v4 老库：打开即自动升级 v5，既有数据逐行保留，external_id 缺省空串。"""
        path = self.dir / "old.sqlite3"
        supply_json = make_v4_db(path)
        db = pilot_db.Database(path)
        self.assertEqual(meta_version(path), "5")
        self.assertIn("external_id", table_columns(path, "supplies"))
        self.assertIn("external_id", table_columns(path, "batches"))
        row = db.execute("SELECT * FROM supplies WHERE supply_id='D-S1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["supply_json"], supply_json)  # 数据逐字节保留
        self.assertEqual(row["external_id"], "")           # 新列缺省不污染
        batch = db.execute("SELECT * FROM batches WHERE batch_id='D-S1'").fetchone()
        self.assertEqual(batch["original_mass_t"], 8.0)
        self.assertEqual(batch["external_id"], "")
        db.add_relation("91110000XXXXXXX001", "D-S1", "supplies")
        self.assertEqual(len(db.list_relations()), 1)
        db.close()

    def test_03_repeated_migration_is_idempotent(self):
        """重复打开/重复迁移：版本稳定为 5，数据行数不增不减，relations 可继续使用。"""
        path = self.dir / "repeat.sqlite3"
        make_v4_db(path)
        counts = ("SELECT (SELECT COUNT(*) FROM supplies), (SELECT COUNT(*) FROM batches),"
                  " (SELECT COUNT(*) FROM relations)")
        for round_no in range(3):
            db = pilot_db.Database(path)
            self.assertEqual(meta_version(path), "5", f"round {round_no}")
            row = db.execute(counts).fetchone()
            self.assertEqual(tuple(row)[:2], (1, 1), f"round {round_no} 数据行数漂移")
            db.add_relation("X", "Y", "same_as")
            self.assertEqual(len(db.list_relations(rel_type="same_as")), 1)
            db.close()

    def test_04_rollback_drill_restore_then_reupgrade(self):
        """回滚演练：升级前文件级备份 → 打开升级 v5 → 写入增量 → 恢复回 v4 →
        再打开自动重新升级，v5 后增量（relations）随恢复消失，既有数据保留。"""
        import shutil
        path = self.dir / "drill.sqlite3"
        backup = self.dir / "drill-backup.sqlite3"
        make_v4_db(path)
        shutil.copyfile(path, backup)  # 升级前备份（v4 老状态）
        db = pilot_db.Database(path)   # 打开 → 自动升级
        self.assertEqual(meta_version(path), "5")
        db.add_relation("POST", "UPGRADE", "same_as")  # 升级后产生的增量数据
        db.restore_from(backup)        # 恢复到升级前
        self.assertEqual(meta_version(path), "4", "恢复后应回到 v4 备份状态")
        db.close()                    # restore 会重开连接；替换实例前显式释放文件句柄
        db = pilot_db.Database(path)   # 再打开：自动重新升级（可回滚、可重放）
        self.assertEqual(meta_version(path), "5")
        self.assertEqual(db.list_relations(), [])
        row = db.execute("SELECT supply_json FROM supplies WHERE supply_id='D-S1'").fetchone()
        self.assertIsNotNone(row)
        db.close()


class V13ExternalIdCompat(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write_csv(self, name, header, rows):
        path = self.dir / name
        lines = [",".join(header)]
        lines += [",".join(r) for r in rows]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        return path

    def test_05_old_csv_without_external_id_still_imports(self):
        """旧 CSV（无 external_id 列）：导入兼容，external_id 缺省空串，数值不变。"""
        path = self._write_csv("old.csv",
                               ["supply_id", "supplier_name", "material_code", "available_mass_t",
                                "price_cny_per_t", "distance_km", "yield_pct", "evidence_source",
                                "form", "color", "mfi_g_10min", "moisture_pct", "ash_pct",
                                "recycled_content_pct", "transport_mode"],
                               [["D-01", "老格式供应商", "PP", "8", "4200", "30", "100",
                                 "检测报告", "颗粒", "黑色", "12", "0.3", "3", "95", "公路货运"]])
        supplies, stats = load_supplies_csv(path)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(stats["rejected"], 0)
        self.assertEqual(supplies[0].external_id, "")
        self.assertEqual(supplies[0].available_mass_t, 8.0)
        self.assertEqual(supplies[0].price_cny_per_t, 4200.0)

    def test_06_new_csv_external_id_passthrough(self):
        """新 CSV（可选 external_id 列）：透传入库对象；其余字段不受影响。"""
        path = self._write_csv("new.csv",
                               ["supply_id", "supplier_name", "material_code", "available_mass_t",
                                "price_cny_per_t", "distance_km", "yield_pct", "evidence_source",
                                "form", "color", "mfi_g_10min", "moisture_pct", "ash_pct",
                                "recycled_content_pct", "transport_mode", "external_id"],
                               [["D-02", "新格式供应商", "PP", "7", "4000", "60", "100",
                                 "检测报告", "颗粒", "灰色", "10", "0.4", "4", "85", "公路货运",
                                 "91110000XXXXXXX002"]])
        supplies, stats = load_supplies_csv(path)
        self.assertEqual(stats["accepted"], 1)
        self.assertEqual(supplies[0].external_id, "91110000XXXXXXX002")
        self.assertEqual(supplies[0].available_mass_t, 7.0)

    def test_07_payload_roundtrip_stable(self):
        """payload 往返稳定：含/不含 external_id 的供给行，序列化-反序列化逐字段一致。"""
        base = {"supply_id": "D-03", "supplier_name": "往返供应商", "material_code": "PP",
                "available_mass_t": "5", "price_cny_per_t": "3800", "distance_km": "120",
                "evidence_source": "检测报告", "form": "颗粒", "color": "黑色",
                "mfi_g_10min": "11", "moisture_pct": "0.3", "ash_pct": "3.5",
                "recycled_content_pct": "90", "transport_mode": "公路货运",
                "yield_pct": "100"}
        with_ext = dict(base, external_id="91110000XXXXXXX003")
        s1 = supply_from_payload(with_ext)
        self.assertEqual(s1.external_id, "91110000XXXXXXX003")
        s1_back = supply_from_payload(supply_to_payload(s1))
        self.assertEqual(supply_to_payload(s1_back), supply_to_payload(s1))
        s2 = supply_from_payload(base)  # 不含 external_id：缺省空串，往返同样稳定
        self.assertEqual(s2.external_id, "")
        s2_back = supply_from_payload(supply_to_payload(s2))
        self.assertEqual(supply_to_payload(s2_back), supply_to_payload(s2))
        # 数值字段不受新列影响（演示案例逐位不变的结构性保证）
        self.assertEqual(s2.available_mass_t, 5.0)
        self.assertEqual(s2.price_cny_per_t, 3800.0)


if __name__ == "__main__":
    unittest.main()
