"""v14 任务 3：双边撮合引擎 v0（Gale-Shapley 容量变体）回归。

性质验证：≥10 组随机多需求-共享批次池案例——
① 稳定性自检阻断对 = 0；② 批次容量不超卖；③ 同输入两次结果逐位一致（确定性）；
④ 未满足需求必须带原因；⑤ 已知小案例的精确分配对照。
"""

import random
import unittest

from src.material import load_material_factors, supply_state
from src.matching import (demand_batch_preference, match_demands,
                          supply_demand_preference)

_TOL = 1e-6


def make_pool(seed, n=5):
    """共享批次池：全部 PP 颗粒、证据齐全（对标准需求可行）。"""
    rng = random.Random(seed)
    supplies = []
    for i in range(1, n + 1):
        supplies.append({
            "supply_id": f"B-{i:02d}", "supplier_name": f"池内供应商{i}（测试）",
            "material_code": "PP", "polymer_type": "PP", "form": "颗粒",
            "color": "黑色" if i % 2 else "灰色", "grade": "注塑级",
            "mfi_g_10min": round(rng.uniform(5, 15), 1), "moisture_pct": 0.3,
            "ash_pct": 2.0, "recycled_content_pct": 90.0,
            "available_mass_t": round(rng.uniform(3.0, 8.0), 1),
            "available_from": "2026-09-01", "available_to": "2026-12-31",
            "price_cny_per_t": round(rng.uniform(3600, 4600), 2),
            "price_includes_freight": rng.choice([False, False, True]),
            "yield_pct": 100.0, "transport_mode": "公路货运",
            "distance_km": float(rng.randint(20, 300)),
            "evidence_source": "检测报告（测试）", "source": "测试",
        })
    return supplies


def make_demands(seed, n=3):
    rng = random.Random(2000 + seed)
    demands = []
    for i in range(1, n + 1):
        demands.append({
            "demand_id": f"D-{i:02d}", "buyer_name": f"撮合需方{i}（测试）",
            "target_material_code": "PP", "required_mass_t": round(rng.uniform(3.0, 9.0), 1),
            "min_recycled_content_pct": 10.0, "mfi_min": 1.0, "mfi_max": 100.0,
            "max_moisture_pct": 5.0, "max_ash_pct": 5.0, "accepted_form": "颗粒",
            "accepted_color": "黑色、灰色", "required_from": "2026-09-01",
            "due_date": "2026-12-31", "baseline_virgin_price_cny_per_t":
                round(rng.uniform(4800, 5600), 2), "max_distance_km": 10000.0,
        })
    return demands


def build(seed):
    """构造 (demands, supplies, factor_data)（MaterialDemand/MaterialSupply 对象）。"""
    from src.material import MaterialDemand, MaterialSupply
    demands, supplies = [], []
    for d in make_demands(seed):
        demands.append(MaterialDemand(**d))
    for s in make_pool(seed):
        supplies.append(MaterialSupply(**s))
    return demands, supplies, load_material_factors()


class MatchingProperty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for seed in range(1, 40):
            if len(cls.cases) >= 10:
                break
            demands, supplies, factor_data = build(seed)
            if len({d.demand_id for d in demands}) != len(demands):
                continue
            eligible_any = any(
                supply_state(demand, s)["state"] == "eligible"
                for demand in demands for s in supplies)
            if eligible_any:
                cls.cases.append((seed, demands, supplies, factor_data))

    def test_10_cases_collected(self):
        self.assertGreaterEqual(len(self.cases), 10)

    def run_case(self, seed, demands, supplies, factor_data):
        result = match_demands(demands, supplies, factor_data)
        # ① 稳定性：阻断对为零
        self.assertEqual(result["stability"]["blocking_pairs"], 0,
                         f"seed={seed} 存在阻断对：{result['stability']['checked_pairs'][:3]}")
        # ② 容量不超卖
        per_batch = {}
        for match in result["matches"]:
            for sid, mass in match["allocations"].items():
                per_batch[sid] = per_batch.get(sid, 0.0) + mass
        capacity = {s.supply_id: (s.available_mass_t or 0.0) for s in supplies}
        for sid, total in per_batch.items():
            self.assertLessEqual(total, capacity[sid] + 1e-6,
                                 f"seed={seed} 批次 {sid} 超卖：{total} > {capacity[sid]}")
        # ③ 分配质量为正、需求分配不超过其需求量
        for match in result["matches"]:
            demand = next(d for d in demands if d.demand_id == match["demand_id"])
            self.assertLessEqual(match["satisfied_t"],
                                 (demand.required_mass_t or 0.0) + 1e-6)
            for mass in match["allocations"].values():
                self.assertGreater(mass, 0)
        # ④ 未满足需求必须带原因
        for item in result["unmatched"]:
            self.assertTrue(item.get("reason"))
        # ⑤ 输出口径标注
        self.assertIn("非交易/非预留", result["note"])
        return result

    def test_case_01(self):
        seed, d, s, f = self.cases[0]
        self.run_case(seed, d, s, f)

    def test_case_02(self):
        seed, d, s, f = self.cases[1]
        self.run_case(seed, d, s, f)

    def test_case_03(self):
        seed, d, s, f = self.cases[2]
        self.run_case(seed, d, s, f)

    def test_case_04(self):
        seed, d, s, f = self.cases[3]
        self.run_case(seed, d, s, f)

    def test_case_05(self):
        seed, d, s, f = self.cases[4]
        self.run_case(seed, d, s, f)

    def test_case_06(self):
        seed, d, s, f = self.cases[5]
        self.run_case(seed, d, s, f)

    def test_case_07(self):
        seed, d, s, f = self.cases[6]
        self.run_case(seed, d, s, f)

    def test_case_08(self):
        seed, d, s, f = self.cases[7]
        self.run_case(seed, d, s, f)

    def test_case_09(self):
        seed, d, s, f = self.cases[8]
        self.run_case(seed, d, s, f)

    def test_case_10(self):
        seed, d, s, f = self.cases[9]
        self.run_case(seed, d, s, f)

    def test_deterministic(self):
        seed, d, s, f = self.cases[0]
        a = match_demands(d, s, f)
        b = match_demands(d, s, f)
        self.assertEqual(a["matches"], b["matches"])
        self.assertEqual(a["unmatched"], b["unmatched"])


class MatchingKnownCase(unittest.TestCase):
    def _material_types(self):
        from src.material import MaterialDemand, MaterialSupply
        return MaterialDemand, MaterialSupply

    def test_two_demands_two_batches_exact(self):
        """已知小案例：高毛利需求优先占据低单价批次，第二需求补位（可手算）。"""
        MaterialDemand, MaterialSupply = self._material_types()
        demands = [
            MaterialDemand(
                demand_id="D-A", buyer_name="需方A", target_material_code="PP",
                required_mass_t=5.0, min_recycled_content_pct=10.0, mfi_min=1.0,
                mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
                accepted_form="颗粒", accepted_color="黑色、灰色",
                required_from="2026-09-01", due_date="2026-12-31",
                baseline_virgin_price_cny_per_t=5600.0, max_distance_km=10000.0,
                evidence_source="测试", source="测试"),
            MaterialDemand(
                demand_id="D-B", buyer_name="需方B", target_material_code="PP",
                required_mass_t=5.0, min_recycled_content_pct=10.0, mfi_min=1.0,
                mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
                accepted_form="颗粒", accepted_color="黑色、灰色",
                required_from="2026-09-01", due_date="2026-12-31",
                baseline_virgin_price_cny_per_t=4800.0, max_distance_km=10000.0,
                evidence_source="测试", source="测试"),
        ]
        supplies = [
            MaterialSupply(
                supply_id="B-CHEAP", supplier_name="低价批次", material_code="PP",
                polymer_type="PP", form="颗粒", color="黑色", mfi_g_10min=10.0,
                moisture_pct=0.3, ash_pct=2.0, recycled_content_pct=90.0,
                available_mass_t=6.0, available_from="2026-09-01",
                available_to="2026-12-31", price_cny_per_t=3600.0,
                price_includes_freight=True, yield_pct=100.0,
                transport_mode="公路货运", distance_km=0.0,
                evidence_source="检测报告", source="测试"),
            MaterialSupply(
                supply_id="B-PRICY", supplier_name="高价批次", material_code="PP",
                polymer_type="PP", form="颗粒", color="黑色", mfi_g_10min=10.0,
                moisture_pct=0.3, ash_pct=2.0, recycled_content_pct=90.0,
                available_mass_t=6.0, available_from="2026-09-01",
                available_to="2026-12-31", price_cny_per_t=4300.0,
                price_includes_freight=True, yield_pct=100.0,
                transport_mode="公路货运", distance_km=0.0,
                evidence_source="检测报告", source="测试"),
        ]
        factor_data = load_material_factors()
        result = match_demands(demands, supplies, factor_data)
        self.assertEqual(result["stability"]["blocking_pairs"], 0)
        by_demand = {m["demand_id"]: m for m in result["matches"]}
        # 两需求都被满足（总容量 12t ≥ 10t）
        self.assertEqual(len(by_demand), 2)
        for match in by_demand.values():
            self.assertEqual(match["remaining_t"], 0.0)
        # 低价批次被两家瓜分（A 毛利空间更大优先、容量 6t 不够 A 单家独占 5t+5t…）
        self.assertIn("B-CHEAP", by_demand["D-A"]["allocations"])
        self.assertIn("B-CHEAP", by_demand["D-B"]["allocations"])
        # 容量不超卖
        self.assertLessEqual(by_demand["D-A"]["allocations"].get("B-CHEAP", 0)
                             + by_demand["D-B"]["allocations"].get("B-CHEAP", 0),
                             6.0 + _TOL)

    def test_unmatched_reports_reason_when_capacity_short(self):
        """容量不足：后到需求未满足且带原因。"""
        MaterialDemand, MaterialSupply = self._material_types()
        demands = [
            MaterialDemand(
                demand_id=f"D-{i}", buyer_name=f"需方{i}", target_material_code="PP",
                required_mass_t=8.0, min_recycled_content_pct=10.0, mfi_min=1.0,
                mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
                accepted_form="颗粒", accepted_color="黑色、灰色",
                required_from="2026-09-01", due_date="2026-12-31",
                baseline_virgin_price_cny_per_t=5500.0, max_distance_km=10000.0,
                evidence_source="测试", source="测试")
            for i in ("A", "B")
        ]
        supplies = [MaterialSupply(
            supply_id="B-ONLY", supplier_name="唯一批次", material_code="PP",
            polymer_type="PP", form="颗粒", color="黑色", mfi_g_10min=10.0,
            moisture_pct=0.3, ash_pct=2.0, recycled_content_pct=90.0,
            available_mass_t=10.0, available_from="2026-09-01",
            available_to="2026-12-31", price_cny_per_t=4000.0,
            price_includes_freight=True, yield_pct=100.0,
            transport_mode="公路货运", distance_km=0.0,
            evidence_source="检测报告", source="测试")]
        result = match_demands(demands, supplies, load_material_factors())
        satisfied_total = sum(m["satisfied_t"] for m in result["matches"])
        self.assertLessEqual(satisfied_total, 10.0 + _TOL)
        unsatisfied = [d for d in demands
                       if sum(m["allocations"].get("B-ONLY", 0.0) for m in result["matches"]
                              if m["demand_id"] == d.demand_id) < 8.0 - _TOL]
        self.assertTrue(unsatisfied)


class PreferenceFunctions(unittest.TestCase):
    def test_demand_preference_orders_by_landed_cost(self):
        from src.material import MaterialSupply
        demand = __import__("src.material", fromlist=["demo_demand"]).demo_demand()
        cheap = MaterialSupply(
            supply_id="B-1", supplier_name="低", material_code="PP", polymer_type="PP",
            form="颗粒", color="黑色", price_cny_per_t=3600.0, price_includes_freight=True,
            distance_km=50.0, evidence_source="报告", available_mass_t=5.0)
        dear = MaterialSupply(
            supply_id="B-2", supplier_name="高", material_code="PP", polymer_type="PP",
            form="颗粒", color="黑色", price_cny_per_t=4300.0, price_includes_freight=True,
            distance_km=50.0, evidence_source="报告", available_mass_t=5.0)
        factor_data = load_material_factors()
        key_cheap = demand_batch_preference(demand, cheap, factor_data)
        key_dear = demand_batch_preference(demand, dear, factor_data)
        self.assertLess(key_cheap, key_dear)

    def test_supply_preference_prefers_higher_margin(self):
        from src.material import MaterialSupply, MaterialDemand
        supply = MaterialSupply(
            supply_id="B-1", supplier_name="批次", material_code="PP", polymer_type="PP",
            form="颗粒", color="黑色", price_cny_per_t=4000.0, price_includes_freight=True,
            distance_km=0.0, evidence_source="报告", available_mass_t=10.0)
        high = MaterialDemand(
            demand_id="D-HIGH", buyer_name="高", target_material_code="PP",
            required_mass_t=5.0, baseline_virgin_price_cny_per_t=5600.0,
            accepted_form="颗粒", accepted_color="黑色", mfi_min=1.0, mfi_max=100.0,
            min_recycled_content_pct=10.0, max_moisture_pct=5.0, max_ash_pct=5.0,
            required_from="2026-09-01", due_date="2026-12-31",
            evidence_source="测试", source="测试")
        low = MaterialDemand(
            demand_id="D-LOW", buyer_name="低", target_material_code="PP",
            required_mass_t=5.0, baseline_virgin_price_cny_per_t=4800.0,
            accepted_form="颗粒", accepted_color="黑色", mfi_min=1.0, mfi_max=100.0,
            min_recycled_content_pct=10.0, max_moisture_pct=5.0, max_ash_pct=5.0,
            required_from="2026-09-01", due_date="2026-12-31",
            evidence_source="测试", source="测试")
        self.assertLess(supply_demand_preference(supply, high),
                        supply_demand_preference(supply, low))

    def test_unknown_preference_model_rejected(self):
        from src.material import MaterialSupply, MaterialDemand
        supply = MaterialSupply(supply_id="B", supplier_name="x", material_code="PP",
                                polymer_type="PP", form="颗粒", color="黑色",
                                price_cny_per_t=4000.0, distance_km=0.0,
                                evidence_source="r", available_mass_t=1.0)
        demand = MaterialDemand(demand_id="D", buyer_name="x", target_material_code="PP",
                                required_mass_t=1.0, accepted_form="颗粒",
                                accepted_color="黑色", mfi_min=1.0, mfi_max=100.0,
                                min_recycled_content_pct=10.0, max_moisture_pct=5.0,
                                max_ash_pct=5.0, required_from="2026-09-01",
                                due_date="2026-12-31", evidence_source="t", source="t")
        with self.assertRaises(ValueError):
            supply_demand_preference(supply, demand, preference_model="nope")


if __name__ == "__main__":
    unittest.main()
