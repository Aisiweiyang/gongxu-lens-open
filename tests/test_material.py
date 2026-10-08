"""material.py 单元测试：净减排手算、基准距离回归、门槛三态、组合、缺价、N-1。"""

import unittest

from src.material import (
    MaterialDemand,
    MaterialSupply,
    build_options,
    demo_demand,
    demo_supplies,
    load_material_factors,
    match_gates,
    n1_stress_test,
    net_emissions,
    representative_options,
    run_material,
    simulate_supply_mix,
    supply_state,
    validate_material_factors,
)
from src.evidence_value import optimistic_supplies

VIRGIN = 1800.0
RECYCLE = 400.0
TRANSPORT = 0.07447
BASELINE_DISTANCE = 100.0


def make_factors():
    return {
        "factors": {
            "DEMO-VIRGIN-PP-PROD": {"factor_id": "DEMO-VIRGIN-PP-PROD", "stage": "virgin_production",
                                    "material_code": "PP", "value": VIRGIN, "unit": "kgCO2e/t",
                                    "boundary": "cradle-to-gate", "region": "中国", "year": "2025",
                                    "source_name": "演示假设因子", "source_url": "", "version": 1,
                                    "grade": "演示假设"},
            "DEMO-PP-RECYCLE-PROCESS": {"factor_id": "DEMO-PP-RECYCLE-PROCESS",
                                        "stage": "recycled_granule_production", "material_code": "PP",
                                        "value": RECYCLE, "unit": "kgCO2e/t", "boundary": "gate-to-gate",
                                        "region": "中国", "year": "2025", "source_name": "演示假设因子",
                                        "source_url": "", "version": 1, "grade": "演示假设"},
            "DEFRA-ARTIC-HGV-DIESEL-2024": {"factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024",
                                            "stage": "transport", "material_code": "ANY",
                                            "value": TRANSPORT, "unit": "kgCO2e/(t·km)", "boundary": "TTW",
                                            "region": "英国", "year": "2024", "applies_to": ["公路货运"],
                                            "source_name": "测试因子", "source_url": "", "version": 1,
                                            "grade": "权威"},
        },
        "baseline_transport_distance_km": BASELINE_DISTANCE,
    }


def make_demand(**overrides):
    base = dict(demand_id="D1", buyer_name="测试需求方", target_material_code="PP",
                application="测试制品", required_mass_t=15.0, min_recycled_content_pct=80.0,
                mfi_min=8.0, mfi_max=16.0, max_moisture_pct=0.5, max_ash_pct=5.0,
                accepted_form="颗粒", accepted_color="黑色、灰色", required_from="2026-09-15",
                due_date="2026-10-15", destination="测试园区",
                baseline_virgin_price_cny_per_t=5500.0, max_distance_km=500.0,
                evidence_source="测试", source="测试")
    base.update(overrides)
    return MaterialDemand(**base)


def make_supply(**overrides):
    base = dict(supply_id="S1", supplier_name="测试供给方", material_code="PP",
                material_name_raw="再生PP颗粒", polymer_type="PP", form="颗粒", color="黑色",
                grade="注塑级", mfi_g_10min=12.0, moisture_pct=0.3, ash_pct=3.0,
                recycled_content_pct=95.0, available_mass_t=20.0, available_from="2026-09-10",
                available_to="2026-10-31", origin="测试园区", price_cny_per_t=4200.0,
                preprocessing="已造粒", transport_mode="公路货运",
                factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
                distance_km=30.0, evidence_source="检测报告", evidence_date="2026-08-15",
                source="测试")
    base.update(overrides)
    return MaterialSupply(**base)


class TestNetEmissions(unittest.TestCase):
    def test_hand_calc(self):
        # E_baseline = 15×(1800 + 100×0.07447)；E_project = 15×400 + 15×30×0.07447
        demand = make_demand()
        supply = make_supply()
        result = net_emissions(demand, supply, 15.0, make_factors())
        self.assertEqual(result["status"], "可计算")
        baseline = 15.0 * (VIRGIN + BASELINE_DISTANCE * TRANSPORT)
        project = 15.0 * RECYCLE + 15.0 * 30.0 * TRANSPORT
        self.assertAlmostEqual(result["baseline_kgco2e"], round(baseline, 2), places=1)
        self.assertAlmostEqual(result["project_kgco2e"], round(project, 2), places=1)
        self.assertAlmostEqual(result["net_avoided_kgco2e"], round(baseline - project, 2), places=1)
        self.assertGreater(result["net_avoided_kgco2e"], 0)

    def test_baseline_distance_only_affects_baseline(self):
        # 改基准运输距离：只改基准排放与净减排，不改变项目运输排放
        factors = make_factors()
        near = net_emissions(make_demand(), make_supply(), 15.0, factors, baseline_distance_km=100.0)
        far = net_emissions(make_demand(), make_supply(), 15.0, factors, baseline_distance_km=500.0)
        self.assertGreater(far["baseline_kgco2e"], near["baseline_kgco2e"])
        self.assertAlmostEqual(far["project_kgco2e"], near["project_kgco2e"], places=6)
        self.assertGreater(far["net_avoided_kgco2e"], near["net_avoided_kgco2e"])

    def test_missing_factor_or_distance_pending(self):
        factors = make_factors()
        del factors["factors"]["DEMO-VIRGIN-PP-PROD"]
        result = net_emissions(make_demand(), make_supply(), 15.0, factors)
        self.assertEqual(result["status"], "待补证据")
        result = net_emissions(make_demand(), make_supply(distance_km=None), 15.0, make_factors())
        self.assertEqual(result["status"], "待补证据")
        self.assertIn("禁止用 0", result["problems"][0])

    def test_granules_delivered_one_to_one(self):
        # 已造粒成品：交付质量 = 分配质量，无产率折算
        result = net_emissions(make_demand(), make_supply(), 15.0, make_factors())
        self.assertAlmostEqual(result["mass_t"], 15.0)


class TestGates(unittest.TestCase):
    def test_demo_states(self):
        result = run_material("demo")
        by_id = {s["supply_id"]: s["state"] for s in result["match_states"]}
        self.assertEqual(by_id["SUP-01"], "eligible")          # 8t 完全匹配（可组合，无单供资格）
        self.assertEqual(by_id["SUP-02"], "pending_evidence")  # 7t 需补检测
        self.assertEqual(by_id["SUP-03"], "ineligible")        # 熔指超范围
        self.assertEqual(by_id["SUP-04"], "eligible")          # 18t 低价单供（脆弱）

    def test_partial_capacity_not_eliminated(self):
        demand = make_demand()
        supply = make_supply(available_mass_t=8.0)
        gates, elimination, pending = match_gates(demand, supply)
        self.assertEqual(elimination, [])
        quantity = next(g for g in gates if g["name"] == "数量")
        self.assertEqual(quantity["status"], "pass")
        self.assertIn("可与其他批次组合", quantity["detail"])


class TestMassConservation(unittest.TestCase):
    def test_all_allocations_sum_to_required(self):
        result = run_material("demo")
        for plan in result["mix"]["plans"]:
            self.assertAlmostEqual(sum(plan["allocation"].values()), 15.0, places=6)
        for option in result["options"]:
            if option.get("allocation"):
                self.assertAlmostEqual(sum(option["allocation"].values()), 15.0, places=6)

    def test_eight_plus_seven_combo(self):
        # 8t + 7t 组合满足 15t，两家都不能单独满足
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        mix = simulate_supply_mix(demand, supplies, factor_data)
        eight_seven = next(
            (p for p in mix["plans"]
             if len(p["allocation"]) == 2
             and sorted(round(v, 1) for v in p["allocation"].values()) == [7.0, 8.0]),
            None,
        )
        self.assertIsNotNone(eight_seven, "应存在 8t+7t 组合方案")
        for supply in supplies:
            if supply.supply_id in ("SUP-01", "SUP-02"):
                self.assertLess(supply.available_mass_t, 15.0)


class TestParetoAndPricing(unittest.TestCase):
    def test_directions_explicit(self):
        # 成本越小越好、净减排越大越好、订单最大份额越小越好
        result = run_material("demo")
        for plan in result["mix"]["plans"]:
            self.assertIsNotNone(plan["total_cost_cny"])
            self.assertIsNotNone(plan["net_avoided_kgco2e"])
            self.assertIsNotNone(plan["order_max_share"])

    def test_missing_price_excluded_from_cost_pareto(self):
        demand = make_demand()
        supplies = [make_supply(supply_id="A", supplier_name="A", available_mass_t=8.0),
                    make_supply(supply_id="B", supplier_name="B", available_mass_t=8.0,
                                price_cny_per_t=None)]
        mix = simulate_supply_mix(demand, supplies, make_factors())
        for plan in mix["plans"]:
            self.assertIsNotNone(plan["total_cost_cny"])  # 含缺价供给的方案被移出前沿
        self.assertTrue(mix.get("plans_unpriced"))
        self.assertIn("缺价", mix["plans_unpriced"][0]["reason"])

    def test_factor_validation(self):
        good = {
            "factors": [
                {"factor_id": "F1", "stage": "virgin_production", "material_code": "PP",
                 "value": 1.0, "unit": "kgCO2e/t", "boundary": "b", "region": "中国",
                 "year": "2025", "source_name": "来源", "source_url": "", "version": 1,
                 "grade": "演示假设"},
            ],
            "baseline_transport_distance_km": 100.0,
        }
        self.assertEqual(validate_material_factors(good), [])
        bad = {
            "factors": [
                {"factor_id": "F1", "stage": "virgin_production", "material_code": "PP",
                 "value": -1, "unit": "kgCO2e/t", "boundary": "b", "region": "中国",
                 "year": "2025", "source_name": "来源", "source_url": "", "version": 1,
                 "grade": "演示假设"},
            ],
            "baseline_transport_distance_km": 100.0,
        }
        self.assertTrue(validate_material_factors(bad))


class TestOptions(unittest.TestCase):
    def test_representatives_dedupe_and_baseline(self):
        result = run_material("demo")
        labels = [o["label"] for o in result["representatives"]]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(any(o["kind"] == "baseline" for o in result["representatives"]))

    def test_single_supply_requires_full_capacity(self):
        result = run_material("demo")
        single_labels = [o["label"] for o in result["options"] if o["kind"] == "single"]
        for label in single_labels:
            supply = next(s for s in result["supplies"] if s.supplier_name == label)
            self.assertGreaterEqual(supply.available_mass_t, 15.0)

    def test_premium_hand_calc(self):
        # 业务理由：成本改为到厂成本分项口径。
        # 项目侧 = 材料 3800×15 + 运费 15×480×0.35 + 装卸 15×20 = 57,000+2,520+300 = 59,820
        # 基准侧 = 原生 5500×15 + 基准运费 15×100×0.35 = 82,500+525 = 83,025
        # 旧口径（纯材料价）57,000 与 82,500 仅保留在成本账本的「材料价」分项。
        demand = demo_demand()
        supply = demo_supplies()[3]  # SUP-04：18t、3800、480km
        factor_data = load_material_factors()
        options = build_options(demand, [supply], factor_data)
        sup04 = next(o for o in options if o["label"] == supply.supplier_name)
        self.assertAlmostEqual(sup04["total_cost_cny"], 3800.0 * 15.0 + 15 * 480 * 0.35 + 15 * 20.0, places=2)
        baseline_total = 5500.0 * 15.0 + 15 * 100 * 0.35
        self.assertAlmostEqual(sup04["cost_delta_cny"], sup04["total_cost_cny"] - baseline_total, places=2)
        # 账本分项：材料价分项保留旧口径 57,000
        material_line = next(l for l in sup04["cost_ledger"]["lines"] if l["item"] == "材料价")
        self.assertAlmostEqual(material_line["amount"], 3800.0 * 15.0, places=2)
        self.assertGreater(sup04["net_avoided_kgco2e"], 0)
        self.assertIsNotNone(sup04["marginal_abatement_cost_cny_per_tco2e"])
        self.assertEqual(sup04["category"], "成本与碳双降")


class TestN1(unittest.TestCase):
    def test_single_supplier_is_fragile(self):
        result = run_material("demo")
        n1 = result["n1_stress"]
        self.assertGreaterEqual(len(n1), 2)
        for entry in n1:
            self.assertIn("min_delivery_service_rate", entry)
            self.assertTrue(0.0 <= entry["min_delivery_service_rate"] <= 1.0)

    def test_evidence_unlocks_resilient_combo(self):
        # 现状世界：外省单供断供时只有本地 8t 备份 → 服务率 53%
        demand = demo_demand()
        factor_data = load_material_factors()
        current = run_material("demo")
        current_single = next(e for e in current["n1_stress"] if e["kind"] == "single")
        self.assertLess(current_single["min_delivery_service_rate"], 1.0)
        # 补证据后的世界：邻区批次（7t）检测通过 → 组合含三家，N-1 下服务率 100%
        supplies = demo_supplies()
        for supply in supplies:
            if supply.supply_id == "SUP-02":
                supply.evidence_source = "检测报告（演示，补证后）"
                supply.evidence_date = "2026-09-05"
        options = build_options(demand, supplies, factor_data)
        reps = representative_options(options)
        n1 = n1_stress_test(demand, supplies, factor_data, options=reps)
        combo = next((e for e in n1 if e["kind"] == "combo"), None)
        self.assertIsNotNone(combo)
        self.assertAlmostEqual(combo["min_delivery_service_rate"], 1.0, places=4)
        self.assertGreater(combo["min_delivery_service_rate"],
                           current_single["min_delivery_service_rate"])

    def test_n1_conservative_service_rate_locked(self):
        """锁定：保守情景所有代表组合断供服务率 0.5333（答辩「低价脆弱」侧对照数字）。"""
        result = run_material("demo")
        for entry in result["n1_stress"]:
            self.assertAlmostEqual(entry["min_delivery_service_rate"], 0.5333, places=4)

    def test_n1_optimistic_full_service_locked(self):
        """锁定：乐观情景代表组合断供服务率全部 100%，含答辩故事线组合（本地 8t+邻区 7t）。"""
        result = run_material("demo")
        demand = result["demand"]
        factor_data = load_material_factors()
        optimistic = optimistic_supplies(demand, result["supplies"])
        options = build_options(demand, optimistic, factor_data)
        n1 = n1_stress_test(demand, optimistic, factor_data,
                            options=representative_options(options))
        for entry in n1:
            self.assertAlmostEqual(entry["min_delivery_service_rate"], 1.0, places=6)
        # 答辩故事线组合（成本 61600）：显式构造后也须为 100%
        by_id = {s.supply_id: s.supplier_name for s in result["supplies"]}
        story_combo = {"label": "组合：本地 8 t + 邻区 7 t（乐观情景）", "kind": "combo",
                       "allocation": {by_id["SUP-01"]: 8, by_id["SUP-02"]: 7}}
        story_n1 = n1_stress_test(demand, optimistic, factor_data, options=[story_combo])
        self.assertEqual(len(story_n1), 1)
        self.assertAlmostEqual(story_n1[0]["min_delivery_service_rate"], 1.0, places=6)


class TestModes(unittest.TestCase):
    def test_demo_mode_runs(self):
        result = run_material("demo")
        self.assertEqual(result["data_mode"], "demo")
        self.assertTrue(result["warnings"])

    def test_online_mode_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            run_material("online")
        self.assertIn("未实现", str(ctx.exception))

    def test_local_mode_requires_files(self):
        with self.assertRaises(ValueError):
            run_material("local")  # data/input 下无文件时拒绝回退演示


if __name__ == "__main__":
    unittest.main()
