"""v13 求解基准：规模-耗时曲线（完整穷举 vs OR-Tools CP-SAT vs 贪心回退）。

运行（WSL）：`python3 benchmarks/solver_benchmark.py`
输出：benchmarks/求解基准-v13.md（每次运行整体重写，含时间戳与版本信息）。

口径与引擎一致：
- 组合数 ≤ MAX_COMBINATIONS：完整穷举（is_complete=True，返回完整非支配前沿）；
- 组合数超限：OR-Tools CP-SAT 三锚点字典序全局最优（is_complete=False，仅锚点非完整前沿）；
  未安装 OR-Tools 时同一规模自动回退贪心可行解（solver_status=feasible）。
所有成本/碳值均由真实账本（ledger.py）出账；CP-SAT 系数按 1e-6 定点量化、
结果经真实账本复算校验（见 src/material.py _solver_mix 与 tests/test_v13_solver.py）。
"""

import math
import platform
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.material import (MAX_COMBINATIONS, MaterialDemand, MaterialSupply,
                          _greedy_feasible_plan, _import_cp_sat, _ortools_version,
                          simulate_supply_mix, supply_state)

STEP_T = 0.5
REQUIRED_T = 20.0
TOTAL_STEPS = round(REQUIRED_T / STEP_T)
SCALES = [2, 3, 4, 5, 8, 12, 16, 20, 30]  # eligible 供给批次数


def make_factors():
    return {
        "baseline_transport_distance_km": 100.0,
        "factors": {
            "DEMO-VIRGIN-PP-PROD": {
                "factor_id": "DEMO-VIRGIN-PP-PROD", "role": "baseline_material",
                "stage": "virgin_production", "material_code": "PP", "value": 1800.0,
                "unit": "kgCO2e/t", "boundary": "cradle-to-gate", "region": "中国",
                "year": 2025, "source_name": "演示假设因子", "source_url": "",
                "version": 1, "grade": "演示假设"},
            "DEMO-PP-RECYCLE-PROCESS": {
                "factor_id": "DEMO-PP-RECYCLE-PROCESS", "role": "project_material",
                "stage": "recycled_granule_production", "material_code": "PP", "value": 400.0,
                "unit": "kgCO2e/t", "boundary": "gate-to-gate", "region": "中国",
                "year": 2025, "source_name": "演示假设因子", "source_url": "",
                "version": 1, "grade": "演示假设"},
            "TEST-TRANSPORT-2025": {
                "factor_id": "TEST-TRANSPORT-2025", "role": "transport",
                "stage": "transport", "material_code": "ANY", "value": 0.07447,
                "unit": "kgCO2e/(t·km)", "boundary": "TTW", "region": "英国",
                "year": 2025, "source_name": "测试运输因子", "source_url": "",
                "version": 1, "grade": "演示假设", "applies_to": ["公路货运"],
                "transport_included_in_factor": False},
        },
        "cost_assumptions": {
            "freight_rate_cny_per_tkm": 0.35,
            "handling_fee_cny_per_t": 20.0,
            "inspection_fee_per_batch_cny": 800.0,
            "default_yield": 1.0,
        },
    }


def make_case(n):
    rng = random.Random(1000 + n)
    demand = MaterialDemand(
        demand_id="BM-001", buyer_name="基准需方（测试）", target_material_code="PP",
        application="基准", required_mass_t=REQUIRED_T, min_recycled_content_pct=10.0,
        mfi_min=1.0, mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
        accepted_form="颗粒", accepted_color="黑色、灰色",
        required_from="2026-09-01", due_date="2026-12-31", destination="基准园区（测试）",
        baseline_virgin_price_cny_per_t=5500.0, max_distance_km=10000.0,
        evidence_source="基准规格书", source="测试")
    supplies = []
    for i in range(1, n + 1):
        cap = rng.randint(max(1, TOTAL_STEPS // 4), max(2, int(TOTAL_STEPS * 0.6)))
        supplies.append(MaterialSupply(
            supply_id=f"B-{i:02d}", supplier_name=f"基准供应商{i}（测试）",
            material_code="PP", polymer_type="PP", form="颗粒",
            color="黑色" if i % 2 else "灰色", grade="注塑级",
            mfi_g_10min=round(rng.uniform(5, 15), 1), moisture_pct=0.3, ash_pct=2.0,
            recycled_content_pct=90.0, available_mass_t=round(cap * STEP_T, 4),
            available_from="2026-09-01", available_to="2026-12-31",
            price_cny_per_t=round(rng.uniform(3400, 4600), 2),
            price_includes_freight=rng.choice([False, False, False, True]),
            yield_pct=rng.choice([100.0, 100.0, 100.0, 80.0, 50.0]),
            inspection_required=False, preprocessing="已造粒",
            transport_mode="公路货运", factor_id="TEST-TRANSPORT-2025",
            distance_km=float(rng.randint(10, 400)),
            evidence_source="检测报告（测试）", evidence_date="2026-08-01", source="测试"))
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    return demand, eligible, make_factors()


def main():
    has_solver = _import_cp_sat() is not None
    rows = []
    for n in SCALES:
        demand, eligible, factor_data = make_case(n)
        if len(eligible) < 2 or sum(s.available_mass_t for s in eligible) < REQUIRED_T:
            rows.append((n, "-", "-", "-", "跳过（生成案例不可行）", "-", None, "-"))
            continue
        combos = math.comb(TOTAL_STEPS + len(eligible) - 1, len(eligible) - 1)
        t0 = time.perf_counter()
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=STEP_T, max_plans=None)
        engine_ms = (time.perf_counter() - t0) * 1000
        status = mix.get("solver_status") or "optimal（穷举）"
        frontier_n = len(mix.get("frontier") or [])
        if combos <= MAX_COMBINATIONS:
            path = "完整穷举"
        elif has_solver:
            path = "OR-Tools CP-SAT"
        else:
            path = "贪心回退（未安装 OR-Tools）"
        t0 = time.perf_counter()
        greedy = _greedy_feasible_plan(demand, eligible, factor_data)
        greedy_ms = (time.perf_counter() - t0) * 1000
        rows.append((n, len(eligible), combos, path, status, round(engine_ms, 1),
                     frontier_n, round(greedy_ms, 1)))

    lines = [
        "# 求解基准 · v13（任务 1：规模-耗时曲线）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 机器：{{'platform': '{platform.system().lower()}', 'python': '{platform.python_version()}'}}",
        f"- OR-Tools：{'已安装 ' + (_ortools_version() or '（版本未知）') if has_solver else '未安装（超限规模回退贪心）'}",
        f"- 算法版本：material-v8；口径：需求 {REQUIRED_T:g} t、步长 {STEP_T:g} t（{TOTAL_STEPS} 步）；"
        f"穷举上限 MAX_COMBINATIONS={MAX_COMBINATIONS}",
        "",
        "| 供给批次 | eligible | 组合数 | 求解路径 | solver_status/最优性 | 引擎耗时 ms | 前沿/锚点数 | 贪心对照 ms |",
        "|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    for n, nel, combos, path, status, ms, frontier_n, greedy_ms in rows:
        status_short = status if len(str(status)) <= 46 else str(status)[:43] + "…"
        lines.append(f"| {n} | {nel} | {combos} | {path} | {status_short} | {ms} | {frontier_n} | {greedy_ms} |")
    lines += [
        "",
        "## 口径说明（如实）",
        "",
        "- 完整穷举行：is_complete=True，前沿=完整非支配集；组合数超过上限的规模不走穷举。",
        "- CP-SAT 行：返回最低到厂成本 / 最高保守净减排 / 最低订单集中度三个锚点（字典序全局最优），"
        "**不是完整前沿**，frontier 列为锚点数；成本系数按 1e-6 CNY 定点量化（单系数舍入误差 ≤5e-7），"
        "结果已用真实账本复算校验（_solver_mix 内置，不一致即回退贪心）。",
        "- 贪心对照：v12 超限回退的可行解（最低价优先填满），作参考下界对照；"
        "随机案例中贪心常达最优，但其不提供最优性证明、也看不到前沿与集中度权衡。",
        "- 与穷举的互验（≥10 组随机案例，成本/碳/分配矩阵逐位一致 + 前沿支配反证）见 tests/test_v13_solver.py。",
        "- Windows 解释器下引擎整体偏慢（既有已知项，属测试环境，勿改产品代码迁就）；本基准在 WSL 生成。",
    ]
    out = Path(__file__).resolve().parent / "求解基准-v13.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
