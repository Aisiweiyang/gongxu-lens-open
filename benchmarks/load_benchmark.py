"""服务器压力基准（v16 任务 2）：并发爬坡 × 固定用户行为序列，零依赖。

方法学借鉴 Locust（MIT，见 SPEC-v16 第四节核验记录）的「虚拟用户 × 固定行为序列 ×
并发爬坡 + P50/P95/P99 + 错误率」思想——仅借方法学，不引入 locust/gevent 依赖
（红线 4/7：零新增运行依赖）。

口径与诚实性约束：
- 默认被测服务器以**独立子进程**启动（pilot/start_pilot.py --db 临时库），与压测客户端
  进程隔离：压测线程与服务器不共享 GIL，不虚增也不掩盖服务器负载；
- --url 可指向已运行的真实部署（需 --username/--password，账号须有 editor 及以上权限）；
- 每一档并发使用**全新服务器实例与空库**（避免跨档状态污染：运行快照累积、进程级缓存、
  登录限流的 900s 窗口跨档残留）；每档计时前先做一次不计入统计的预热运行
  （因子表加载与进程级缓存建立）；
- 行为序列固定（每虚拟用户每轮，Locust on_start + task 语义）：
  ① 会话校验 GET /api/auth/me → ② 任务列表 GET /api/tasks → ③ 运行引擎（小案例）
  POST /api/tasks/{id}/runs → ④ 结果读取 GET /api/runs/{run_id} →
  ⑤ 护照下载 GET /api/tasks/{id}/passport?run_id=（走快照路径，不触发重算）；
- 登录模型 --login shared（默认，容量口径）：全体虚拟用户共享一个编辑会话，测量业务路径
  容量；--login per-user（限流口径）：每虚拟用户在序列内各自登录——受服务器「同 IP
  900 秒内最多 10 次登录尝试」限流，单机压测下 >10 并发登录必然出现 429（如实呈现，
  属真实部署约束：局域网各终端独立 IP 不触发；经 NAT/代理共用出口 IP 则会触发）；
- 百分位用最近秩法（nearest-rank），不引入统计包；同输入同输出（除时间戳/主机名）；
- 输出：JSON 原始数据 + Markdown 汇总表（可直接并入 docs/压力基准-v16.md）。
  本脚本只产生测量数据，不下「达标/不达标」结论——结论在报告中对照容量声明如实撰写。
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parent.parent

# 固定小案例夹具（与 tests/test_pilot.py 同构：2 供给 15t，未超组合上限，穷举路径）
DEMAND = {
    "demand_id": "LB-DEMAND", "buyer_name": "压测需方", "target_material_code": "PP",
    "application": "周转箱", "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15", "destination": "园区",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}
SUPPLIES = [
    {"supply_id": "LB-S1", "supplier_name": "压测甲", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 12.0, "moisture_pct": 0.3,
     "ash_pct": 3.0, "recycled_content_pct": 95.0, "available_mass_t": 8.0,
     "available_from": "2026-09-10", "available_to": "2026-10-31", "origin": "园区",
     "price_cny_per_t": 4200.0, "price_includes_freight": False, "yield_pct": 100.0,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
     "evidence_source": "检测报告", "evidence_date": "2026-08-15", "source": "压测"},
    {"supply_id": "LB-S2", "supplier_name": "压测乙", "material_code": "PP",
     "material_name_raw": "再生PP颗粒（黑色）", "polymer_type": "PP", "form": "颗粒",
     "color": "黑色", "grade": "注塑级", "mfi_g_10min": 11.0, "moisture_pct": 0.3,
     "ash_pct": 3.5, "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-08", "available_to": "2026-10-10", "origin": "外省",
     "price_cny_per_t": 3800.0, "price_includes_freight": False, "yield_pct": 100.0,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 480.0,
     "evidence_source": "检测报告", "evidence_date": "2026-08-05", "source": "压测"},
]

# 行为序列步骤名 → 期望状态码（非此即错误）
STEPS = ("login", "me", "tasks", "run", "result", "passport")
EXPECTED = {"login": 200, "me": 200, "tasks": 200, "run": 201,
            "result": 200, "passport": 200}


def large_case(n=30, seed=1030):
    """大案例夹具（v17 任务 3）：n=30 供给、需求 20t，与
    benchmarks/solver_benchmark.py make_case(30) 同源——同 rng 种子（1000+30）、
    同参数取值顺序（cap→mfi→价→含运→产率→距离），组合规模触 FRONTIER_CAP 截断
    （见 求解基准-v14.md 30 批次行：eligible=30、单次完整前沿 19204.6 ms）。

    与 make_case 的两处差异（如实标注，不改变规模与截断性质）：
    ① factor_id 用服务器演示因子表中的 DEFRA-ARTIC-HGV-DIESEL-2024——服务器环境
      无 TEST-TRANSPORT-2025，用演示因子保证运输碳可算、与真实请求路径一致；
    ② material_name_raw / origin 为演示值（payload 必填字段，make_case 的
      MaterialDemand/MaterialSupply 对象不携带这两个 API 字段）。
    """
    rng = random.Random(seed)
    demand = dict(DEMAND, demand_id="LB-BIG", buyer_name="基准需方（测试）",
                  application="基准", required_mass_t=20.0,
                  min_recycled_content_pct=10.0, mfi_min=1.0, mfi_max=100.0,
                  max_moisture_pct=5.0, max_ash_pct=5.0, max_distance_km=10000.0,
                  destination="基准园区（测试）")
    supplies = []
    total_steps = round(20.0 / 0.5)
    for i in range(1, n + 1):
        cap = rng.randint(max(1, total_steps // 4), max(2, int(total_steps * 0.6)))
        supplies.append({
            "supply_id": f"B-{i:02d}", "supplier_name": f"基准供应商{i}（测试）",
            "material_code": "PP", "material_name_raw": f"再生PP颗粒-{i:02d}（测试）",
            "polymer_type": "PP", "form": "颗粒",
            "color": "黑色" if i % 2 else "灰色", "grade": "注塑级",
            "mfi_g_10min": round(rng.uniform(5, 15), 1), "moisture_pct": 0.3,
            "ash_pct": 2.0, "recycled_content_pct": 90.0,
            "available_mass_t": round(cap * 0.5, 4),
            "available_from": "2026-09-01", "available_to": "2026-12-31",
            "origin": "基准园区（测试）",
            "price_cny_per_t": round(rng.uniform(3400, 4600), 2),
            "price_includes_freight": rng.choice([False, False, False, True]),
            "yield_pct": rng.choice([100.0, 100.0, 100.0, 80.0, 50.0]),
            "preprocessing": "已造粒", "transport_mode": "公路货运",
            "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024",
            "distance_km": float(rng.randint(10, 400)),
            "evidence_source": "检测报告（测试）", "evidence_date": "2026-08-01",
            "source": "压测"})
    return demand, supplies


FIXTURES = {
    "small": (DEMAND, SUPPLIES),      # 2 供给 15t：穷举路径（v16 压测口径）
    "large": large_case(),            # 30 供给 20t：CP-SAT 完整前沿，触 CAP 截断
}


class Client:
    """极简 HTTP 客户端：每请求一条连接，计时覆盖发送到响应体读完（大响应体如实计入）。"""

    def __init__(self, host, port, timeout=300):
        self.host, self.port, self.timeout = host, port, timeout
        self.cookie = ""
        self.csrf = ""

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        t0 = time.perf_counter()
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        set_cookie = res.getheader("Set-Cookie") or ""
        conn.close()
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = None
        return res.status, parsed, dt_ms, len(raw)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_health(client, deadline_s=60):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            status, body, _dt, _n = client.request("GET", "/api/health")
            if status == 200 and body and body.get("status") == "ok":
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def spawn_server(db_path, port):
    cmd = [sys.executable, str(PROJECT / "pilot" / "start_pilot.py"),
           "--host", "127.0.0.1", "--port", str(port), "--db", str(db_path)]
    return subprocess.Popen(cmd, cwd=str(PROJECT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def provision(c, admin_pw, n_editors):
    """空库初始化：管理员 + 压测编辑账号 + 返回编辑会话（供共享/建任务）。"""
    status, body, _dt, _n = c.request("POST", "/api/auth/init",
                                      {"username": "admin", "password": admin_pw})
    if status != 200:
        raise RuntimeError(f"init admin 失败：{status} {body}")
    status, body, _dt, _n = c.request("POST", "/api/auth/login",
                                      {"username": "admin", "password": admin_pw})
    if status != 200:
        raise RuntimeError(f"admin 登录失败：{status}")
    admin = Client(c.host, c.port, c.timeout)
    admin.cookie, admin.csrf = c.cookie, body["csrf"]
    editors = []
    for i in range(n_editors):
        username = f"lb_editor_{i}"
        status, body, _dt, _n = admin.request(
            "POST", "/api/users",
            {"username": username, "password": "lb-editor-pass-123", "role": "editor"})
        if status != 201:
            raise RuntimeError(f"创建编辑失败：{status} {body}")
        editors.append(username)
    status, body, _dt, _n = c.request("POST", "/api/auth/login",
                                      {"username": editors[0],
                                       "password": "lb-editor-pass-123"})
    if status != 200:
        raise RuntimeError(f"编辑登录失败：{status}")
    c.csrf = body["csrf"]
    return editors


def make_task(c, index, fixture):
    """建一个压测任务（需求 + 供给批次），返回 task_id。"""
    demand_template, supplies_template = fixture
    demand = dict(demand_template, demand_id=f"LB-D{index}")
    status, body, _dt, _n = c.request("POST", "/api/tasks", {"demand": demand})
    if status != 201:
        raise RuntimeError(f"建任务失败：{status} {body}")
    task_id = body["task_id"]
    for supply in supplies_template:
        status, body, _dt, _n = c.request(
            "POST", f"/api/tasks/{task_id}/supplies", {"supply": supply})
        if status != 201:
            raise RuntimeError(f"录供给失败：{status} {body}")
    return task_id


def vu_worker(vu_id, client_args, login_mode, credentials, iterations, task_id,
              shared_session, sink, lock, barrier):
    """单个虚拟用户：on_start 登录（per-user）+ 固定行为序列 × iterations。

    依赖前步的步骤（运行失败则无 run_id）不伪造请求也不计入统计：各步 n 可能不等，
    差值即上游失败导致的未执行次数。"""
    c = Client(*client_args)
    if login_mode == "shared":
        c.cookie, c.csrf = shared_session

    def record(step, status, dt_ms, nbytes):
        with lock:
            sink.append((step, status, dt_ms, nbytes))

    barrier.wait()
    logged_in = login_mode == "shared"
    for _it in range(iterations):
        if not logged_in:
            username, password = credentials[vu_id]
            status, body, dt, nb = c.request(
                "POST", "/api/auth/login", {"username": username, "password": password})
            record("login", status, dt, nb)
            if status != 200:
                break  # 限流（429）等：该虚拟用户无法继续，后续轮次不再伪造请求
            c.csrf = (body or {}).get("csrf") or ""  # 写接口需携带本人会话的 CSRF
            logged_in = True
        status, _b, dt, nb = c.request("GET", "/api/auth/me")
        record("me", status, dt, nb)
        status, _b, dt, nb = c.request("GET", "/api/tasks")
        record("tasks", status, dt, nb)
        status, body, dt, nb = c.request("POST", f"/api/tasks/{task_id}/runs")
        record("run", status, dt, nb)
        run_id = (body or {}).get("run_id") if status == 201 else None
        if not run_id:
            continue
        status, _b, dt, nb = c.request("GET", f"/api/runs/{run_id}")
        record("result", status, dt, nb)
        status, _b, dt, nb = c.request(
            "GET", f"/api/tasks/{task_id}/passport?run_id={run_id}")
        record("passport", status, dt, nb)


def percentile(sorted_vals, p):
    """最近秩法百分位（nearest-rank，确定性）。"""
    if not sorted_vals:
        return None
    k = max(1, math.ceil(p / 100.0 * len(sorted_vals)))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def run_level(args, level, tag):
    """跑一档并发：全新服务器 + 空库 + 预热，返回 {meta, steps, level_summary}。"""
    tmp = tempfile.TemporaryDirectory(prefix=f"loadbench-{tag}-{level}-",
                                      ignore_cleanup_errors=True)
    port = free_port()
    proc = None
    try:
        host = args.url_host if args.url else "127.0.0.1"
        # 大案例单次引擎运行可达数十秒且并发时排队：客户端超时放宽（如实记录）
        timeout_s = 1800 if args.fixture == "large" else 300
        client_args = (host, args.url_port if args.url else port, timeout_s)
        if not args.url:
            proc = spawn_server(Path(tmp.name) / "pilot.sqlite3", port)
        probe = Client(*client_args)
        if not wait_health(probe):
            raise RuntimeError(f"服务器健康检查超时（档 {level}）")
        if args.url:
            status, body, _dt, _n = probe.request(
                "POST", "/api/auth/login",
                {"username": args.username, "password": args.password})
            if status != 200:
                raise RuntimeError(f"--url 登录失败：{status}")
            probe.csrf = body["csrf"]
            credentials = [(args.username, args.password)] * level
        else:
            # per-user 模式每虚拟用户独立账号（限流按 IP 计，账号数不影响限流结论）
            editors = provision(probe, "lb-admin-pass-123",
                                level if args.login == "per-user" else 1)
            credentials = [(editors[i % len(editors)], "lb-editor-pass-123")
                           for i in range(level)]
        fixture = FIXTURES[args.fixture]
        tasks = [make_task(probe, i, fixture) for i in range(level)]
        # 预热（不计入统计）：因子表加载 + 进程级缓存建立 + WAL 就绪
        status, body, _dt, _n = probe.request("POST", f"/api/tasks/{tasks[0]}/runs")
        if status != 201:
            raise RuntimeError(f"预热运行失败：{status}")
        shared_session = (probe.cookie, probe.csrf) if args.login == "shared" else ("", "")

        sink, lock = [], threading.Lock()
        barrier = threading.Barrier(level + 1)
        threads = [threading.Thread(
            target=vu_worker,
            args=(i, client_args, args.login, credentials, args.iterations,
                  tasks[min(i, len(tasks) - 1)], shared_session, sink, lock, barrier),
            daemon=True) for i in range(level)]
        for t in threads:
            t.start()
        t0 = time.perf_counter()
        barrier.wait()
        for t in threads:
            t.join(3600)
        wall = time.perf_counter() - t0

        steps = {}
        for step, status, dt, nb in sink:
            steps.setdefault(step, []).append((status, dt, nb))
        step_stats = {}
        for step in STEPS:
            rows = steps.get(step, [])
            lat = sorted(r[1] for r in rows)
            errs = [r for r in rows if r[0] != EXPECTED[step]]
            step_stats[step] = {
                "n": len(rows),
                "errors": len(errs),
                "error_statuses": sorted({str(r[0]) for r in errs}),
                "mean_ms": round(sum(lat) / len(lat), 1) if lat else None,
                "p50_ms": round(percentile(lat, 50), 1) if lat else None,
                "p95_ms": round(percentile(lat, 95), 1) if lat else None,
                "p99_ms": round(percentile(lat, 99), 1) if lat else None,
                "max_ms": round(lat[-1], 1) if lat else None,
                "bytes_total": sum(r[2] for r in rows),
            }
        total_req = sum(s["n"] for s in step_stats.values())
        total_err = sum(s["errors"] for s in step_stats.values())
        return {
            "level": level,
            "steps": step_stats,
            "summary": {
                "requests": total_req, "errors": total_err,
                "error_rate": round(total_err / total_req, 4) if total_req else None,
                "wall_s": round(wall, 2),
                "rps": round(total_req / wall, 2) if wall else None,
                "engine_runs": step_stats["run"]["n"],
                "engine_runs_per_s": (round(step_stats["run"]["n"] / wall, 3)
                                      if wall else None),
                "engine_p50_ms": step_stats["run"]["p50_ms"],
                "engine_p95_ms": step_stats["run"]["p95_ms"],
                "engine_p99_ms": step_stats["run"]["p99_ms"],
            },
        }
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _cleanup_tmp(tmp)


def _cleanup_tmp(tmp):
    """Windows 上子进程退出后 SQLite 文件锁释放有滞后：重试后仍失败则放弃清理
    （临时目录由系统回收，不影响任何测量结果）。"""
    for _ in range(10):
        try:
            tmp.cleanup()
            return
        except OSError:
            time.sleep(0.5)
    print(f"[load_benchmark] 提示：临时目录未能立即清理（Windows 文件锁），可忽略：{tmp.name}")


def markdown_report(all_runs, meta):
    lines = ["## 压测原始汇总（脚本自动产出）", "",
             f"- 环境：`{meta['python']}` · `{meta['platform']}` · CPU {meta['cpu']} 逻辑核",
             f"- 口径：登录模型 {meta['login']}；案例夹具 {meta.get('fixture', 'small')}；"
             f"每虚拟用户每档 {meta['iterations']} 轮序列；"
             f"每档全新服务器实例 + 空库 + 1 次不计入预热", "",
             "### 档级汇总", "",
             "| 并发 | 请求 | 错误 | 错误率 | 吞吐 req/s | 引擎运行 | 引擎吞吐 runs/s | "
             "引擎 P50/P95/P99 ms | 墙钟 s |",
             "|---|---|---|---|---|---|---|---|---|"]
    for run in all_runs:
        s = run["summary"]
        lines.append(
            f"| {run['level']} | {s['requests']} | {s['errors']} | "
            f"{(s['error_rate'] or 0):.2%} | {s['rps']} | {s['engine_runs']} | "
            f"{s['engine_runs_per_s']} | {s['engine_p50_ms']}/{s['engine_p95_ms']}/"
            f"{s['engine_p99_ms']} | {s['wall_s']} |")
    lines += ["", "### 分步延迟与错误", "",
              "| 并发 | 步骤 | n | 错误 | 错误状态 | P50 ms | P95 ms | P99 ms | 最大 ms |",
              "|---|---|---|---|---|---|---|---|---|"]
    for run in all_runs:
        for step, s in run["steps"].items():
            lines.append(
                f"| {run['level']} | {step} | {s['n']} | {s['errors']} | "
                f"{','.join(s['error_statuses']) or '-'} | {s['p50_ms']} | "
                f"{s['p95_ms']} | {s['p99_ms']} | {s['max_ms']} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="供需透镜服务器压力基准（零依赖）")
    parser.add_argument("--url", help="已运行服务器（如 http://127.0.0.1:8765）；缺省自起子进程")
    parser.add_argument("--username", help="--url 模式登录用户名（editor 及以上）")
    parser.add_argument("--password", help="--url 模式登录密码")
    parser.add_argument("--levels", default="1,5,10,20,30",
                        help="并发爬坡档位，逗号分隔（默认 1,5,10,20,30）")
    parser.add_argument("--iterations", type=int, default=3,
                        help="每虚拟用户每档执行行为序列的轮数（默认 3）")
    parser.add_argument("--login", choices=("shared", "per-user"), default="shared",
                        help="登录模型：shared=共享会话（容量口径）；per-user=序列内各自登录"
                             "（限流口径，同 IP >10 并发登录将呈现 429）")
    parser.add_argument("--fixture", choices=("small", "large"), default="small",
                        help="案例夹具：small=2 供给 15t 穷举路径（默认，v16 口径）；"
                             "large=30 供给 20t 完整前沿触 CAP 截断（求解基准-v14 同源，"
                             "v17 任务 3 大案例复核；建议配 --levels 1,2,4 --iterations 1）")
    parser.add_argument("--tag", default="run", help="输出文件名与环境标注（如 wsl / windows）")
    parser.add_argument("--out-dir", default=str(PROJECT / "benchmarks"),
                        help="原始 JSON 与 Markdown 输出目录")
    args = parser.parse_args()

    if args.url:
        parsed = urlparse(args.url)
        args.url_host, args.url_port = parsed.hostname or "127.0.0.1", parsed.port or 80
        if not args.username or not args.password:
            parser.error("--url 模式需要 --username/--password")

    levels = [int(x) for x in str(args.levels).split(",") if x.strip()]
    meta = {
        "tag": args.tag,
        "login": args.login,
        "fixture": args.fixture,
        "iterations": args.iterations,
        "levels": levels,
        "target": args.url or "spawned subprocess (pilot/start_pilot.py, temp db)",
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": os.cpu_count(),
        "hostname": socket.gethostname(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    print(f"[load_benchmark] 目标：{meta['target']}；档位 {levels}；"
          f"iterations={args.iterations}；login={args.login}；fixture={args.fixture}")
    all_runs = []
    for level in levels:
        t0 = time.perf_counter()
        run = run_level(args, level, args.tag)
        all_runs.append(run)
        s = run["summary"]
        print(f"  档 {level:>2}：{s['requests']} 请求 / {s['errors']} 错误 · "
              f"{s['rps']} req/s · 引擎 P95 {s['engine_p95_ms']} ms · "
              f"墙钟 {s['wall_s']}s（含搭建 {time.perf_counter() - t0 - s['wall_s']:.1f}s）")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"压力基准-raw-{args.tag}-{args.login}.json"
    json_path.write_text(
        json.dumps({"meta": meta, "runs": all_runs}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    md_path = out_dir / f"压力基准-raw-{args.tag}-{args.login}.md"
    md_path.write_text(markdown_report(all_runs, meta), encoding="utf-8")
    print(f"[load_benchmark] 原始数据：{json_path}")
    print(f"[load_benchmark] 汇总表：{md_path}")


if __name__ == "__main__":
    main()
