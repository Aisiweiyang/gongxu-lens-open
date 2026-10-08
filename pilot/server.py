"""试点应用服务器：stdlib http.server + 路由 + 会话/CSRF + 角色权限 + SQLite。

设计约束：
- 轻量 Web/API + 静态前端 + 关系型持久化；不用原 serve.py 的开发静态服务承载业务。
- 静态服务限定允许资源范围（仅 pilot/static 白名单文件），不暴露项目根目录/源码。
- 写入接口：会话 + CSRF 双校验；服务端校验角色权限，不只隐藏按钮。
- 参数化查询、文件大小限制（导入 CSV ≤ 1 MB）、日志带 run_id/耗时、不含敏感内容。
- 登录尝试限速；密码无硬编码默认值（首次启动初始化管理员）。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import auth as authmod
from . import db as dbmod
from .api import (demand_from_wizard, demand_to_wizard, preflight_import,
                  run_engine, serialize_result, supply_from_payload,
                  supply_to_payload)
from src.validation import ValidationError

# —— v18 任务 3：大案例异步运行作业表 ——
# 进程内存态：不落库、服务重启即失（前端如实提示重新提交；语义决策见 docs/adr/）。
# 单守卫：同服务器同时只允许一个后台计算作业（防资源耗尽；大案例单发即分钟级 CPU）。
_ASYNC_JOBS = {}                 # job_id -> 作业状态字典
_ASYNC_LOCK = threading.Lock()
_ASYNC_SLOT = {"active": None}   # 当前在跑的 job_id（None=空闲）
_ASYNC_KEEP = 200                # 已结束作业的保留上限（防内存无界）

log = logging.getLogger("pilot")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
BACKUP_DIR = BASE_DIR / "backups"
DATA_DIR = BASE_DIR / "data"

# 静态资源白名单（路径限定，防目录遍历）
STATIC_ALLOWED = {"index.html", "workbench.html", "app.js", "style.css"}

MAX_BODY_BYTES = 1024 * 1024  # 1 MB

_login_attempts = {}  # ip → [timestamps]


def _rate_limited(ip):
    now_ts = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now_ts - t < 900]
    _login_attempts[ip] = attempts
    if len(attempts) >= 10:
        return True
    attempts.append(now_ts)
    return False


def make_server(db, host="127.0.0.1", port=8765):
    handler = _make_handler(db)
    return ThreadingHTTPServer((host, port), handler)


def _make_handler(database):
    class PilotHandler(BaseHTTPRequestHandler):
        server_version = "SupplyLensPilot/1.0"
        db = database

        # —— 基础 ——
        def log_message(self, fmt, *args):  # 请求日志：运行/错误上下文与耗时，不含敏感体
            log.info("request %s %s %s", self.command, self.path,
                     fmt % args if args else "")

        def _security_headers(self, full_csp=False):
            # 所有响应统一携带的安全头；full_csp 仅用于静态页（脚本/样式限定本源）
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            if full_csp:
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'self'; "
                                 "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                 "connect-src 'self'; base-uri 'none'; form-action 'self'; "
                                 "frame-ancestors 'none'")
            else:
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message, status=400):
            self._json({"error": message}, status)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise ValueError("请求体超过 1 MB 限制")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                # 拒绝 NaN/Infinity（Python json 默认接受，会击穿产能校验与引擎比较）
                return json.loads(raw.decode("utf-8"),
                                  parse_constant=lambda c: (_ for _ in ()).throw(
                                      ValueError(f"JSON 不允许 {c}")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("请求体不是合法 JSON")

        # —— 会话 / CSRF ——
        def _session(self):
            cookie = self.headers.get("Cookie") or ""
            match = re.search(r"(?:^|;\s*)pilot_session=([^;]+)", cookie)
            if not match:
                return None
            row = self.db.execute(
                "SELECT s.token, s.user_id, s.csrf_token, s.expires_at, u.username, u.role, u.active "
                "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                (match.group(1),)).fetchone()
            if row is None or not row["active"]:
                return None
            if row["expires_at"] < dbmod.now():
                self.db.execute("DELETE FROM sessions WHERE token=?", (row["token"],))
                self.db.commit()
                return None
            return row

        def _require(self, min_role, mutate=False):
            session = self._session()
            if session is None:
                self._error("未登录或会话已过期", 401)
                return None
            if not authmod.require_role(session["role"], min_role):
                self._error("权限不足", 403)
                return None
            if mutate:
                token = self.headers.get("X-CSRF-Token") or ""
                if not token or not secrets.compare_digest(token, session["csrf_token"]):
                    self._error("CSRF 校验失败", 403)
                    return None
            return session

        def _actor(self, session):
            return f"{session['username']}({session['role']})" if session else "anonymous"

        # —— 路由 ——
        def do_GET(self):
            self._route(mutate=False)

        def do_POST(self):
            self._route(mutate=True)

        def do_PUT(self):
            self._route(mutate=True)

        def do_DELETE(self):
            self._route(mutate=True)

        def _route(self, mutate):
            started = time.time()
            # 每请求独立连接；离开时自动关闭，未提交的隐式事务由 SQLite 回滚，
            # 失败请求的残留写入不会泄漏给后续请求。
            with self.db.request():
                try:
                    parsed = urlparse(self.path)
                    path = parsed.path
                    if path.startswith("/api/"):
                        self._route_api(path, mutate)
                    elif path in ("/", "/index.html", "/workbench.html"):
                        self._serve_static("workbench.html")
                    elif path.startswith("/static/"):
                        name = path[len("/static/"):]
                        if name not in STATIC_ALLOWED:
                            self._error("资源不在白名单内", 404)
                        else:
                            self._serve_static(name)
                    else:
                        self._error("未找到", 404)
                except ValidationError as exc:
                    self._json({"error": str(exc), "details": exc.details}, 400)
                except ValueError as exc:
                    self._error(str(exc), 400)
                except sqlite3.Error as exc:
                    log.exception("db error on %s", self.path)
                    self._error("数据库错误", 500)
                except Exception:
                    log.exception("unhandled error on %s", self.path)
                    self._error("服务器内部错误", 500)
                finally:
                    log.info("done %s %s in %.0fms", self.command, self.path,
                             (time.time() - started) * 1000)

        def _serve_static(self, name):
            path = (STATIC_DIR / name).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())):
                self._error("非法路径", 403)
                return
            if not path.exists():
                self._error("资源不存在", 404)
                return
            content = path.read_bytes()
            self.send_response(200)
            self._security_headers(full_csp=True)
            if name.endswith(".html"):
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif name.endswith(".js"):
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
            elif name.endswith(".css"):
                self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        # —— API ——
        def _route_api(self, path, mutate):
            method = self.command
            # 健康检查与初始化管理员（无需登录）
            if path == "/api/health" and method == "GET":
                users = self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
                self._json({"status": "ok", "schema_version": dbmod.SCHEMA_VERSION,
                            "users_exist": users > 0, "offline": True})
                return
            if path == "/api/auth/init" and method == "POST":
                self._api_auth_init()
                return
            if path == "/api/auth/login" and method == "POST":
                self._api_login()
                return
            session = self._require("viewer", mutate=mutate)
            if session is None:
                return
            if path == "/api/auth/me" and method == "GET":
                self._json({"username": session["username"], "role": session["role"]})
                return
            if path == "/api/auth/logout" and method == "POST":
                self.db.execute("DELETE FROM sessions WHERE token=?",
                                (session["token"],))
                self.db.commit()
                self._json({"ok": True})
                return
            # 角色权限集中校验：只读角色（viewer）没有任何写接口；
            # 业务写接口需要 editor，管理写接口在各自 handler 内再校验 admin。
            if mutate and not authmod.require_role(session["role"], "editor"):
                self._error("权限不足：只读账号不能执行写操作", 403)
                return
            # 任务
            if path == "/api/tasks" and method == "GET":
                self._api_list_tasks(session)
                return
            if path == "/api/tasks" and method == "POST":
                self._api_create_task(session)
                return
            m = re.match(r"^/api/tasks/([^/]+)$", path)
            if m:
                if method == "GET":
                    self._api_get_task(m.group(1), session)
                elif method == "PUT":
                    self._api_update_task(m.group(1), session)
                elif method == "DELETE":
                    self._api_delete_task(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/supplies$", path)
            if m:
                if method == "GET":
                    self._api_list_supplies(m.group(1), session)
                elif method == "POST":
                    self._api_add_supply(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/supplies/import$", path)
            if m and method == "POST":
                self._api_import_supplies(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/supplies/([^/]+)$", path)
            if m and method == "DELETE":
                self._api_delete_supply(m.group(1), m.group(2), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/runs$", path)
            if m and method == "POST":
                self._api_run_engine(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/runs$", path)
            if m and method == "GET":
                self._api_list_runs(m.group(1), session)
                return
            m = re.match(r"^/api/runs/jobs/([0-9a-f]{16})$", path)
            if m and method == "GET":
                self._api_job_status(m.group(1), session)
                return
            m = re.match(r"^/api/runs/([^/]+)$", path)
            if m and method == "GET":
                self._api_get_run(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/passport$", path)
            if m and method == "GET":
                self._api_passport(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/selections$", path)
            if m and method == "POST":
                self._api_select(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/selections$", path)
            if m and method == "GET":
                self._api_list_selections(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/evidence-tasks$", path)
            if m:
                if method == "GET":
                    self._api_list_evidence_tasks(m.group(1), session)
                elif method == "POST":
                    self._api_create_evidence_task(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/evidence-tasks/(\d+)$", path)
            if m and method == "PUT":
                self._api_update_evidence_task(m.group(1), int(m.group(2)), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/evidence-versions$", path)
            if m and method == "GET":
                self._api_list_evidence_versions(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/confirm$", path)
            if m and method == "POST":
                self._api_confirm(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/feedback$", path)
            if m:
                if method == "GET":
                    self._api_list_feedback(m.group(1), session)
                elif method == "POST":
                    self._api_add_feedback(m.group(1), session)
                return
            m = re.match(r"^/api/tasks/([^/]+)/reservations$", path)
            if m:
                if method == "POST":
                    self._api_reserve(m.group(1), session)
                elif method == "GET":
                    self._api_list_reservations(m.group(1), session)
                return
            m = re.match(r"^/api/reservations/(\d+)/(confirm|release|cancel)$", path)
            if m and method == "POST":
                self._api_transition_reservation(int(m.group(1)), m.group(2), session)
                return
            m = re.match(r"^/api/batches/([^/]+)/availability$", path)
            if m and method == "GET":
                self._api_batch_availability(m.group(1), session)
                return
            if path == "/api/matching/preview" and method == "POST":
                self._api_matching_preview(session)
                return
            if path == "/api/matching/sheet" and method == "POST":
                self._api_matching_sheet(session)
                return
            # 管理
            if path == "/api/users" and method == "GET":
                if not self._require_role_or_error(session, "admin"):
                    self._error("权限不足", 403)
                    return
                self._api_list_users(session)
                return
            if path == "/api/users" and method == "POST":
                self._api_create_user(session)
                return
            if path == "/api/backup" and method == "POST":
                self._api_backup(session)
                return
            if path == "/api/backups" and method == "GET":
                self._api_list_backups(session)
                return
            m = re.match(r"^/api/backups/([^/]+)/restore$", path)
            if m and method == "POST":
                self._api_restore(m.group(1), session)
                return
            if path == "/api/audit" and method == "GET":
                self._api_audit(session)
                return
            self._error("接口不存在", 404)

        def _require_role_or_error(self, session, role):
            return authmod.require_role(session["role"], role)

        # —— 认证 ——
        def _api_auth_init(self):
            body = self._read_body()
            count = self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            if count > 0:
                self._error("初始化管理员仅允许在无任何账号时执行", 403)
                return
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or "")
            if not username:
                self._error("用户名必填", 400)
                return
            policy_error = authmod.password_policy_error(password)
            if policy_error:
                self._error(policy_error, 400)
                return
            digest, salt = authmod.hash_password(password)
            self.db.execute(
                "INSERT INTO users(username,password_hash,salt,role,active,created_at,updated_at)"
                " VALUES(?,?,?,?,1,?,?)",
                (username, digest, salt, "admin", dbmod.now(), dbmod.now()))
            self.db.commit()
            self.db.audit("system", "auth.init_admin", f"user:{username}")
            self._json({"ok": True, "username": username})

        def _api_login(self):
            ip = self.client_address[0]
            if _rate_limited(ip):
                self._error("登录尝试过于频繁，请稍后再试", 429)
                return
            body = self._read_body()
            username = str(body.get("username") or "")
            password = str(body.get("password") or "")
            row = self.db.execute(
                "SELECT id, username, password_hash, salt, role, active FROM users WHERE username=?",
                (username,)).fetchone()
            if row is None or not row["active"]:
                # 恒定工作量：无论用户名是否存在都执行一次口令推导，避免时序探测账号
                authmod.hash_password(password)
                self._error("用户名或密码错误", 401)
                return
            if not authmod.verify_password(password, row["salt"], row["password_hash"]):
                self._error("用户名或密码错误", 401)
                return
            token = authmod.new_session_token()
            csrf = authmod.new_csrf_token()
            self.db.execute(
                "INSERT INTO sessions(token,user_id,csrf_token,created_at,expires_at)"
                " VALUES(?,?,?,?,?)",
                (token, row["id"], csrf, dbmod.now(), authmod.session_expiry()))
            self.db.commit()
            self.db.audit(username, "auth.login", f"user:{username}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie",
                             f"pilot_session={token}; Path=/; HttpOnly; SameSite=Strict{secure}")
            body_out = json.dumps({"ok": True, "csrf": csrf,
                                   "username": username, "role": row["role"]},
                                  ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)

        # —— 任务 CRUD ——
        def _api_list_tasks(self, session):
            rows = self.db.execute(
                "SELECT t.id, t.case_id, t.demand_json, t.status, t.created_at,"
                " t.updated_at,"
                " (SELECT COUNT(*) FROM supplies s WHERE s.task_id=t.id) AS supply_count"
                " FROM tasks t ORDER BY t.updated_at DESC").fetchall()
            self._json({"tasks": [
                {"id": r["id"], "case_id": r["case_id"], "status": r["status"],
                 "demand": json.loads(r["demand_json"]),
                 "supply_count": r["supply_count"],
                 "created_at": r["created_at"], "updated_at": r["updated_at"]}
                for r in rows]})

        def _api_create_task(self, session):
            body = self._read_body()
            demand = demand_from_wizard(body.get("demand") or {})
            task_id = str(body.get("task_id") or demand.demand_id)
            case_id = str(body.get("case_id") or demand.demand_id)
            payload = json.dumps(demand_to_wizard(demand), ensure_ascii=False)
            self.db.execute(
                "INSERT INTO tasks(id,case_id,demand_json,status,created_by,created_at,updated_at)"
                " VALUES(?,?,?,'draft',?,?,?)",
                (task_id, case_id, payload, session["user_id"], dbmod.now(), dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "task.create", f"task:{task_id}")
            self._json({"ok": True, "task_id": task_id}, 201)

        def _api_get_task(self, task_id, session):
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                self._error("任务不存在", 404)
                return
            supplies = self.db.execute(
                "SELECT * FROM supplies WHERE task_id=? ORDER BY supply_id", (task_id,)).fetchall()
            runs = self.db.execute(
                "SELECT id, scenario_id, created_at, config_sha256 FROM runs"
                " WHERE task_id=? ORDER BY created_at DESC", (task_id,)).fetchall()
            self._json({"task": dict(row),
                        "demand": json.loads(row["demand_json"]),
                        "supplies": [json.loads(s["supply_json"]) for s in supplies],
                        "runs": [dict(r) for r in runs]})

        def _api_update_task(self, task_id, session):
            body = self._read_body()
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                self._error("任务不存在", 404)
                return
            before = json.loads(row["demand_json"])
            demand = demand_from_wizard(body.get("demand") or {})
            payload = json.dumps(demand_to_wizard(demand), ensure_ascii=False)
            self.db.execute("UPDATE tasks SET demand_json=?, updated_at=? WHERE id=?",
                            (payload, dbmod.now(), task_id))
            self.db.commit()
            self.db.audit(self._actor(session), "task.update", f"task:{task_id}",
                          before=before, after=json.loads(payload))
            self._json({"ok": True})

        def _api_delete_task(self, task_id, session):
            if not self._require_role_or_error(session, "editor"):
                self._error("权限不足", 403)
                return
            row = self.db.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                self._error("任务不存在", 404)
                return
            self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self.db.commit()
            self.db.audit(self._actor(session), "task.delete", f"task:{task_id}")
            self._json({"ok": True})

        # —— 供给 ——
        def _api_list_supplies(self, task_id, session):
            rows = self.db.execute(
                "SELECT * FROM supplies WHERE task_id=? ORDER BY supply_id", (task_id,)).fetchall()
            self._json({"supplies": [json.loads(r["supply_json"]) for r in rows]})

        def _upsert_batch(self, supply):
            """把供给行同步为全局批次实体（原始可供量=声明可供量；占用另计）。"""
            payload = json.dumps(supply_to_payload(supply), ensure_ascii=False)
            self.db.execute(
                "INSERT INTO batches(batch_id,supply_json,original_mass_t,external_id,"
                "created_at,updated_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(batch_id) DO UPDATE SET "
                "supply_json=excluded.supply_json,"
                "original_mass_t=excluded.original_mass_t,"
                "external_id=excluded.external_id,updated_at=excluded.updated_at",
                (supply.supply_id, payload, supply.available_mass_t or 0.0,
                 supply.external_id or "", dbmod.now(), dbmod.now()))

        def _api_add_supply(self, task_id, session):
            body = self._read_body()
            supply = supply_from_payload(body.get("supply") or {})
            payload = json.dumps(supply_to_payload(supply), ensure_ascii=False)
            # 幂等策略：同 supply_id 重复提交 = 覆盖，不产生重复行
            self.db.execute(
                "INSERT OR REPLACE INTO supplies(task_id,supply_id,batch_id,"
                "supply_json,external_id,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (task_id, supply.supply_id, supply.supply_id, payload,
                 supply.external_id or "", dbmod.now(), dbmod.now()))
            # 同步全局批次库存实体（任务只引用批次，不复制彼此无关的库存真相）
            self._upsert_batch(supply)
            self.db.commit()
            self.db.audit(self._actor(session), "supply.add", f"task:{task_id}/{supply.supply_id}")
            self._json({"ok": True, "supply_id": supply.supply_id}, 201)

        def _api_import_supplies(self, task_id, session):
            body = self._read_body()
            csv_text = str(body.get("csv_text") or "")
            if not csv_text.strip():
                self._error("csv_text 为空", 400)
                return
            task_row = self.db.execute("SELECT demand_json FROM tasks WHERE id=?",
                                       (task_id,)).fetchone()
            if task_row is None:
                self._error("任务不存在", 404)
                return
            demand_payload = json.loads(task_row["demand_json"])
            preview = preflight_import(demand_payload, csv_text, "supply")
            commit = bool(body.get("commit"))
            if not commit:
                # 预览模式：返回行级问题，不写入
                self._json({"ok": True, "preview": preview,
                            "can_commit": len(preview["structural"]) == 0})
                return
            if preview["structural"]:
                self._error("存在结构错误，拒绝导入（修正后重试）："
                            + "；".join(i["problem"] for i in preview["structural"][:10]), 400)
                return
            # 提交：整批先用正式导入同一套解析/校验（supply_from_payload，含千分位），
            # 全部通过后才在单个事务内写入；任意一行失败整体回滚。
            import csv as _csv
            import io
            parsed_rows = []
            row_errors = []
            for line_no, row in enumerate(_csv.DictReader(io.StringIO(csv_text)), 2):
                try:
                    supply = supply_from_payload(row)
                    parsed_rows.append(supply)
                except ValidationError as exc:
                    row_errors.append({"row": line_no, "error": str(exc),
                                       "details": exc.details})
            if row_errors:
                self._json({"error": "导入失败：存在无法解析的行，整批未写入（已回滚）",
                            "details": row_errors}, 400)
                return
            written = 0
            with self.db.transaction():
                for supply in parsed_rows:
                    payload = json.dumps(supply_to_payload(supply), ensure_ascii=False)
                    self.db.execute(
                        "INSERT OR REPLACE INTO supplies(task_id,supply_id,batch_id,"
                        "supply_json,external_id,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (task_id, supply.supply_id, supply.supply_id, payload,
                         supply.external_id or "", dbmod.now(), dbmod.now()))
                    # 同一事务内同步全局批次（整批原子）
                    self.db.execute(
                        "INSERT INTO batches(batch_id,supply_json,original_mass_t,external_id,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(batch_id) DO UPDATE SET "
                        "supply_json=excluded.supply_json,"
                        "original_mass_t=excluded.original_mass_t,"
                        "external_id=excluded.external_id,updated_at=excluded.updated_at",
                        (supply.supply_id, payload, supply.available_mass_t or 0.0,
                         supply.external_id or "", dbmod.now(), dbmod.now()))
                    written += 1
            self.db.audit(self._actor(session), "supply.import",
                          f"task:{task_id}", after={"written": written})
            self._json({"ok": True, "written": written,
                        "business_issues": preview["business"]})

        def _api_delete_supply(self, task_id, supply_id, session):
            self.db.execute("DELETE FROM supplies WHERE task_id=? AND supply_id=?",
                            (task_id, supply_id))
            self.db.commit()
            self.db.audit(self._actor(session), "supply.delete", f"task:{task_id}/{supply_id}")
            self._json({"ok": True})

        # —— 计算运行（快照） ——
        def _api_run_engine(self, task_id, session):
            task_row, supply_rows, err, status = self._check_run_inputs(task_id)
            if err:
                self._error(err, status)
                return
            # v18 任务 3：?async=1 走后台计算（202 + 作业轮询）；缺省同步语义逐位不变
            if "async=1" in (urlparse(self.path).query or ""):
                self._start_async_run(task_id, session, task_row, supply_rows)
                return
            run_id, elapsed_ms = self._execute_run(task_id, session,
                                                   task_row, supply_rows)
            self._json({"ok": True, "run_id": run_id, "elapsed_ms": elapsed_ms}, 201)

        def _check_run_inputs(self, task_id):
            """运行输入校验（同步/异步共用口径）；返回 (task_row, supply_rows, err, status)。"""
            task_row = self.db.execute("SELECT * FROM tasks WHERE id=?",
                                       (task_id,)).fetchone()
            if task_row is None:
                return None, None, "任务不存在", 404
            supply_rows = self.db.execute(
                "SELECT * FROM supplies WHERE task_id=?", (task_id,)).fetchall()
            if not supply_rows:
                return None, None, "该任务尚无供给批次，先录入/导入供给", 400
            return task_row, supply_rows, None, None

        def _execute_run(self, task_id, session, task_row, supply_rows):
            """执行引擎并落运行快照（同步/异步共用同一段代码，输出逐位一致）。

            返回 (run_id, elapsed_ms)。必须在持有 self.db.request() 连接的线程内调用
            （异步路径由后台 worker 自行开启连接上下文）。"""
            from src.material import MaterialDemand, MaterialSupply
            demand = demand_from_wizard(json.loads(task_row["demand_json"]))
            # 推荐计算必须使用实时全局可用量（扣除其他任务的有效预留与确认占用量）
            # 用 supply_from_payload 重建（过滤 _evidence_version 等内部键 + 统一校验）
            supplies = []
            availability_map = {}
            for row in supply_rows:
                supply = supply_from_payload(json.loads(row["supply_json"]))
                batch_id = row["batch_id"] or supply.supply_id
                declared = supply.available_mass_t
                available = self._batch_available(batch_id)
                if declared is None or available < declared:
                    supply.available_mass_t = available  # 实时可用量覆盖声明量
                supplies.append(supply)
                availability_map[supply.supply_id] = {
                    "batch_id": batch_id, "declared_mass_t": declared,
                    "available_mass_t": available}
            run_id = uuid.uuid4().hex
            started = time.time()
            result, factor_data = run_engine(demand, supplies)
            elapsed_ms = (time.time() - started) * 1000
            payload = json.dumps(serialize_result(result), ensure_ascii=False)
            config_sha = __import__("src.passport", fromlist=["sha256_file"]).sha256_file(
                Path(__file__).resolve().parent.parent / "config" / "material_emission_factors.yaml")
            # 冻结完整输入快照（需求/供给/库存/因子配置内容/算法版本/时间/操作者）
            factor_content = (Path(__file__).resolve().parent.parent
                              / "config" / "material_emission_factors.yaml").read_text(encoding="utf-8")
            input_snapshot = {
                "demand": json.loads(task_row["demand_json"]),
                "supplies": [json.loads(r["supply_json"]) for r in supply_rows],
                "availability": availability_map,
                "factor_config_sha256": config_sha,
                "factor_config_content": factor_content,
                "algorithm_version": result.get("algorithm_version"),
                "state_hash": self._current_task_state_hash(task_id),
                "created_by": session["user_id"],
                "created_at": dbmod.now(),
            }
            self.db.execute(
                "INSERT INTO runs(id,task_id,scenario_id,result_json,input_snapshot_json,"
                "config_sha256,created_by,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (run_id, task_id, "local", payload,
                 json.dumps(input_snapshot, ensure_ascii=False), config_sha,
                 session["user_id"], dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "run.create", f"task:{task_id}/{run_id}",
                          after={"elapsed_ms": round(elapsed_ms, 1),
                                 "config_sha256": config_sha[:16]})
            log.info("engine run %s on task %s in %.0fms", run_id, task_id, elapsed_ms)
            return run_id, round(elapsed_ms, 1)

        # —— v18 任务 3：大案例后台计算（?async=1；作业内存态，重启即失） ——
        def _start_async_run(self, task_id, session, task_row, supply_rows):
            job_id = secrets.token_hex(8)
            with _ASYNC_LOCK:
                if _ASYNC_SLOT["active"] is not None:
                    self._error("已有大案例后台计算进行中，请等待完成后再提交"
                                "（同服务器同时只允许一个后台计算任务）", 409)
                    return
                _ASYNC_SLOT["active"] = job_id
                _ASYNC_JOBS[job_id] = {
                    "job_id": job_id, "task_id": task_id, "status": "queued",
                    "run_id": None, "error": None, "elapsed_ms": None,
                    "created_at": time.time(), "started_at": None,
                    "finished_at": None}
            self._prune_async_jobs()
            self.db.audit(self._actor(session), "run.async",
                          f"task:{task_id}/{job_id}", after={"accepted": True})

            def _worker():
                job = _ASYNC_JOBS.get(job_id)
                if job is None:  # 极端：受理后被清理（不应发生）
                    with _ASYNC_LOCK:
                        if _ASYNC_SLOT["active"] == job_id:
                            _ASYNC_SLOT["active"] = None
                    return
                job["status"] = "running"
                job["started_at"] = time.time()
                try:
                    with self.db.request():  # 后台线程独立连接（threading.local）
                        fresh_task, fresh_supplies, err, _status = \
                            self._check_run_inputs(task_id)
                        if err:
                            raise ValueError(err)
                        run_id, elapsed_ms = self._execute_run(
                            task_id, session, fresh_task, fresh_supplies)
                    job["run_id"] = run_id
                    job["elapsed_ms"] = elapsed_ms
                    job["status"] = "done"
                    log.info("async job %s done: run %s in %.0fms",
                             job_id, run_id, elapsed_ms)
                except Exception as exc:  # 失败如实呈现，不伪造完成
                    job["status"] = "failed"
                    job["error"] = str(exc) or exc.__class__.__name__
                    log.exception("async job %s failed on task %s", job_id, task_id)
                finally:
                    job["finished_at"] = time.time()
                    with _ASYNC_LOCK:
                        if _ASYNC_SLOT["active"] == job_id:
                            _ASYNC_SLOT["active"] = None

            threading.Thread(target=_worker, name=f"async-run-{job_id}",
                             daemon=True).start()
            self._json({
                "ok": True, "job_id": job_id, "status": "queued", "task_id": task_id,
                "note": "后台计算已受理：大案例预计十几分钟。轮询 GET /api/runs/jobs/"
                        + job_id + " 获取状态；完成凭 run_id 读取结果。"
                        "作业为进程内存态，服务重启即丢失（需重新提交）。"},
                202)

        def _api_job_status(self, job_id, session):
            job = _ASYNC_JOBS.get(job_id)
            if job is None:
                self._error("作业不存在（已被清理或服务重启）", 404)
                return
            payload = dict(job)
            payload["elapsed_s"] = round(
                (job["finished_at"] or time.time())
                - (job["started_at"] or job["created_at"]), 1)
            self._json(payload)

        def _prune_async_jobs(self, keep=None):
            keep = _ASYNC_KEEP if keep is None else keep
            with _ASYNC_LOCK:
                if len(_ASYNC_JOBS) <= keep:
                    return
                finished = sorted(
                    (j["finished_at"] or j["created_at"], jid)
                    for jid, j in _ASYNC_JOBS.items()
                    if j["status"] in ("done", "failed"))
                for _t, jid in finished[:len(_ASYNC_JOBS) - keep]:
                    _ASYNC_JOBS.pop(jid, None)

        def _api_list_runs(self, task_id, session):
            rows = self.db.execute(
                "SELECT id, scenario_id, created_at, config_sha256, created_by FROM runs"
                " WHERE task_id=? ORDER BY created_at DESC", (task_id,)).fetchall()
            self._json({"runs": [dict(r) for r in rows]})

        def _api_get_run(self, run_id, session):
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                self._error("运行快照不存在", 404)
                return
            result = json.loads(row["result_json"])
            self._json({"run": {"id": row["id"], "task_id": row["task_id"],
                                "scenario_id": row["scenario_id"],
                                "config_sha256": row["config_sha256"],
                                "created_at": row["created_at"]},
                        "result": result})

        def _api_passport(self, task_id, session):
            from src.passport import build_passport
            from urllib.parse import parse_qs
            run_id = (parse_qs(urlparse(self.path).query).get("run_id") or [None])[0]
            if run_id:
                row = self.db.execute("SELECT * FROM runs WHERE id=? AND task_id=?",
                                      (run_id, task_id)).fetchone()
                if row is None:
                    self._error("运行快照不存在", 404)
                    return
                result = json.loads(row["result_json"])
                # 序列化快照 → 引擎对象（护照需要 dataclass 字段）
                from src.material import MaterialDemand, MaterialSupply
                demand = demand_from_wizard(result["demand"])
                supplies = [supply_from_payload(s) for s in result["supplies"]]
                result["demand"] = demand
                result["supplies"] = supplies
                snapshot = json.loads(row["input_snapshot_json"] or "{}")
                document = build_passport(
                    result, run_id=run_id, case_id=task_id,
                    scenario_id=row["scenario_id"],
                    input_hashes={
                        "demand_file": None, "supply_file": None,
                        "factor_file": snapshot.get("factor_config_sha256")})
            else:
                task_row = self.db.execute("SELECT * FROM tasks WHERE id=?",
                                           (task_id,)).fetchone()
                if task_row is None:
                    self._error("任务不存在", 404)
                    return
                supply_rows = self.db.execute(
                    "SELECT supply_json FROM supplies WHERE task_id=?", (task_id,)).fetchall()
                demand = demand_from_wizard(json.loads(task_row["demand_json"]))
                supplies = [supply_from_payload(json.loads(r["supply_json"]))
                            for r in supply_rows]
                result, _ = run_engine(demand, supplies)
                document = build_passport(result, case_id=task_id, scenario_id="local")
            body = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="passport_{task_id}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # —— 选定方案 / 证据任务 / 反馈 / 预留 ——
        def _api_select(self, task_id, session):
            body = self._read_body()
            run_id = str(body.get("run_id") or "")
            option_id = str(body.get("option_id") or "")
            reason = str(body.get("reason") or "")
            if not run_id or not option_id:
                self._error("run_id 与 option_id 必填", 400)
                return
            row = self.db.execute("SELECT id FROM runs WHERE id=? AND task_id=?",
                                  (run_id, task_id)).fetchone()
            if row is None:
                self._error("运行快照不存在", 404)
                return
            result = json.loads(self.db.execute(
                "SELECT result_json FROM runs WHERE id=?", (run_id,)).fetchone()["result_json"])
            valid = {o["option_id"] for o in result.get("options") or []}
            if option_id not in valid:
                self._error("option_id 不在该运行快照的方案集内", 400)
                return
            # 同一 (task, run, option) 幂等，重复点击不产生重复行
            self.db.execute(
                "INSERT OR IGNORE INTO selections(task_id,run_id,option_id,reason,selected_by,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (task_id, run_id, option_id, reason, session["user_id"], dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "option.select", f"task:{task_id}",
                          after={"run_id": run_id, "option_id": option_id})
            self._json({"ok": True}, 201)

        def _api_list_selections(self, task_id, session):
            rows = self.db.execute(
                "SELECT s.*, u.username FROM selections s JOIN users u ON u.id=s.selected_by"
                " WHERE s.task_id=? ORDER BY s.created_at DESC", (task_id,)).fetchall()
            self._json({"selections": [dict(r) for r in rows]})

        def _api_list_evidence_tasks(self, task_id, session):
            rows = self.db.execute(
                "SELECT * FROM evidence_tasks WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
            self._json({"evidence_tasks": [dict(r) for r in rows]})

        def _api_create_evidence_task(self, task_id, session):
            body = self._read_body()
            self.db.execute(
                "INSERT INTO evidence_tasks(task_id,action_id,supply_id,assignee,due_date,"
                "status,evidence_ref,verdict_reason,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'open','','',?,?)",
                (task_id, str(body.get("action_id") or ""), str(body.get("supply_id") or ""),
                 str(body.get("assignee") or ""), str(body.get("due_date") or ""),
                 dbmod.now(), dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "evidence_task.create", f"task:{task_id}")
            self._json({"ok": True}, 201)

        def _api_update_evidence_task(self, task_id, et_id, session):
            """证据任务状态机：open → submitted → passed/rejected → resubmit。

            submitted 必须携带结构化证据（类型/来源/机构/检测日期/有效期/关键实测字段）；
            passed 仅在必填字段完整且业务校验通过时产生，生成不可静默覆盖的证据版本，
            并把实测字段写回供应批次快照后引导重算；rejected 保持/强化门槛。
            """
            body = self._read_body()
            status = str(body.get("status") or "")
            if status not in ("open", "submitted", "passed", "rejected", "resubmit", "closed"):
                self._error("status 非法", 400)
                return
            row = self.db.execute(
                "SELECT * FROM evidence_tasks WHERE id=? AND task_id=?",
                (et_id, task_id)).fetchone()
            if row is None:
                self._error("证据任务不存在", 404)
                return
            transition_ok = {
                "open": ("open", "resubmit", "rejected"),
                "submitted": ("open", "resubmit"),
                "passed": ("submitted",),
                "rejected": ("submitted",),
                "resubmit": ("rejected", "open"),
                "closed": ("open", "rejected"),
            }[status]
            if row["status"] not in transition_ok:
                self._error(f"状态 {row['status']} 不允许流转到 {status}", 409)
                return
            evidence = body.get("evidence") or {}
            if status == "passed":
                # passed 前置：结构化证据必填字段完整 + 实测字段通过业务校验
                problems = self._validate_evidence_submission(evidence)
                if problems:
                    self._json({"error": "证据不完整，不能通过审核",
                                "details": problems}, 400)
                    return
            try:
                with self.db.transaction():
                    self.db.execute(
                        "UPDATE evidence_tasks SET status=?, evidence_ref=?, evidence_json=?, "
                        "verdict_reason=?, updated_at=? WHERE id=? AND task_id=?",
                        (status,
                         str(evidence.get("source_ref") or body.get("evidence_ref") or ""),
                         json.dumps(evidence, ensure_ascii=False, default=str),
                         str(body.get("verdict_reason") or ""), dbmod.now(), et_id, task_id))
                    if status == "passed":
                        self._create_evidence_version(task_id, row["supply_id"],
                                                      evidence, session)
            except sqlite3.Error:
                log.exception("evidence update failed")
                self._error("数据库错误", 500)
                return
            self.db.audit(self._actor(session), f"evidence_task.{status}",
                          f"task:{task_id}/{et_id}",
                          before={"status": row["status"]}, after={"status": status})
            self._json({"ok": True, "status": status,
                        "note": ("证据版本已生成并写入供应批次快照，请重新运行计算以应用新证据"
                                 if status == "passed" else "")})

        def _validate_evidence_submission(self, evidence):
            """passed 前置校验：必填字段完整 + 关键实测字段业务校验（复用统一校验层）。"""
            from src.validation import parse_date, parse_number
            problems = []
            for field, label in (("evidence_type", "证据类型"), ("source_ref", "来源/附件引用"),
                                 ("issuing_body", "出具机构"), ("measured_date", "检测/核验日期")):
                if not str(evidence.get(field) or "").strip():
                    problems.append({"field": field, "problem": f"缺少必填字段 {label}",
                                     "suggestion": f"填写 {label}"})
            measured_date, err = parse_date(evidence.get("measured_date"), "measured_date")
            if err:
                problems.append(err)
            valid_until = evidence.get("valid_until")
            if valid_until:
                _, err = parse_date(valid_until, "valid_until")
                if err:
                    problems.append(err)
            fields = evidence.get("measured_fields") or {}
            if not isinstance(fields, dict) or not fields:
                problems.append({"field": "measured_fields",
                                 "problem": "缺少关键实测字段（熔指/水分/灰分/再生含量等）",
                                 "suggestion": "填写检测报告中的实测字段"})
            for name, value in (fields or {}).items():
                if name not in ("mfi_g_10min", "moisture_pct", "ash_pct",
                                "recycled_content_pct"):
                    problems.append({"field": f"measured_fields.{name}",
                                     "problem": f"不支持的实测字段 {name}",
                                     "suggestion": "使用 mfi_g_10min/moisture_pct/ash_pct/recycled_content_pct"})
                    continue
                parsed, err = parse_number(value, f"measured_fields.{name}")
                if err:
                    problems.append(err)
                elif parsed is not None and not (0 <= parsed <= (100.0 if name != "mfi_g_10min" else 1000.0)):
                    problems.append({"field": f"measured_fields.{name}",
                                     "problem": f"实测值超出合理范围：{parsed}",
                                     "suggestion": "核对检测报告数值"})
            if measured_date is None and not problems:
                problems.append({"field": "measured_date",
                                 "problem": "检测日期缺失",
                                 "suggestion": "填写检测/核验日期"})
            return problems

        def _create_evidence_version(self, task_id, supply_id, evidence, session):
            """passed → 生成不可静默覆盖的证据版本 + 写回供应批次快照。"""
            latest = self.db.execute(
                "SELECT COALESCE(MAX(version_no),0) AS v FROM evidence_versions "
                "WHERE task_id=? AND supply_id=?", (task_id, supply_id)).fetchone()["v"]
            version_no = latest + 1
            self.db.execute(
                "UPDATE evidence_versions SET status='superseded' "
                "WHERE task_id=? AND supply_id=? AND status='passed'",
                (task_id, supply_id))
            self.db.execute(
                "INSERT INTO evidence_versions(task_id,supply_id,version_no,evidence_type,"
                "source_ref,attachment_ref,issuing_body,measured_date,valid_until,"
                "measured_fields_json,submitted_by,reviewed_by,reviewed_at,status,"
                "reject_reason,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'','passed','',?)",
                (task_id, supply_id, version_no,
                 str(evidence.get("evidence_type") or ""),
                 str(evidence.get("source_ref") or ""),
                 str(evidence.get("attachment_ref") or ""),
                 str(evidence.get("issuing_body") or ""),
                 str(evidence.get("measured_date") or ""),
                 str(evidence.get("valid_until") or ""),
                 json.dumps(evidence.get("measured_fields") or {}, ensure_ascii=False),
                 session["user_id"], session["user_id"], dbmod.now()))
            # 写回供应批次快照：证据来源/日期与实测字段（只覆盖补证覆盖的字段）
            supply_row = self.db.execute(
                "SELECT * FROM supplies WHERE task_id=? AND supply_id=?",
                (task_id, supply_id)).fetchone()
            if supply_row is not None:
                supply_json = json.loads(supply_row["supply_json"])
                supply_json["evidence_source"] = (
                    f"{evidence.get('issuing_body', '')} {evidence.get('evidence_type', '')}"
                    .strip() or supply_json.get("evidence_source", ""))
                supply_json["evidence_date"] = evidence.get("measured_date") or ""
                supply_json["evidence_attachment"] = evidence.get("attachment_ref") or ""
                for name, value in (evidence.get("measured_fields") or {}).items():
                    supply_json[name] = value
                supply_json["_evidence_version"] = version_no
                self.db.execute(
                    "UPDATE supplies SET supply_json=?, updated_at=? WHERE task_id=? AND supply_id=?",
                    (json.dumps(supply_json, ensure_ascii=False), dbmod.now(),
                     task_id, supply_id))
            self.db.audit(self._actor(session), "evidence_version.passed",
                          f"task:{task_id}/{supply_id}", after={"version_no": version_no})

        def _api_list_evidence_versions(self, task_id, session):
            """历史证据版本可回放（含被替代版本）。"""
            rows = self.db.execute(
                "SELECT * FROM evidence_versions WHERE task_id=? ORDER BY version_no DESC",
                (task_id,)).fetchall()
            self._json({"evidence_versions": [dict(r) for r in rows]})

        def _api_confirm(self, task_id, session):
            """最终采购确认与「比较」分离。确认前服务端新鲜度校验：
            价格、库存、交期、证据或规则任一版本变化 → 409 阻止直接确认并引导重算。
            确认成功 = 对方案分配的每个批次创建 confirmed 预留（原子、校验实时可用量）。"""
            body = self._read_body()
            run_id = str(body.get("run_id") or "")
            option_id = str(body.get("option_id") or "")
            reason = str(body.get("reason") or "")
            if not run_id or not option_id:
                self._error("run_id 与 option_id 必填", 400)
                return
            run = self.db.execute("SELECT * FROM runs WHERE id=? AND task_id=?",
                                  (run_id, task_id)).fetchone()
            if run is None:
                self._error("运行快照不存在", 404)
                return
            result = json.loads(run["result_json"])
            option = next((o for o in result.get("options") or []
                           if o.get("option_id") == option_id), None)
            if option is None:
                self._error("option_id 不在该运行快照的方案集内", 400)
                return
            # 新鲜度：当前任务状态哈希 vs 运行冻结快照
            current_state = self._current_task_state_hash(task_id)
            snapshot = json.loads(run["input_snapshot_json"] or "{}")
            if snapshot.get("state_hash") != current_state:
                self._json({"error": "运行快照已过期：需求/供给/证据/库存或规则配置已变化，"
                                     "请重新运行计算后再确认（服务端强制，不能只靠前端提示）",
                            "details": {"run_state_hash": snapshot.get("state_hash"),
                                        "current_state_hash": current_state}}, 409)
                return
            allocation = option.get("allocation") or {}
            if not allocation:
                self._error("基准方案不可确认（请选择实际采购方案）", 400)
                return
            try:
                with self.db.transaction():
                    for supply_id, mass in allocation.items():
                        batch_row = self.db.execute(
                            "SELECT batch_id FROM supplies WHERE task_id=? AND supply_id=?",
                            (task_id, supply_id)).fetchone()
                        batch_id = batch_row["batch_id"] if batch_row else supply_id
                        available = self._batch_available_locked(batch_id)
                        if available < mass - 1e-9:
                            self._json({"error": f"确认失败：批次 {batch_id} 当前可用 {available:g} t"
                                                f" 不足以确认 {mass:g} t（含其他任务占用）"}, 409)
                            return
                        self.db.execute(
                            "INSERT INTO reservations(task_id,batch_id,quantity_t,status,"
                            "expires_at,run_id,created_by,created_at,updated_at)"
                            " VALUES(?,?,?,'confirmed',NULL,?,?,?,?)",
                            (task_id, batch_id, mass, run_id, session["user_id"],
                             dbmod.now(), dbmod.now()))
                    self.db.execute(
                        "INSERT OR IGNORE INTO selections(task_id,run_id,option_id,reason,"
                        "selected_by,created_at) VALUES(?,?,?,?,?,?)",
                        (task_id, run_id, option_id, reason + "（已确认）",
                         session["user_id"], dbmod.now()))
            except sqlite3.Error:
                log.exception("confirm failed")
                self._error("数据库错误", 500)
                return
            self.db.audit(self._actor(session), "purchase.confirm",
                          f"task:{task_id}/{run_id}",
                          after={"option_id": option_id, "allocation": allocation})
            self._json({"ok": True, "confirmed": True,
                        "allocation": allocation,
                        "note": "已按方案生成 confirmed 预留（占用实时库存）"}, 201)

        def _current_task_state_hash(self, task_id):
            """当前任务状态哈希 = 需求 + 供给快照 + 最新证据版本 + 因子配置哈希。"""
            import hashlib as _hashlib
            task = self.db.execute("SELECT demand_json FROM tasks WHERE id=?",
                                   (task_id,)).fetchone()
            supplies = [json.loads(r["supply_json"]) for r in self.db.execute(
                "SELECT supply_json FROM supplies WHERE task_id=? ORDER BY supply_id",
                (task_id,)).fetchall()]
            versions = [f"{r['supply_id']}:{r['version_no']}" for r in self.db.execute(
                "SELECT supply_id, MAX(version_no) AS version_no FROM evidence_versions "
                "WHERE task_id=? GROUP BY supply_id", (task_id,)).fetchall()]
            from src.passport import sha256_file
            config_sha = sha256_file(Path(__file__).resolve().parent.parent
                                     / "config" / "material_emission_factors.yaml")
            canonical = json.dumps({"demand": json.loads(task["demand_json"]),
                                    "supplies": supplies, "evidence": sorted(versions),
                                    "config": config_sha},
                                   ensure_ascii=False, sort_keys=True)
            return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        def _api_list_feedback(self, task_id, session):
            rows = self.db.execute(
                "SELECT * FROM feedback WHERE task_id=? ORDER BY reviewed_at DESC",
                (task_id,)).fetchall()
            self._json({"feedback": [dict(r) for r in rows]})

        def _api_add_feedback(self, task_id, session):
            body = self._read_body()
            decision = str(body.get("decision") or "")
            if decision not in ("通过", "否决", "补证"):
                self._error("decision 必须是 通过/否决/补证", 400)
                return
            observation_type = str(body.get("observation_type") or "真实")
            if observation_type not in ("真实", "模拟"):
                self._error("observation_type 必须是 真实/模拟", 400)
                return
            reviewed_at = str(body.get("reviewed_at") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed_at):
                self._error("reviewed_at 必须为 YYYY-MM-DD", 400)
                return
            actual_date = str(body.get("actual_date") or "")
            if actual_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", actual_date):
                self._error("actual_date 必须为 YYYY-MM-DD 或留空", 400)
                return
            def _fopt(value):
                if value in (None, ""):
                    return None
                try:
                    import math as _math
                    parsed = float(value)
                    if not _math.isfinite(parsed):
                        return None
                    return parsed
                except (TypeError, ValueError):
                    return None
            qty = _fopt(body.get("actual_qty_t"))
            cost = _fopt(body.get("actual_cost_cny"))
            if qty is not None and qty < 0:
                self._error("actual_qty_t 不能为负数", 400)
                return
            if cost is not None and cost < 0:
                self._error("actual_cost_cny 不能为负数", 400)
                return
            self.db.execute(
                "INSERT INTO feedback(task_id,scenario_id,candidate_id,decision,reason_code,"
                "notes,reviewed_at,rule_version,observation_type,stage,actual_qty_t,"
                "actual_date,actual_cost_cny,quality_result,return_breach,created_by,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, str(body.get("scenario_id") or ""),
                 str(body.get("candidate_id") or ""), decision,
                 str(body.get("reason_code") or ""), str(body.get("notes") or ""),
                 reviewed_at, str(body.get("rule_version") or ""),
                 observation_type,
                 str(body.get("stage") or ""),
                 qty,
                 actual_date,
                 cost,
                 str(body.get("quality_result") or ""),
                 str(body.get("return_breach") or ""),
                 session["user_id"], dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "feedback.add", f"task:{task_id}")
            self._json({"ok": True}, 201)

        def _api_reserve(self, task_id, session):
            """全局批次库存预留。可用量 = 原始可供量 − 全部未释放且未过期的有效预留
            − 已确认占用量；事务内原子条件校验，并发请求只有一个成功。"""
            import math as _math
            body = self._read_body()
            supply_ref = str(body.get("batch_id") or body.get("supply_id") or "")
            try:
                quantity = float(body.get("quantity_t") or 0)
            except (TypeError, ValueError):
                self._error("quantity_t 必须是数字", 400)
                return
            if not _math.isfinite(quantity) or quantity <= 0:
                self._error("quantity_t 必须为正的有限数字", 400)
                return
            supply = self.db.execute(
                "SELECT * FROM supplies WHERE task_id=? AND (supply_id=? OR batch_id=?)",
                (task_id, supply_ref, supply_ref)).fetchone()
            if supply is None:
                self._error("供给批次不存在或不在本任务内", 404)
                return
            batch_id = supply["batch_id"] or supply["supply_id"]
            expires_at = str(body.get("expires_at") or "") or None
            if expires_at:
                from src.validation import parse_date
                _, err = parse_date(expires_at, "expires_at")
                if err:
                    self._json({"error": err["problem"], "details": [err]}, 400)
                    return
            try:
                with self.db.transaction():
                    available = self._batch_available_locked(batch_id)
                    if available < quantity - 1e-9:
                        self._json({"error": f"库存不足：批次 {batch_id} 当前可用 {available:g} t，"
                                            f"本次请求 {quantity:g} t（全局有效预留与确认占用量已扣除）"}, 409)
                        return
                    self.db.execute(
                        "INSERT INTO reservations(task_id,batch_id,quantity_t,status,expires_at,"
                        "run_id,created_by,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (task_id, batch_id, quantity, "reserved", expires_at,
                         str(body.get("run_id") or ""), session["user_id"], dbmod.now(), dbmod.now()))
            except sqlite3.Error:
                log.exception("reserve tx failed")
                self._error("数据库错误", 500)
                return
            self.db.audit(self._actor(session), "reservation.create",
                          f"batch:{batch_id}", after={"quantity_t": quantity,
                                                      "expires_at": expires_at})
            self._json({"ok": True, "batch_id": batch_id}, 201)

        def _api_list_reservations(self, task_id, session):
            rows = self.db.execute(
                "SELECT * FROM reservations WHERE task_id=? ORDER BY id DESC",
                (task_id,)).fetchall()
            self._json({"reservations": [dict(r) for r in rows]})

        def _api_transition_reservation(self, res_id, action, session):
            """预留状态机：reserved → confirmed / released / cancelled；过期惰性判定。"""
            transitions = {"confirm": "confirmed", "release": "released", "cancel": "cancelled"}
            new_status = transitions[action]
            row = self.db.execute("SELECT * FROM reservations WHERE id=?",
                                  (res_id,)).fetchone()
            if row is None:
                self._error("预留不存在", 404)
                return
            allowed = {"confirm": ("reserved",), "release": ("reserved", "confirmed"),
                       "cancel": ("reserved",)}
            if row["status"] not in allowed[action]:
                self._error(f"状态 {row['status']} 不允许 {action}", 409)
                return
            try:
                with self.db.transaction():
                    if action == "confirm":
                        available = self._batch_available_locked(row["batch_id"],
                                                                 exclude_id=res_id)
                        if available < row["quantity_t"] - 1e-9:
                            self._json({"error": f"确认失败：批次 {row['batch_id']} 当前可用 "
                                                f"{available:g} t 不足以确认 {row['quantity_t']:g} t"}, 409)
                            return
                    self.db.execute(
                        "UPDATE reservations SET status=?, updated_at=? WHERE id=?",
                        (new_status, dbmod.now(), res_id))
            except sqlite3.Error:
                log.exception("reservation transition failed")
                self._error("数据库错误", 500)
                return
            self.db.audit(self._actor(session), f"reservation.{action}",
                          f"reservation:{res_id}",
                          before={"status": row["status"]}, after={"status": new_status})
            self._json({"ok": True, "status": new_status})

        def _api_batch_availability(self, batch_id, session):
            available = self._batch_available(batch_id)
            row = self.db.execute("SELECT * FROM batches WHERE batch_id=?",
                                  (batch_id,)).fetchone()
            if row is None:
                self._error("批次不存在", 404)
                return
            self._json({"batch_id": batch_id,
                        "original_mass_t": row["original_mass_t"],
                        "available_mass_t": available})

        # —— 多需求撮合（只读试算，v16 任务 3：即算即返不落库，仅写 audit） ——
        # v17 任务 2：预览与建议单共用同一计算（确定性），响应口径逐位一致
        def _api_matching_preview(self, session):
            body = self._read_body()
            payload = self._matching_payload(session, body.get("task_ids"),
                                             "matching.preview")
            if payload is None:
                return  # 错误响应（404/400）已由 _matching_payload 写出
            self._json(payload)

        def _api_matching_sheet(self, session):
            from src.matching_sheet import render_matching_sheet
            body = self._read_body()
            payload = self._matching_payload(session, body.get("task_ids"),
                                             "matching.sheet")
            if payload is None:
                return  # 错误响应（404/400）已由 _matching_payload 写出
            html = render_matching_sheet(payload, time.strftime("%Y-%m-%d %H:%M:%S"))
            body_bytes = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition",
                             'inline; filename="matching-sheet.html"')
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body_bytes)

        def _matching_payload(self, session, task_ids, action):
            if not isinstance(task_ids, list) or not task_ids:
                raise ValueError("task_ids 必须为非空数组")
            if any(not isinstance(t, str) or not t.strip() for t in task_ids):
                raise ValueError("task_ids 必须为非空字符串")
            if len(set(task_ids)) != len(task_ids):
                raise ValueError("task_ids 存在重复项")
            if len(task_ids) > 50:
                raise ValueError("单次撮合最多勾选 50 个任务")

            from src.material import load_material_factors
            from src.matching import match_demands
            from src.visuals import matching_svg

            demands, demand_meta, batch_meta = [], [], {}
            pool = {}  # batch_id → MaterialSupply（引擎键统一为全局批次 id）
            for tid in task_ids:
                row = self.db.execute(
                    "SELECT id, demand_json FROM tasks WHERE id=?", (tid,)).fetchone()
                if row is None:
                    self._error(f"任务不存在：{tid}", 404)
                    return
                demand = demand_from_wizard(json.loads(row["demand_json"]))
                # 引擎以 demand_id 为键：任务 id 是库内主键，跨任务必然唯一
                demand.demand_id = tid
                demands.append(demand)
                demand_meta.append({
                    "task_id": tid, "buyer_name": demand.buyer_name,
                    "required_mass_t": demand.required_mass_t,
                    "target_material_code": demand.target_material_code})
                for srow in self.db.execute(
                        "SELECT * FROM supplies WHERE task_id=?", (tid,)).fetchall():
                    supply = supply_from_payload(json.loads(srow["supply_json"]))
                    batch_id = srow["batch_id"] or supply.supply_id
                    # 与运行引擎同口径：容量=实时全局可用量（扣除其他任务有效预留与确认占用）
                    available = self._batch_available(batch_id)
                    if supply.available_mass_t is None or available < supply.available_mass_t:
                        supply.available_mass_t = available
                    if batch_id in pool:
                        # 同一批次被多任务引用：物理上只有一批，容量不重复计入
                        batch_meta[batch_id]["referenced_by_tasks"].append(tid)
                        continue
                    supply.supply_id = batch_id
                    pool[batch_id] = supply
                    batch_meta[batch_id] = {
                        "batch_id": batch_id, "supplier_name": supply.supplier_name,
                        "material_code": supply.material_code,
                        "capacity_t": supply.available_mass_t,
                        "referenced_by_tasks": [tid]}
            if not pool:
                self._error("所选任务均无供给批次，无可撮合对象", 400)
                return

            started = time.time()
            factor_data = load_material_factors()
            result = match_demands(demands, list(pool.values()), factor_data)
            elapsed_ms = round((time.time() - started) * 1000, 1)

            # 逐需求「撮合理由」摘要：由引擎输出与输入确定性组合，不引入新判断
            required = {d.demand_id: (d.required_mass_t or 0.0) for d in demands}
            unmatched_by_id = {u["demand_id"]: u for u in result["unmatched"]}
            reasons, alloc_view = {}, {}
            for match in result["matches"]:
                did = match["demand_id"]
                alloc_view[did] = dict(match["allocations"])
                pct = (match["satisfied_t"] / required[did] * 100.0) if required[did] else 0.0
                text = (f"按需求侧偏好（到厂单价升序、净减排强度降序、距离升序）分配 "
                        f"{len(match['allocations'])} 个批次、满足 {match['satisfied_t']:g}/"
                        f"{required[did]:g} t（{pct:.1f}%）")
                if match.get("proposed_cost_cny") is not None:
                    text += (f"；建议到厂成本 {match['proposed_cost_cny']:,.2f} 元"
                             "（核算口径，非报价）")
                if did in unmatched_by_id:
                    text += (f"；未满足 {unmatched_by_id[did]['remaining_t']:g} t——"
                             f"{unmatched_by_id[did]['reason']}")
                reasons[did] = text
            for did, u in unmatched_by_id.items():
                if did not in reasons:
                    reasons[did] = f"未获得分配（余 {u['remaining_t']:g} t）——{u['reason']}"

            svg = matching_svg(
                [{"id": d.demand_id,
                  "title": f"{d.demand_id} · {d.target_material_code}",
                  "sub": (f"需求 {(d.required_mass_t or 0.0):g} t"
                          + (f" · 未满足 {unmatched_by_id[d.demand_id]['remaining_t']:g} t"
                             if d.demand_id in unmatched_by_id else ""))}
                 for d in demands],
                [{"id": b, "title": b,
                  "sub": (f"{batch_meta[b]['supplier_name']} · 容量 "
                          f"{batch_meta[b]['capacity_t']:g} t"
                          + (f" · 本轮剩余 {result['leftover_capacity_t'][b]:g} t"
                             if b in result["leftover_capacity_t"] else " · 本轮已分满"))}
                 for b in batch_meta],
                alloc_view)

            self.db.audit(self._actor(session), action,
                          "tasks:" + ",".join(task_ids),
                          after={"tasks": len(task_ids), "matches": len(result["matches"]),
                                 "unmatched": len(result["unmatched"]),
                                 "blocking_pairs": result["stability"]["blocking_pairs"],
                                 "elapsed_ms": elapsed_ms})
            return {
                "ok": True, "preview": result, "reasons": reasons,
                "demands": demand_meta,
                "batches": [batch_meta[b] for b in sorted(batch_meta)],
                "svg": svg, "elapsed_ms": elapsed_ms,
                "disclaimers": [
                    "撮合建议（非交易/非预留）：即算即返、不落库，不产生预留或占用，"
                    "不改变批次库存真相",
                    "供给侧偏好为演示毛利模型（非真实报价行为）；批次容量=实时全局可用量"],
            }

        def _batch_available_locked(self, batch_id, exclude_id=None):
            """事务内查询全局有效占用并惰性过期（原子条件更新的一部分）。"""
            self.db.execute(
                "UPDATE reservations SET status='expired', updated_at=? WHERE batch_id=? "
                "AND status='reserved' AND expires_at IS NOT NULL AND expires_at < ?",
                (dbmod.now(), batch_id, dbmod.now()))
            query = ("SELECT COALESCE(SUM(quantity_t),0) AS s FROM reservations "
                     "WHERE batch_id=? AND status IN ('reserved','confirmed')")
            params = [batch_id]
            if exclude_id is not None:
                query += " AND id<>?"
                params.append(exclude_id)
            occupied = self.db.execute(query, params).fetchone()["s"]
            batch = self.db.execute("SELECT original_mass_t FROM batches WHERE batch_id=?",
                                    (batch_id,)).fetchone()
            original = batch["original_mass_t"] if batch else 0.0
            return max(0.0, original - occupied)

        def _batch_available(self, batch_id):
            """非事务查询（供 GET 与引擎使用）。"""
            return self._batch_available_locked(batch_id)

        # —— 管理 ——
        def _api_list_users(self, session):
            rows = self.db.execute(
                "SELECT id, username, role, active, created_at FROM users").fetchall()
            self._json({"users": [dict(r) for r in rows]})

        def _api_create_user(self, session):
            if not self._require_role_or_error(session, "admin"):
                self._error("权限不足", 403)
                return
            body = self._read_body()
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or "")
            role = str(body.get("role") or "viewer")
            if role not in authmod.ROLES:
                self._error("role 非法", 400)
                return
            if not username:
                self._error("用户名必填", 400)
                return
            policy_error = authmod.password_policy_error(password)
            if policy_error:
                self._error(policy_error, 400)
                return
            digest, salt = authmod.hash_password(password)
            self.db.execute(
                "INSERT INTO users(username,password_hash,salt,role,active,created_at,updated_at)"
                " VALUES(?,?,?,?,1,?,?)",
                (username, digest, salt, role, dbmod.now(), dbmod.now()))
            self.db.commit()
            self.db.audit(self._actor(session), "user.create", f"user:{username}")
            self._json({"ok": True}, 201)

        def _api_backup(self, session):
            if not self._require_role_or_error(session, "admin"):
                self._error("权限不足", 403)
                return
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
            name = f"pilot-backup-{stamp}.sqlite3"
            target = (BACKUP_DIR / name).resolve()
            self.db.backup_to(target)
            self.db.audit(self._actor(session), "backup.create", f"backup:{name}")
            self._json({"ok": True, "backup": name})

        def _api_list_backups(self, session):
            if not self._require_role_or_error(session, "admin"):
                self._error("权限不足", 403)
                return
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            # 名称降序 = 最新在前（名称含时间戳；前端取 [0] 即「最近备份」）
            backups = sorted(
                [{"name": p.name, "size": p.stat().st_size}
                 for p in BACKUP_DIR.glob("pilot-backup-*.sqlite3")],
                key=lambda b: b["name"], reverse=True)
            self._json({"backups": backups})

        def _api_restore(self, name, session):
            if not self._require_role_or_error(session, "admin"):
                self._error("权限不足", 403)
                return
            if not re.fullmatch(r"pilot-backup-[\d-]+\.sqlite3", name):
                self._error("备份文件名非法", 400)
                return
            source = (BACKUP_DIR / name).resolve()
            if not str(source).startswith(str(BACKUP_DIR.resolve())) or not source.exists():
                self._error("备份不存在", 404)
                return
            try:
                self.db.restore_from(source)
            except ValueError as exc:
                self._error(str(exc), 400)
                return
            self.db.audit(self._actor(session), "backup.restore", f"backup:{name}")
            self._json({"ok": True})

        def _api_audit(self, session):
            if not self._require_role_or_error(session, "admin"):
                self._error("权限不足", 403)
                return
            rows = self.db.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
            self._json({"audit": [dict(r) for r in rows]})

    return PilotHandler
