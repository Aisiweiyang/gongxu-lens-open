"""v14 求解基准：完整前沿路径的规模-耗时-前沿点数曲线（对照 v13 三锚点路径）。

运行（WSL，需已安装 OR-Tools）：`python3 benchmarks/solver_benchmark_v14.py`
输出：benchmarks/求解基准-v14.md（每次运行整体重写）。

口径与 v13 基准一致（同 make_case 生成器、需求 20t、步长 0.5t）：
- 组合数 ≤ MAX_COMBINATIONS：完整穷举；
- 组合数超限：OR-Tools CP-SAT 完整非支配前沿（ε-约束直接后继精确枚举；三锚点全局
  精确恒在；触达 FRONTIER_CAP 如实截断）。v13 锚点路径的耗时对照见 求解基准-v13.md。
"""

import math
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.material import (MAX_COMBINATIONS, FRONTIER_CAP, simulate_supply_mix,
                          _import_cp_sat, _ortools_version)
from benchmarks.solver_benchmark import make_case


def main():
    has_solver = _import_cp_sat() is not None
    rows = []
    for n in (5, 8, 12, 16, 20, 30):
        demand, eligible, factor_data = make_case(n)
        if len(eligible) < 2:
            rows.append((n, "-", "-", "跳过", "-", "-", "-"))
            continue
        combos = math.comb(40 + len(eligible) - 1, len(eligible) - 1)
        t0 = time.perf_counter()
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        ms = (time.perf_counter() - t0) * 1000
        solver_meta = mix.get("solver") or {}
        rows.append((n, len(eligible), combos,
                     "完整穷举" if combos <= MAX_COMBINATIONS else
                     ("CP-SAT 完整前沿" if not solver_meta.get("truncated") else "CP-SAT 前沿(触CAP截断)"),
                     round(ms, 1),
                     len(mix.get("frontier") or []),
                     "是" if mix.get("is_complete") else "否（如实截断）"))

    lines = [
        "# 求解基准 · v14（任务 2：完整前沿路径 规模-耗时-点数曲线）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 机器：{{'platform': '{platform.system().lower()}', 'python': '{platform.python_version()}'}}",
        f"- OR-Tools：{'已安装 ' + (_ortools_version() or '') if has_solver else '未安装'}",
        f"- 算法版本：material-v8；需求 20 t、步长 0.5 t（40 步）；穷举上限 {MAX_COMBINATIONS}；"
        f"前沿点数上限 FRONTIER_CAP={FRONTIER_CAP}",
        "",
        "| 供给批次 | eligible | 组合数 | 求解路径 | 引擎耗时 ms | 前沿点数 | 前沿完整 |",
        "|---:|---:|---:|---|---:|---:|---|",
    ]
    for n, nel, combos, path, ms, points, complete in rows:
        lines.append(f"| {n} | {nel} | {combos} | {path} | {ms} | {points} | {complete} |")
    lines += [
        "",
        "## 口径说明（如实）",
        "",
        "- v14 求解路径返回**完整三维非支配前沿**（ε-约束直接后继精确枚举；三锚点全局精确恒在），",
        "  替代 v13 的三锚点路径；触达 FRONTIER_CAP 时如实截断（is_complete=False），",
        "  三锚点仍全局精确（截断只影响前沿中段）。",
        "- 前沿点数为该离散网格下的非支配方案总数；穷举行的前沿=完整非支配集。",
        "- 与穷举的互验（≥10 组随机案例前沿集合相等 + 三目标逐位一致）见 tests/test_v14_frontier.py。",
        "- v13 三锚点路径的耗时对照见 求解基准-v13.md（同规模 5/8/12/16/20/30 供给为 "
        "31.9/54.2/70.9/88.0/70.4/144.1 ms；v14 完整前沿以更多求解次数换取完整性与锚点全局保证）。",
        "- Windows 解释器偏慢为既有已知项；本基准在 WSL 生成。",
    ]
    out = Path(__file__).resolve().parent / "求解基准-v14.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
