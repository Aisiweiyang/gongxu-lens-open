"""试点应用持久层：SQLite（stdlib sqlite3），每请求连接 + 事务管理。

事务与连接约束：
- 每个 HTTP 请求使用独立连接（thread-local），请求结束自动关闭；
  关闭时未提交的隐式事务由 SQLite 回滚——失败请求的残留写入绝不外泄给后续请求。
- 显式写入使用 transaction()（BEGIN IMMEDIATE + COMMIT/ROLLBACK）：整批写入原子生效，
  任意一行失败整体回滚。
- WAL + busy_timeout=5000：并发写互不污染，忙时等待而非立刻失败。
- 备份/恢复为管理操作：恢复只影响当前请求的连接（每请求连接隔离了影响面）。

选型理由：单组织独立部署、轻量决策服务场景下 SQLite 是最小而成熟的关系型
持久化方案；并发限制如实写入运维手册。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 5

SCHEMA_SQL = """
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
  external_id TEXT NOT NULL DEFAULT '',
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
  external_id TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  rel_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (from_id, to_id, rel_type)
);
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self._local = threading.local()
        conn = self._conn()
        self._migrate(conn)
        # v13 修正：迁移成功后把 meta.schema_version 盖章为当前版本（此前 OR IGNORE
        # 会让老库版本号永远停留在创建时的值，仅靠幂等迁移重跑掩盖）。
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),))
        conn.commit()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                   isolation_level=None)  # 自动事务，手动控制
            conn.row_factory = sqlite3.Row
            # Windows 下恢复（copyfile 覆盖主文件）后新建连接可能短暂遇文件锁：
            # WAL 失败时重试一次，再失败回退 DELETE 日志模式（功能等价，并发略降）。
            for attempt in (0, 1):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError:
                    if attempt == 0:
                        time.sleep(0.1)
                        continue
                    try:
                        conn.execute("PRAGMA journal_mode=DELETE")
                    except sqlite3.OperationalError:
                        pass
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    @contextmanager
    def request(self):
        """每请求连接：进入时建/复用线程本地连接，离开时关闭（未提交自动回滚）。"""
        conn = self._conn()
        try:
            yield conn
        finally:
            self._close_thread_conn()

    @contextmanager
    def transaction(self):
        """显式写事务：BEGIN IMMEDIATE + COMMIT/ROLLBACK（整批原子）。"""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._local.in_transaction = True
            yield conn
            self._local.in_transaction = False
            conn.execute("COMMIT")
        except BaseException:
            self._local.in_transaction = False
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _close_thread_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            del self._local.conn

    def close(self):
        self._close_thread_conn()

    def execute(self, sql, params=()):
        return self._conn().execute(sql, params)

    def commit(self):
        self._conn().commit()

    def _migrate(self, conn):
        """版本迁移（幂等）。v1 → v2：selections 幂等唯一索引。
        v2 → v3：reservations 从 (task, supply_id) 改为全局批次 (batch_id)
        库存模型；由既有 supplies 回填 batches 表；旧预留按 supply_id 映射迁移。
        v3 → v4：evidence_tasks 补 evidence_json（submitted 草稿持久化）。
        v4 → v5（v13 数据模型）：supplies/batches 增加可选 external_id（外部权威编号）；
        新增 relations 三元组表（from_id/to_id/rel_type，唯一索引幂等）。
        均为纯增量变更，不改动既有列与数据；回滚演练：升级前 backup_to，恢复后
        schema_version 回到旧值，下次打开自动重新升级（见 tests/test_v13_datamodel.py）。"""
        conn.executescript(SCHEMA_SQL)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        version = int(row["value"]) if row else 0
        if version < 2:
            conn.execute(
                "DELETE FROM selections WHERE id NOT IN ("
                "SELECT MIN(id) FROM selections GROUP BY task_id, run_id, option_id)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_selections_idempotent "
                "ON selections(task_id, run_id, option_id)")
            conn.commit()
        if version < 3:
            # 旧 reservations（含 supply_id 列、无 batch_id）→ 改名后按新结构重建
            cols = [r[1] for r in conn.execute("PRAGMA table_info(reservations)").fetchall()]
            if "batch_id" not in cols:
                conn.execute("ALTER TABLE reservations RENAME TO reservations_old")
                conn.execute("""
                    CREATE TABLE reservations (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      task_id TEXT NOT NULL REFERENCES tasks(id),
                      batch_id TEXT NOT NULL REFERENCES batches(batch_id),
                      quantity_t REAL NOT NULL,
                      status TEXT NOT NULL DEFAULT 'reserved'
                        CHECK (status IN ('reserved','confirmed','released','cancelled','expired')),
                      expires_at TEXT,
                      run_id TEXT NOT NULL DEFAULT '',
                      created_by INTEGER NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )""")
                conn.execute("""
                    INSERT INTO reservations(task_id,batch_id,quantity_t,status,expires_at,
                        run_id,created_by,created_at,updated_at)
                    SELECT task_id, supply_id, quantity_t, status, NULL, run_id, 1,
                           created_at, COALESCE(released_at, created_at)
                    FROM reservations_old""")
                conn.execute("DROP TABLE reservations_old")
            # 由既有 supplies 回填全局批次实体（不破坏既有数据）
            conn.execute("""
                INSERT OR IGNORE INTO batches(batch_id, supply_json, original_mass_t,
                                              created_at, updated_at)
                SELECT s.supply_id, s.supply_json,
                       COALESCE(CAST(json_extract(s.supply_json,'$.available_mass_t') AS REAL), 0),
                       s.created_at, s.updated_at
                FROM supplies s""")
            conn.execute("UPDATE supplies SET batch_id = supply_id WHERE batch_id = ''")
            conn.commit()
        if version < 4:
            # submitted 证据草稿必须持久化：页面刷新不得丢失已填写内容
            cols = [r[1] for r in conn.execute("PRAGMA table_info(evidence_tasks)").fetchall()]
            if "evidence_json" not in cols:
                conn.execute("ALTER TABLE evidence_tasks ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
        if version < 5:
            # v13 数据模型：外部权威编号（可选）+ 关系三元组单表（SC-DEX 结构借鉴）
            for table in ("supplies", "batches"):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                if "external_id" not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN external_id TEXT NOT NULL DEFAULT ''")
            # relations 表与唯一索引由 SCHEMA_SQL 的 CREATE IF NOT EXISTS 保证（幂等）
            conn.commit()

    def reset(self):
        """清空全部数据（仅测试/重置用）。"""
        conn = self._conn()
        for table in ("audit_log", "relations", "reservations", "feedback", "selections",
                      "runs", "evidence_versions", "evidence_tasks", "supplies", "tasks",
                      "batches", "sessions", "users", "meta"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()

    # —— 审计：普通业务角色不能直接修改（只在此层写入，API 层不可改） ——
    def audit(self, actor, event, entity, before=None, after=None):
        conn = self._conn()
        conn.execute(
            "INSERT INTO audit_log(actor,event,entity,before_json,after_json,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (actor, event, entity,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None,
             now()))
        # 事务内不提交（由外层 transaction 统一提交）；独立调用时立即提交
        if not getattr(self._local, "in_transaction", False):
            conn.commit()

    # —— 关系三元组（v13 数据模型：供需/替代等实体关系，单表承载） ——
    def add_relation(self, from_id, to_id, rel_type):
        """写入一条关系三元组（幂等：同一 (from_id, to_id, rel_type) 只存一条）。

        rel_type 建议词汇：supplies（供应商供给批次）/ substitutes（替代关系）/
        same_as（同一实体的外部编号映射）；允许按需扩展，不做 CHECK 强约束，
        词汇表治理见 docs/数据模型-v13.md。
        """
        conn = self._conn()
        conn.execute(
            "INSERT OR IGNORE INTO relations(from_id,to_id,rel_type,created_at)"
            " VALUES(?,?,?,?)",
            (str(from_id or "").strip(), str(to_id or "").strip(),
             str(rel_type or "").strip(), now()))
        if not getattr(self._local, "in_transaction", False):
            conn.commit()

    def list_relations(self, from_id=None, rel_type=None):
        """按方向/类型过滤读取关系三元组（均缺省返回全部，稳定排序）。"""
        sql = "SELECT from_id,to_id,rel_type,created_at FROM relations"
        conditions, params = [], []
        if from_id is not None:
            conditions.append("from_id=?")
            params.append(from_id)
        if rel_type is not None:
            conditions.append("rel_type=?")
            params.append(rel_type)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY from_id, rel_type, to_id"
        return [dict(r) for r in self._conn().execute(sql, params).fetchall()]

    # —— 备份 / 恢复 ——
    def backup_to(self, target):
        target = Path(target)
        backup = sqlite3.connect(str(target))
        with backup:
            self._conn().backup(backup)
        backup.close()
        return target

    def restore_from(self, source):
        import shutil
        source = Path(source)
        source_conn = sqlite3.connect(str(source))
        has_meta = source_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if not has_meta:
            source_conn.close()
            raise ValueError("备份文件无效：缺少 meta 表")
        source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source_conn.close()
        # 每请求连接模型：只关闭当前线程的连接；失败也必须重开
        # Windows 关键步骤：先把当前 WAL checkpoint 进主文件并截断——否则残留的
        # 旧 WAL 会在恢复后被新连接重放，覆盖恢复回来的数据（恢复一致性约束）。
        try:
            self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        self._close_thread_conn()
        try:
            # Windows 下文件可能被防病毒/索引器短暂锁定：带延迟重试（最多约 2 秒）
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                for attempt in range(10):
                    if not sidecar.exists():
                        break
                    try:
                        sidecar.unlink()
                        break
                    except PermissionError:
                        time.sleep(0.2)
            for attempt in range(10):
                try:
                    shutil.copyfile(source, self.path)
                    break
                except PermissionError:
                    time.sleep(0.2)
            else:
                raise PermissionError(f"无法覆盖数据库文件（被其他进程占用）：{self.path}")
        finally:
            self._conn()

    def row_to_dict(self, row):
        return dict(row) if row is not None else None
