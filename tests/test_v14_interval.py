"""v14 任务 1：净减排区间传播（确定性 UQ）回归。

互验方式：以演示因子结构为底、给部分因子加 value_min/value_max，手算区间端点
逐位对照；属性断言「点估计必在区间内」「区间随因子区间加宽而单调加宽」；
demo 因子无区间时输出不携带区间键（数值冻结锚点不受影响）。
"""

import copy
import unittest

from src.ledger import (factor_data_has_ranges, net_emissions_ledger,
                        net_interval)
from src.material import (MaterialDemand, MaterialSupply, build_options,
                          demo_demand, demo_supplies, load_material_factors,
                          validate_material_factors)


def base_factors(**overrides):
    factors = {
        "baseline_transport_distance_km": 100.0,
        "factors": {
            "V-PP": {"factor_id": "V-PP", "role": "baseline_material",
                     "stage": "virgin_production", "material_code": "PP",
                     "value": 1800.0, "unit": "kgCO2e/t", "boundary": "cradle-to-gate",
                     "region": "中国", "year": 2025, "source_name": "演示假设",
                     "source_url": "", "version": 1, "grade": "演示假设"},
            "R-PP": {"factor_id": "R-PP", "role": "project_material",
                     "stage": "recycled_granule_production", "material_code": "PP",
                     "value": 400.0, "unit": "kgCO2e/t", "boundary": "gate-to-gate",
                     "region": "中国", "year": 2025, "source_name": "演示假设",
                     "source_url": "", "version": 1, "grade": "演示假设"},
            "T-2025": {"factor_id": "T-2025", "role": "transport", "stage": "transport",
                       "material_code": "ANY", "value": 0.07447, "unit": "kgCO2e/(t·km)",
                       "boundary": "TTW", "region": "英国", "year": 2025,
                       "source_name": "演示假设", "source_url": "", "version": 1,
                       "grade": "演示假设", "applies_to": ["公路货运"],
                       "transport_included_in_factor": False},
        },
        "cost_assumptions": {"freight_rate_cny_per_tkm": 0.35,
                             "handling_fee_cny_per_t": 20.0,
                             "inspection_fee_per_batch_cny": 800.0,
                             "default_yield": 1.0},
    }
    factors["factors"].update(overrides.get("factors", {}))
    for key, value in overrides.items():
        if key != "factors":
            factors[key] = value
    return factors


def make_supply(distance_km=60.0, mass=None):
    return MaterialSupply(
        supply_id="S-01", supplier_name="区间供应商（测试）", material_code="PP",
        polymer_type="PP", form="颗粒", color="黑色", mfi_g_10min=10.0,
        moisture_pct=0.3, ash_pct=2.0, recycled_content_pct=90.0,
        available_mass_t=mass or 20.0, available_from="2026-09-01",
        available_to="2026-12-31", price_cny_per_t=4000.0,
        price_includes_freight=False, yield_pct=100.0, transport_mode="公路货运",
        factor_id="T-2025", distance_km=distance_km,
        evidence_source="检测报告（测试）", source="测试")


def make_demand(mass=10.0):
    return MaterialDemand(
        demand_id="IV-001", buyer_name="区间需方（测试）", target_material_code="PP",
        application="区间互验", required_mass_t=mass, min_recycled_content_pct=10.0,
        mfi_min=1.0, mfi_max=100.0, max_moisture_pct=5.0, max_ash_pct=5.0,
        accepted_form="颗粒", accepted_color="黑色", required_from="2026-09-01",
        due_date="2026-12-31", destination="测试园区",
        baseline_virgin_price_cny_per_t=5500.0, max_distance_km=10000.0,
        evidence_source="测试规格书", source="测试")


class NetIntervalHandComputation(unittest.TestCase):
    def test_recycled_range_hand_computed(self):
        """单因子区间：端点与手算逐位一致（同一浮点运算序列）。"""
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=350.0, value_max=450.0)})
        demand, supply = make_demand(10.0), make_supply(60.0)
        mass = 10.0
        v = 1800.0
        t = 0.07447
        d = 60.0
        d_base = 100.0
        expected_lo = mass * v + mass * d_base * t - (mass * 450.0 + mass * d * t)
        expected_hi = mass * v + mass * d_base * t - (mass * 350.0 + mass * d * t)
        result = net_interval(demand, supply, mass, factors)
        self.assertEqual(result["status"], "可计算")
        lo, hi = result["interval_kgco2e"]
        self.assertEqual(lo, expected_lo)
        self.assertEqual(hi, expected_hi)
        # 点估计必在区间内（同输入同账本）
        point = net_emissions_ledger(demand, supply, mass, factors)["_net_full"]
        self.assertLessEqual(lo - 1e-9, point)
        self.assertLessEqual(point, hi + 1e-9)

    def test_transport_range_combined(self):
        """运输因子也带区间：基准侧与项目侧端点组合手算对照。"""
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=380.0, value_max=420.0),
            "T-2025": dict(base_factors()["factors"]["T-2025"],
                           value_min=0.07, value_max=0.08)})
        demand, supply = make_demand(8.0), make_supply(120.0)
        mass, v, d, d_base = 8.0, 1800.0, 120.0, 100.0
        # 基准侧：原生点估计 + 基准运输区间 [0.07, 0.08]
        base_lo = mass * v + mass * d_base * 0.07
        base_hi = mass * v + mass * d_base * 0.08
        # 项目侧：再生区间 + 候选运输区间（候选用供给指定因子 T-2025）
        proj_lo = mass * 380.0 + mass * d * 0.07
        proj_hi = mass * 420.0 + mass * d * 0.08
        result = net_interval(demand, supply, mass, factors)
        lo, hi = result["interval_kgco2e"]
        self.assertEqual(lo, base_lo - proj_hi)
        self.assertEqual(hi, base_hi - proj_lo)

    def test_monotone_widening(self):
        """因子区间加宽 → 净减排区间单调加宽。"""
        demand, supply = make_demand(10.0), make_supply(60.0)
        narrow = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=390.0, value_max=410.0)})
        wide = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=350.0, value_max=450.0)})
        lo_n, hi_n = net_interval(demand, supply, 10.0, narrow)["interval_kgco2e"]
        lo_w, hi_w = net_interval(demand, supply, 10.0, wide)["interval_kgco2e"]
        self.assertLessEqual(lo_w, lo_n)
        self.assertGreaterEqual(hi_w, hi_n)

    def test_status_parity_with_point_ledger(self):
        """状态判定与点估计账本一致：距离缺失 → 双双待补证据。"""
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=350.0, value_max=450.0)})
        demand = make_demand(10.0)
        supply = make_supply(None)
        point = net_emissions_ledger(demand, supply, 10.0, factors)
        interval = net_interval(demand, supply, 10.0, factors)
        self.assertNotEqual(point["status"], "可计算")
        self.assertEqual(interval["status"], "待补证据")
        self.assertIsNone(interval["interval_kgco2e"])


class FactorRangeValidation(unittest.TestCase):
    @staticmethod
    def as_yaml(factors):
        """validate_material_factors 吃 YAML 原始形态（factors 为 list）。"""
        return {**factors, "factors": list(factors["factors"].values())}

    def test_reject_single_sided_range(self):
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"], value_min=350.0)})
        problems = validate_material_factors(self.as_yaml(factors))
        self.assertTrue(any("成对出现" in p for p in problems))

    def test_reject_value_outside_range(self):
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=350.0, value_max=390.0)})  # value=400 越上界
        problems = validate_material_factors(self.as_yaml(factors))
        self.assertTrue(any("必须落在" in p for p in problems))

    def test_reject_negative_min(self):
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=-1.0, value_max=450.0)})
        problems = validate_material_factors(self.as_yaml(factors))
        self.assertTrue(any("非负有限" in p for p in problems))

    def test_valid_range_passes(self):
        factors = base_factors(factors={
            "R-PP": dict(base_factors()["factors"]["R-PP"],
                         value_min=350.0, value_max=450.0)})
        self.assertEqual(validate_material_factors(self.as_yaml(factors)), [])


class OptionIntervalSurface(unittest.TestCase):
    def test_demo_has_no_interval_keys_and_frozen_values(self):
        """demo 因子无区间：选项不带区间键，冻结锚点不变。"""
        demand, supplies = demo_demand(), demo_supplies()
        factor_data = load_material_factors()
        self.assertFalse(factor_data_has_ranges(factor_data))
        options = build_options(demand, supplies, factor_data)
        self.assertFalse(any("net_interval_kgco2e" in o for o in options))
        baseline = next(o for o in options if o["kind"] == "baseline")
        self.assertEqual(baseline["total_cost_cny"], 83025.0)

    def test_options_carry_interval_when_ranges_present(self):
        """带区间因子：单供与组合选项携带区间键与注记，点估计落在区间内。"""
        demand, supplies = demo_demand(), demo_supplies()
        factor_data = load_material_factors()
        factor_data = copy.deepcopy(factor_data)
        factor_data["factors"]["DEMO-PP-RECYCLE-PROCESS"]["value_min"] = 350.0
        factor_data["factors"]["DEMO-PP-RECYCLE-PROCESS"]["value_max"] = 450.0
        self.assertTrue(factor_data_has_ranges(factor_data))
        options = build_options(demand, supplies, factor_data)
        with_interval = [o for o in options if "net_interval_kgco2e" in o]
        self.assertTrue(with_interval)
        for option in with_interval:
            lo, hi = option["net_interval_kgco2e"]
            self.assertLessEqual(lo, hi)
            self.assertIn("非统计置信区间", option["net_interval_note"])
            net = option.get("net_avoided_kgco2e")
            if net is not None:
                self.assertLessEqual(lo - 0.01, net)
                self.assertLessEqual(net, hi + 0.01)
        # 基准选项不携带区间（净减排恒为 0）
        baseline = next(o for o in options if o["kind"] == "baseline")
        self.assertNotIn("net_interval_kgco2e", baseline)


if __name__ == "__main__":
    unittest.main()
