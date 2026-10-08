"""D1 独立核验脚本（不 import 被测核心函数之外的东西，穷举/核算公式独立实现）。

用法：python docs/独立核验脚本-v10.py
对照口径：
- feasible = 满足规格与产能约束的全部完整枚举分配（与引擎 simulate_supply_mix 相同定义，
  含单供分配；引擎把单供分配从「组合前沿」排除并在 single 选项中表达，属设计而非错误，
  前沿对照只比多供方方案）。
- 目标值公式独立推导：成本 = Σ m×(价格+距离×0.35) + m×20（材料+运费+装卸，无检测/损耗）；
  净减排 = Σ m×(1800 + 100×0.07447) − m×(400 + 距离×0.07447)。
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.material import (demo_demand, demo_supplies, load_material_factors,
                          simulate_supply_mix, supply_state)

VIRGIN, RECYCLE, TRANSPORT, BASE_DIST = 1800.0, 400.0, 0.07447, 100.0
FREIGHT, HANDLING = 0.35, 20.0


def independent_case(demand, supplies, step_t=0.5):
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    total_steps = int(round(demand.required_mass_t / step_t))
    feasible, multi = [], []

    def comps(rem, i, cur):
        if i == len(eligible) - 1:
            yield cur + [rem]
            return
        for k in range(rem + 1):
            yield from comps(rem - k, i + 1, cur + [k])

    for alloc in comps(total_steps, 0, []):
        masses = [k * step_t for k in alloc]
        if any(masses[i] > eligible[i].available_mass_t + 1e-9
               for i in range(len(eligible))):
            continue
        cost = sum(m * (s.price_cny_per_t + s.distance_km * FREIGHT) + m * HANDLING
                   for m, s in zip(masses, eligible))
        net = sum(m * (VIRGIN + BASE_DIST * TRANSPORT)
                  - (m * RECYCLE + m * s.distance_km * TRANSPORT)
                  for m, s in zip(masses, eligible))
        share = max(masses) / demand.required_mass_t if masses else 0.0
        feasible.append((cost, net, share, tuple(masses)))
        if sum(1 for m in masses if m > 0) >= 2:
            multi.append((cost, net, share, tuple(masses)))
    # 与引擎一致的支配语义：比较带显式容差（成本/净减排 1e-6 绝对容差），
    # 避免浮点运算顺序差异在末位翻转支配关系（引擎同款容差见 material.DOM_TOL_*）。
    DOM_TOL = 1e-6
    frontier = [f for f in multi
                if not any(o[0] <= f[0] + DOM_TOL and o[1] >= f[1] - DOM_TOL
                           and o[2] <= f[2] + 1e-9
                           and (o[0] < f[0] - DOM_TOL or o[1] > f[1] + DOM_TOL
                                or o[2] < f[2] - 1e-9)
                           for o in multi)]
    return feasible, frontier


def approx_in(engine_frontier, c, n, tol=0.01):
    return any(abs(p["total_cost_cny"] - c) <= tol
               and abs(p["net_avoided_kgco2e"] - n) <= tol
               for p in engine_frontier)


def check_demo(fd):
    demand, supplies = demo_demand(), demo_supplies()
    engine = simulate_supply_mix(demand, supplies, fd, step_t=0.5, max_plans=None)
    feasible, frontier = independent_case(demand, supplies)
    assert engine["feasible_count"] == len(feasible), \
        f"feasible 不一致：引擎 {engine['feasible_count']} vs 独立 {len(feasible)}"
    assert engine["frontier_count"] == len(frontier), \
        f"frontier 不一致：引擎 {engine['frontier_count']} vs 独立 {len(frontier)}"
    for c, n, sh, masses in frontier:
        assert approx_in(engine["frontier"], c, n), f"独立前沿方案缺失 {masses} {c} {n}"
    print(f"[demo] feasible={engine['feasible_count']} frontier={engine['frontier_count']} 一致")


def make_random_case(seed):
    random.seed(seed)
    n_s = random.randint(2, 4)
    req = random.choice([4.0, 6.0, 8.0, 10.0])
    supplies = [demo_supplies()[0].__class__(
        supply_id=f"R{seed}-{i}", supplier_name=f"随机{i}", material_code="PP",
        material_name_raw="再生PP颗粒（黑色）", polymer_type="PP", form="颗粒", color="黑色",
        grade="注塑级", mfi_g_10min=12.0, moisture_pct=0.3, ash_pct=3.0,
        recycled_content_pct=95.0,
        available_mass_t=random.choice([3.0, 4.0, 5.0, 8.0, 12.0]),
        available_from="2026-09-10", available_to="2026-10-31", origin="园区",
        price_cny_per_t=random.choice([3600.0, 4000.0, 4200.0, 4400.0]),
        price_includes_freight=False, yield_pct=100.0, preprocessing="已造粒",
        transport_mode="公路货运", factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
        distance_km=random.choice([30.0, 60.0, 120.0, 480.0]),
        evidence_source="检测报告", evidence_date="2026-08-15", source="随机")
        for i in range(n_s)]
    demand = demo_demand().__class__(
        demand_id=f"R{seed}", buyer_name="随机需方", target_material_code="PP",
        application="测试", required_mass_t=req, min_recycled_content_pct=80.0,
        mfi_min=8.0, mfi_max=16.0, max_moisture_pct=0.5, max_ash_pct=5.0,
        accepted_form="颗粒", accepted_color="黑色", required_from="2026-09-15",
        due_date="2026-10-15", destination="园区",
        baseline_virgin_price_cny_per_t=5500.0, max_distance_km=500.0,
        evidence_source="测试", source="随机")
    return demand, supplies


def check_random(fd, seeds=(42, 43, 44, 45, 46, 47)):
    for seed in seeds:
        demand, supplies = make_random_case(seed)
        engine = simulate_supply_mix(demand, supplies, fd, step_t=0.5, max_plans=None)
        feasible, frontier = independent_case(demand, supplies)
        assert engine["feasible_count"] == len(feasible), \
            f"seed {seed} feasible 不一致：引擎 {engine['feasible_count']} vs 独立 {len(feasible)}"
        assert engine["frontier_count"] == len(frontier), \
            f"seed {seed} frontier 不一致：引擎 {engine['frontier_count']} vs 独立 {len(frontier)}"
        for c, n, sh, masses in frontier:
            assert approx_in(engine["frontier"], c, n), f"seed {seed} 独立前沿方案缺失 {masses}"
        print(f"[random {seed}] feasible={engine['feasible_count']} frontier={engine['frontier_count']} 一致")


if __name__ == "__main__":
    fd = load_material_factors()
    check_demo(fd)
    check_random(fd)
    print("D1 独立核验通过：演示数据 + 6 组随机案例，可行集与前沿与独立穷举完全一致")
