"""v13 OR-Tools CP-SAT 求解路径与穷举互验。

互验方法：随机生成系数可精确表示的案例（价格 2 位小数、距离整数、收率取 100/80/50，
使账本系数可被 1e-6 定点无损离散），把引擎组合上限临时调小，强制「本来可以完整穷举」
的案例走 CP-SAT 路径；测试内用独立实现的组合枚举 + 引擎同款 _mix_plan 定价 + 独立
非支配筛选得到真值，逐位对比三个锚点（最低到厂成本 / 最高保守净减排 / 最低订单集中度）
的成本、碳与分配矩阵；再对穷举前沿逐点做 CP-SAT「支配可行性」反证——若存在支配解则
说明穷举漏解。未安装 OR-Tools 的回退契约用 monkeypatch 单独验证（与 v12 逐键一致）。
"""

import math
import random
import unittest
from unittest import mock

from src import material
from src.greedy_baseline import greedy_report
from src.material import (
    DOM_TOL_COST_NET,
    DOM_TOL_SHARE,
    CP_SCALE,
    MaterialDemand,
    MaterialSupply,
    _greedy_feasible_plan,
    _mix_plan,
    _nondominated_mix,
    _to_scaled_int,
    build_options,
    representative_options,
    simulate_supply_mix,
    supply_state,
)

HAS_ORTOOLS = material._import_cp_sat() is not None

# 互验用临时上限：随机案例的组合数落在 (SOLVER_THRESHOLD, 4×SOLVER_THRESHOLD] 区间，
# 既强制走求解器、又保证测试内可完整穷举取真值。
SOLVER_THRESHOLD = 300


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


def make_demand(required_mass_t):
    return MaterialDemand(
        demand_id="T-001", buyer_name="互验需方（测试）", target_material_code="PP",
        application="互验", required_mass_t=required_mass_t, min_recycled_content_pct=10.0,
        mfi_min=1.0, mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
        accepted_form="颗粒", accepted_color="黑色、灰色",
        required_from="2026-09-01", due_date="2026-12-31", destination="互验园区（测试）",
        baseline_virgin_price_cny_per_t=5500.0, max_distance_km=10000.0,
        evidence_source="测试规格书", source="测试")


def make_supply(rng, index, total_steps, step_t):
    cap_steps = rng.randint(max(1, total_steps // 4), max(2, int(total_steps * 0.6)))
    return MaterialSupply(
        supply_id=f"S-{index:02d}", supplier_name=f"互验供应商{index}（测试）",
        material_code="PP", polymer_type="PP", form="颗粒",
        color="黑色" if index % 2 else "灰色", grade="注塑级",
        mfi_g_10min=round(rng.uniform(5, 15), 1), moisture_pct=0.3, ash_pct=2.0,
        recycled_content_pct=90.0, available_mass_t=round(cap_steps * step_t, 4),
        available_from="2026-09-01", available_to="2026-12-31",
        price_cny_per_t=round(rng.uniform(3400, 4600), 2),
        price_includes_freight=rng.choice([False, False, False, True]),
        yield_pct=rng.choice([100.0, 100.0, 100.0, 80.0, 50.0]),
        inspection_required=False, preprocessing="已造粒",
        transport_mode="公路货运", factor_id="TEST-TRANSPORT-2025",
        distance_km=float(rng.randint(10, 400)),
        evidence_source="检测报告（测试）", evidence_date="2026-08-01", source="测试")


def compositions(total, parts):
    """独立实现的组合拆分枚举（不复用引擎 _compositions）。"""
    if parts == 1:
        yield (total,)
        return
    for first in range(total + 1):
        for rest in compositions(total - first, parts - 1):
            yield (first,) + rest


def enumerate_ground_truth(demand, eligible, factor_data, step_t):
    total_steps = round(demand.required_mass_t / step_t)
    feasible = []
    for combo in compositions(total_steps, len(eligible)):
        masses = [k * step_t for k in combo]
        if any(masses[i] > (eligible[i].available_mass_t or 0.0) + 1e-9
               for i in range(len(eligible))):
            continue
        feasible.append(_mix_plan(demand, eligible, masses, factor_data))
    frontier, _ = _nondominated_mix(feasible)
    return feasible, frontier


def sort_key(p):
    return (p["_cost_full"] if p["_cost_full"] is not None else 1e18,
            -(p["_net_full"] if p["_net_full"] is not None else -1e18),
            p["_share_full"] or 1.0)


def anchor_key(name, p):
    if name == "cost":
        return (p["_cost_full"], -p["_net_full"], p["_share_full"] or 1.0)
    if name == "net":
        return (-p["_net_full"], p["_cost_full"], p["_share_full"] or 1.0)
    return (p["_share_full"] or 1.0, p["_cost_full"], -p["_net_full"])


def rounded_tuple(p):
    return (round(p["_cost_full"], 4), round(-p["_net_full"], 4),
            round(p["_share_full"] or 1.0, 6))


def build_case(seed):
    """生成一个组合数超阈值（走求解器）但测试内可完整穷举的随机案例。

    返回 None 表示该种子产生锚点并列（字典序最优不唯一）——换种子，保证
    「分配矩阵逐位一致」的断言有意义。可控系数（2 位小数价格、整数距离、
    100/80/50 收率）保证量化无损，锚点对比可以要求逐位相等。
    """
    rng = random.Random(seed)
    step_t = 0.5
    n = rng.randint(3, 5)
    total_steps = rng.randint(12, 24)
    combos = math.comb(total_steps + n - 1, n - 1)
    if not (SOLVER_THRESHOLD < combos <= 4 * SOLVER_THRESHOLD):
        return None
    demand = make_demand(total_steps * step_t)
    eligible = [make_supply(rng, i, total_steps, step_t) for i in range(1, n + 1)]
    factor_data = make_factors()
    if sum((s.available_mass_t or 0) for s in eligible) < demand.required_mass_t:
        return None
    for s in eligible:
        if supply_state(demand, s)["state"] != "eligible":
            return None
    feasible, frontier = enumerate_ground_truth(demand, eligible, factor_data, step_t)
    if len(frontier) < 2:
        return None
    anchors = {}
    for name in ("cost", "net", "share"):
        winner = min(frontier, key=lambda p: anchor_key(name, p))
        if sum(1 for p in frontier if rounded_tuple(p) == rounded_tuple(winner)) != 1:
            return None  # 字典序最优不唯一：换种子
        anchors[name] = winner
    return {"demand": demand, "eligible": eligible, "factor_data": factor_data,
            "step_t": step_t, "feasible": feasible, "frontier": frontier,
            "anchors": anchors}


def load_cases(count):
    """逐种子收集有效案例；被拒绝的种子数一并返回供诊断（并列即换种子）。"""
    collected, rejected = [], 0
    for seed in range(1, 400):
        if len(collected) >= count:
            break
        case = build_case(seed)
        if case is None:
            rejected += 1
        else:
            collected.append((seed, case))
    return collected, rejected


class SolverMutualVerification(unittest.TestCase):
    """≥10 组随机案例：穷举 vs CP-SAT 成本/碳/分配矩阵逐位一致。"""

    @classmethod
    def setUpClass(cls):
        if not HAS_ORTOOLS:
            raise unittest.SkipTest("OR-Tools 未安装：互验用例仅在已安装环境运行")
        cls.cases, cls.rejected = load_cases(10)

    def test_10_cases_collected(self):
        self.assertGreaterEqual(len(self.cases), 10,
                                "有效随机案例不足 10 组（种子空间不足或过滤过严）")

    def run_one_case(self, seed, case):
        with mock.patch.object(material, "MAX_COMBINATIONS", SOLVER_THRESHOLD):
            mix = simulate_supply_mix(case["demand"], case["eligible"], case["factor_data"],
                                      step_t=case["step_t"], max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal",
                         f"seed={seed} 应走 CP-SAT 最优路径")
        self.assertIn("CP-SAT", mix.get("optimality") or "")
        by_alloc = {tuple(sorted(p["allocation"].items())): p for p in case["frontier"]}
        solver_frontier = mix.get("frontier") or []
        self.assertTrue(solver_frontier, f"seed={seed} 求解器应返回锚点方案")
        for name in ("cost", "net", "share"):
            truth = case["anchors"][name]
            # 分配矩阵逐位一致：求解器锚点必须与穷举锚点分配完全相同
            self.assertIn(tuple(sorted(truth["allocation"].items())), by_alloc,
                          f"seed={seed} {name} 锚点应在前沿中")
            matches = [p for p in solver_frontier
                       if p["allocation"] == truth["allocation"]]
            self.assertEqual(len(matches), 1,
                             f"seed={seed} {name} 锚点分配未逐位复现："
                             f"truth={truth['allocation']} solver={[p['allocation'] for p in solver_frontier]}")
            got = matches[0]
            # 成本与碳逐位一致（同一 _mix_plan 出账，同分配必同值）
            self.assertEqual(got["total_cost_cny"], truth["total_cost_cny"],
                             f"seed={seed} {name} 锚点成本不一致")
            self.assertEqual(got["_cost_full"], truth["_cost_full"],
                             f"seed={seed} {name} 锚点全精度成本不一致")
            self.assertEqual(got["_net_full"], truth["_net_full"],
                             f"seed={seed} {name} 锚点全精度净减排不一致")
            self.assertEqual(got["option_id"], truth["option_id"],
                             f"seed={seed} {name} 锚点 option_id 不一致")
        # 求解器前沿每个点都必须来自穷举可行集且不被穷举前沿任何点支配
        for p in solver_frontier:
            key = tuple(sorted(p["allocation"].items()))
            self.assertIn(key, by_alloc, f"seed={seed} 求解器方案不在穷举可行集中")
        # 最低成本锚点 = 穷举前沿排序首项（排序键一致）
        self.assertEqual(min(solver_frontier, key=sort_key)["option_id"],
                         min(case["frontier"], key=sort_key)["option_id"],
                         f"seed={seed} 成本排序首项不一致")

    def test_case_01(self):
        seed, case = self.cases[0]
        self.run_one_case(seed, case)

    def test_case_02(self):
        seed, case = self.cases[1]
        self.run_one_case(seed, case)

    def test_case_03(self):
        seed, case = self.cases[2]
        self.run_one_case(seed, case)

    def test_case_04(self):
        seed, case = self.cases[3]
        self.run_one_case(seed, case)

    def test_case_05(self):
        seed, case = self.cases[4]
        self.run_one_case(seed, case)

    def test_case_06(self):
        seed, case = self.cases[5]
        self.run_one_case(seed, case)

    def test_case_07(self):
        seed, case = self.cases[6]
        self.run_one_case(seed, case)

    def test_case_08(self):
        seed, case = self.cases[7]
        self.run_one_case(seed, case)

    def test_case_09(self):
        seed, case = self.cases[8]
        self.run_one_case(seed, case)

    def test_case_10(self):
        seed, case = self.cases[9]
        self.run_one_case(seed, case)


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools 未安装")
class FrontierDominanceCounterCheck(unittest.TestCase):
    """支配反证：对穷举前沿逐点，CP-SAT 证明不存在任何支配它的可行分配（无漏解）。"""

    def test_no_dominating_solution_for_frontier_points(self):
        case = None
        for seed in range(1, 400):
            candidate = build_case(seed)
            if candidate and 2 <= len(candidate["frontier"]) <= 8:
                case = (seed, candidate)
                break
        self.assertIsNotNone(case, "未找到适合反证的小前沿案例")
        seed, case = case
        cp_model = material._import_cp_sat()
        demand, eligible = case["demand"], case["eligible"]
        factor_data, step_t = case["factor_data"], case["step_t"]
        total_steps = round(demand.required_mass_t / step_t)
        units, _ = material._solver_unit_coefficients(demand, eligible, factor_data, step_t)
        order = sorted(units)
        cost_c = {sid: _to_scaled_int(units[sid][0]) for sid in order}
        net_c = {sid: _to_scaled_int(units[sid][1]) for sid in order}
        supplies_by_id = {s.supply_id: s for s in eligible}
        caps = {sid: min(math.floor(((supplies_by_id[sid].available_mass_t or 0.0) / step_t) + 1e-9),
                         total_steps)
                for sid in order}
        for target in case["frontier"][:8]:
            c = target["_cost_full"]
            n = target["_net_full"]
            s_share = target["_share_full"] or 1.0
            model = cp_model.CpModel()
            k, used = {}, {}
            for sid in order:
                k[sid] = model.NewIntVar(0, caps[sid], "k_" + sid)
                used[sid] = model.NewBoolVar("u_" + sid)
                model.Add(k[sid] >= 1).OnlyEnforceIf(used[sid])
                model.Add(k[sid] == 0).OnlyEnforceIf(used[sid].Not())
            model.Add(sum(k.values()) == total_steps)
            model.Add(sum(used.values()) >= 2)
            cost_expr = sum(cost_c[sid] * k[sid] for sid in order)
            net_expr = sum(net_c[sid] * k[sid] for sid in order)
            # 支配条件（与 _nondominated_mix 同式，整数化）：不劣于（带容差）+ 至少一维严格更优
            model.Add(cost_expr <= math.floor((c + DOM_TOL_COST_NET) * CP_SCALE) + 1)
            model.Add(net_expr >= math.ceil((n - DOM_TOL_COST_NET) * CP_SCALE) - 1)
            share_bound = math.floor((s_share + DOM_TOL_SHARE) * total_steps + 1e-6)
            for sid in order:
                model.Add(k[sid] <= share_bound)
            b_cost, b_net, b_share = model.NewBoolVar("bc"), model.NewBoolVar("bn"), model.NewBoolVar("bs")
            model.Add(cost_expr <= math.floor((c - DOM_TOL_COST_NET) * CP_SCALE)).OnlyEnforceIf(b_cost)
            model.Add(net_expr >= math.ceil((n + DOM_TOL_COST_NET) * CP_SCALE)).OnlyEnforceIf(b_net)
            strict_share = math.ceil((s_share - DOM_TOL_SHARE) * total_steps - 1e-6) - 1
            for sid in order:
                model.Add(k[sid] <= strict_share).OnlyEnforceIf(b_share)
            model.Add(sum([b_cost, b_net, b_share]) >= 1)
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            status = solver.Solve(model)
            self.assertEqual(status, cp_model.INFEASIBLE,
                             f"seed={seed} 前沿点 {target['allocation']} 存在支配解，穷举疑漏解")


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools 未安装")
class SolverPathBehavior(unittest.TestCase):
    """求解路径行为契约：小规模不变、超限 optimal、无求解器回退 v12、量化无损。"""

    def make_case_over_threshold(self, n=8, total_steps=40, seed=7):
        rng = random.Random(seed)
        demand = make_demand(total_steps * 0.5)
        eligible = [make_supply(rng, i, total_steps, 0.5) for i in range(1, n + 1)]
        return demand, eligible, make_factors()

    def test_small_case_unchanged_enumeration(self):
        """组合数未超限：行为与 v12 完全一致（穷举、is_complete、无 solver_status）。"""
        demand = make_demand(15.0)
        eligible = [make_supply(random.Random(i), i, 30, 0.5) for i in range(1, 3)]
        for s in eligible:
            s.available_mass_t = 8.0  # 显式产能：2×8t ≥ 15t，保证组合可行
        factor_data = make_factors()
        feasible, frontier = enumerate_ground_truth(demand, eligible, factor_data, 0.5)
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertNotIn("solver_status", mix)
        self.assertTrue(mix["is_complete"])
        self.assertEqual(mix["feasible_count"], len(feasible))
        self.assertEqual([p["option_id"] for p in mix["frontier"]],
                         [p["option_id"] for p in frontier])

    def test_over_threshold_real_limit_optimal(self):
        """真实上限（未打补丁）8 供给 × 40 步 ≈ 6290 万组合：v14 完整前沿路径
        solver_status=optimal；该规模前沿点数超过 FRONTIER_CAP=500 → 如实截断
        （is_complete=False、truncated=True、note 标注）；成本锚点 ≤ 贪心可行解。"""
        demand, eligible, factor_data = self.make_case_over_threshold()
        combinations = math.comb(40 + len(eligible) - 1, len(eligible) - 1)
        self.assertGreater(combinations, material.MAX_COMBINATIONS)
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal")
        self.assertTrue(mix["truncated"])
        self.assertFalse(mix["is_complete"])
        self.assertGreater(mix["solver"]["frontier_points"], 300)
        self.assertLessEqual(len(mix["frontier"]), material.FRONTIER_CAP)
        self.assertIn("FRONTIER_CAP", mix["note"])
        self.assertIsNone(mix["feasible_count"])
        self.assertTrue(mix["frontier"])
        greedy = _greedy_feasible_plan(demand, eligible, factor_data)
        if greedy and greedy.get("computable"):
            best_cost = min(p["_cost_full"] for p in mix["frontier"])
            # 贪心返回展示舍入值（2 位小数），留 0.005 舍入容差
            self.assertLessEqual(best_cost, greedy["total_cost_cny"] + 0.005 + 1e-9)
        for p in mix["frontier"]:
            self.assertTrue(p["computable"])
            self.assertGreaterEqual(len(p["allocation"]), 2)

    def test_deterministic_repeat(self):
        demand, eligible, factor_data = self.make_case_over_threshold(seed=8)
        a = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        b = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertEqual([p["option_id"] for p in a["frontier"]],
                         [p["option_id"] for p in b["frontier"]])
        self.assertEqual(a["frontier"][0]["allocation"], b["frontier"][0]["allocation"])

    def test_fallback_without_ortools_matches_v12(self):
        """未安装 OR-Tools（模拟）：回退贪心，结果键与 v12 契约一致。"""
        demand, eligible, factor_data = self.make_case_over_threshold()
        with mock.patch.object(material, "_import_cp_sat", return_value=None):
            mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertEqual(mix.get("solver_status"), "feasible")
        self.assertIn("未证实最优", mix.get("optimality") or "")
        self.assertFalse(mix["is_complete"])
        self.assertIn("error", mix)
        self.assertIn("超过上限", mix["error"])
        self.assertIsNotNone(mix.get("best_effort_plan"))

    def test_greedy_fallback_options_contract_no_crash(self):
        """v13 修复回归：未安装 ortools 的超限任务 + 有价单供供给——
        回退组合选项必须携带 _cost_full，greedy_report 不再 TypeError
        （v12 潜伏缺陷：None 混入 min() 崩溃，离线部署恰好踩中）。"""
        demand, eligible, factor_data = self.make_case_over_threshold()
        big = make_supply(random.Random(77), 9, 40, 0.5)
        big.available_mass_t = 25.0  # 具备单供资格（≥需求全部产能）
        big.price_cny_per_t = 4900.0
        big.supplier_name = "单供大户（测试）"
        with mock.patch.object(material, "_import_cp_sat", return_value=None):
            options = build_options(demand, eligible + [big], factor_data)
            combos = [o for o in options if o["kind"] == "combo"]
            self.assertTrue(combos, "超限回退场景应产生组合选项（贪心可行解）")
            for option in combos:
                self.assertIsNotNone(option.get("_cost_full"),
                                     "回退组合选项缺 _cost_full（会污染 min()）")
                self.assertIsNotNone(option.get("total_cost_cny"))
            report = greedy_report(demand, eligible + [big], factor_data, None)
        self.assertTrue(report.get("computable"))
        self.assertIsNotNone(report.get("enumeration_best_cost"))
        self.assertIsNotNone(report.get("total_cost_cny"))

    def test_unpriced_supplier_excluded_with_reason(self):
        """缺价批次不进求解器，原因如实列入 excluded_supplies，不影响其余最优性。"""
        rng = random.Random(21)
        demand = make_demand(20.0)
        eligible = [make_supply(rng, i, 40, 0.5) for i in range(1, 8)]
        unpriced = make_supply(random.Random(99), 8, 40, 0.5)
        unpriced.price_cny_per_t = None
        eligible.append(unpriced)
        factor_data = make_factors()
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal")
        excluded_ids = {e["supply_id"] for e in mix["solver"]["excluded_supplies"]}
        self.assertIn(unpriced.supply_id, excluded_ids)
        for plan in mix["frontier"]:
            self.assertNotIn(unpriced.supply_id, plan["allocation"])

    def test_solver_infeasible_falls_back_to_greedy_shape(self):
        """可计算产能填不满需求：求解器弃用 → 回退既有贪心/无解说明（v12 行为）。"""
        demand = make_demand(20.0)
        small = make_supply(random.Random(31), 1, 40, 0.5)
        small.available_mass_t = 6.0
        big_unpriced = make_supply(random.Random(32), 2, 40, 0.5)
        big_unpriced.available_mass_t = 30.0
        big_unpriced.price_cny_per_t = None
        factor_data = make_factors()
        with mock.patch.object(material, "MAX_COMBINATIONS", 10):  # 2 供给 40 步 = 41 组合 > 10
            mix = simulate_supply_mix(demand, [small, big_unpriced], factor_data,
                                      step_t=0.5, max_plans=None)
        self.assertEqual(mix.get("solver_status"), "feasible")
        self.assertIn("error", mix)

    def test_options_integration_over_threshold(self):
        """build_options/代表方案在超限场景下携带 CP-SAT 锚点组合方案。"""
        demand, eligible, factor_data = self.make_case_over_threshold(seed=9)
        options = build_options(demand, eligible, factor_data)
        combos = [o for o in options if o["kind"] == "combo"]
        self.assertTrue(combos)
        self.assertTrue(any("CP-SAT" in (o.get("note") or "") for o in combos))
        reps = representative_options(options)
        self.assertTrue(any(o["kind"] == "combo" for o in reps))
        best_mix_cost = min(o["_cost_full"] for o in combos)
        greedy = _greedy_feasible_plan(demand, eligible, factor_data)
        if greedy and greedy.get("computable"):
            self.assertLessEqual(best_mix_cost, greedy["total_cost_cny"] + 0.005 + 1e-9)

    def test_lossy_yield_quantization_still_exact(self):
        """收率 95%（系数含 1/0.95 非十进制循环小数）：量化后锚点仍与穷举逐位一致。"""
        rng = random.Random(41)
        demand = make_demand(10.0)  # 20 步
        eligible = []
        for i in range(1, 5):
            s = make_supply(rng, i, 20, 0.5)
            if i <= 2:
                s.yield_pct = 95.0
            eligible.append(s)
        factor_data = make_factors()
        feasible, frontier = enumerate_ground_truth(demand, eligible, factor_data, 0.5)
        winner = min(frontier, key=lambda p: anchor_key("cost", p))
        self.assertEqual(
            sum(1 for p in frontier if rounded_tuple(p) == rounded_tuple(winner)), 1)
        with mock.patch.object(material, "MAX_COMBINATIONS", SOLVER_THRESHOLD):
            mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal")
        cost_anchor = mix["frontier"][0]
        self.assertEqual(cost_anchor["allocation"], winner["allocation"])
        self.assertEqual(cost_anchor["_cost_full"], winner["_cost_full"])

    def test_to_scaled_int_rounding(self):
        self.assertEqual(_to_scaled_int(0.1234567), 123457)
        self.assertEqual(_to_scaled_int(0.1234564), 123456)
        self.assertEqual(_to_scaled_int(1.5, scale=100), 150)
        self.assertEqual(_to_scaled_int(-0.5, scale=1), 0)  # 银行家舍入：半数取偶

    def test_demo_case_stays_enumeration_and_frozen_value(self):
        """固定演示案例不受影响：仍走穷举（无 solver_status、is_complete），
        基准 83,025 元冻结锚点不变，组合前沿最低成本快照 59,941.25 不变。
        （试点端固定案例的 64,182 元锚点由 Windows E2E e2e_v12.py 四方核对覆盖。）"""
        from src.material import demo_demand, demo_supplies
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = material.load_material_factors()
        options = build_options(demand, supplies, factor_data)
        baseline = next(o for o in options if o["kind"] == "baseline")
        self.assertEqual(baseline["total_cost_cny"], 83025.0)
        eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
        mix = simulate_supply_mix(demand, eligible, factor_data, step_t=0.5, max_plans=None)
        self.assertNotIn("solver_status", mix)
        self.assertTrue(mix["is_complete"])
        best = min(mix["frontier"], key=lambda p: p["_cost_full"])
        self.assertEqual(best["total_cost_cny"], 59941.25)


if __name__ == "__main__":
    unittest.main()
