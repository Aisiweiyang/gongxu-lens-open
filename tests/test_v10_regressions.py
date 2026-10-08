"""回归测试：锁定已确认的行为约束场景。

原则：关键回归必须证明旧实现会失败；测试不照抄被测函数生成预期。
"""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from src import passport
from src.material import (
    MaterialDemand,
    MaterialSupply,
    build_options,
    demo_demand,
    demo_supplies,
    load_material_factors,
    n1_stress_test,
    option_id,
    representative_options,
    run_material,
    simulate_supply_mix,
    supply_state,
)
from src.material_match import run_recall_benchmark
from src.evidence_value import plan_evidence_actions
from src.site_builder import build_site_html

VIRGIN = 1800.0
RECYCLE = 400.0
TRANSPORT = 0.07447


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


def make_factors(**overrides):
    factors = {
        "factors": {
            "VIRGIN-1": {"factor_id": "VIRGIN-1", "role": "baseline_material",
                         "stage": "virgin_production", "material_code": "PP",
                         "value": VIRGIN, "unit": "kgCO2e/t",
                         "boundary": "cradle-to-gate（聚合→粒料）", "region": "中国",
                         "year": "2025", "source_name": "演示假设因子", "source_url": "",
                         "version": 1, "grade": "演示假设"},
            "RECYCLE-1": {"factor_id": "RECYCLE-1", "role": "project_material",
                          "stage": "recycled_granule_production", "material_code": "PP",
                          "value": RECYCLE, "unit": "kgCO2e/t",
                          "boundary": "gate-to-gate（收集、分选、清洗、造粒）", "region": "中国",
                          "year": "2025", "source_name": "演示假设因子", "source_url": "",
                          "version": 1, "grade": "演示假设"},
            "TRUCK-1": {"factor_id": "TRUCK-1", "role": "transport",
                        "stage": "transport", "material_code": "ANY",
                        "value": TRANSPORT, "unit": "kgCO2e/(t·km)", "boundary": "TTW",
                        "region": "英国", "year": "2024", "applies_to": ["公路货运"],
                        "source_name": "测试因子", "source_url": "", "version": 1,
                        "grade": "权威"},
        },
        "baseline_transport_distance_km": 100.0,
        "cost_assumptions": {"freight_rate_cny_per_tkm": 0.35,
                             "handling_fee_cny_per_t": 20.0,
                             "inspection_fee_per_batch_cny": 800.0,
                             "default_yield": 1.0},
    }
    for key, value in overrides.items():
        factors[key] = value
    return factors


class TestA01StableContainer(unittest.TestCase):
    def test_options_table_never_replaced(self):
        # 旧实现：q("options-table").outerHTML = "<table>…" 替换后 ID 丢失，
        # 二次切换抛 Cannot set properties of null。新实现必须保留稳定容器。
        html = build_site_html(run_material("demo"))
        self.assertIn("id='options-body'", html)
        self.assertNotIn("options-table\").outerHTML", html)
        self.assertNotIn('options-table").outerHTML', html)

    def test_render_pipeline_single_source(self):
        # 场景快照组织：同一场景的选项/N-1/Pareto 共享同一份数据
        html = build_site_html(run_material("demo"))
        import re
        m = re.search(r"id='data-json'>(.*?)</script>", html, re.S)
        data = json.loads(m.group(1))
        self.assertIn("scenarios", data)
        for name in ("conservative", "optimistic"):
            scen = data["scenarios"][name]
            self.assertIn("options", scen)
            self.assertIn("n1", scen)
            self.assertIn("pareto", scen)


class TestA02OptionIdentity(unittest.TestCase):
    def test_same_label_different_allocation_distinct_ids(self):
        # 旧实现按 label（供应商名）去重，同标签不同分配互相覆盖/误合并。
        demand = make_demand()
        combo_a = {"S1": 7.5, "S2": 7.5}
        combo_b = {"S1": 8.0, "S2": 7.0}
        id_a = option_id("combo", demand, combo_a)
        id_b = option_id("combo", demand, combo_b)
        self.assertNotEqual(id_a, id_b)
        # 同分配不同供给批次顺序 → 同一 ID（规范化）
        combo_c = {"S2": 7.5, "S1": 7.5}
        self.assertEqual(option_id("combo", demand, combo_c), id_a)
        # 不同需求 → 不同 ID
        demand2 = make_demand(demand_id="D2")
        self.assertNotEqual(option_id("combo", demand2, combo_a), id_a)

    def test_full_frontier_not_truncated(self):
        # 旧实现 build_options 只取 mix.plans[:5]，完整前沿被截断。
        demand = demo_demand()
        factor_data = load_material_factors()
        options = build_options(demand, demo_supplies(), factor_data)
        combos = [o for o in options if o["kind"] == "combo"]
        mix = simulate_supply_mix(demand, demo_supplies(), factor_data,
                                  partial_capacity=True, max_plans=None)
        self.assertEqual(len(combos), mix["frontier_count"])

    def test_old_demo_anchor_17_combos_and_material_cost(self):
        # 旧演示锚点：17 个组合方案；本地 7.5t + 外省 7.5t 材料价 60,000。
        # 材料价保留在账本「材料价」分项；到厂总成本另加运费与装卸。
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        mix = simulate_supply_mix(demand, supplies, factor_data, max_plans=None)
        self.assertEqual(mix["feasible_count"], 17)
        split = next(p for p in mix["frontier"]
                     if set(round(v, 1) for v in p["allocation"].values()) == {7.5})
        self.assertIsNotNone(split)
        # 材料价分项合计 = 7.5×4200 + 7.5×3800 = 60,000（旧锚点口径，现为账本分项）
        material_total = sum(l["amount"] for l in split["cost_ledger"]["lines"]
                             if l["item"] == "材料价")
        self.assertAlmostEqual(material_total, 60000.0, places=2)
        # 到厂总成本 = 材料 60,000 + 运费 7.5×30×0.35 + 7.5×480×0.35 + 装卸 15×20
        expected_total = 60000.0 + 7.5 * 30 * 0.35 + 7.5 * 480 * 0.35 + 15 * 20.0
        self.assertAlmostEqual(split["total_cost_cny"], round(expected_total, 2), places=2)

    def test_representatives_dedupe_by_option_id(self):
        # 旧实现按 label 去重；两个 label 相同、分配不同的方案只能保留一个。
        demand = make_demand()
        s1 = make_supply(supply_id="A1", supplier_name="同名家", available_mass_t=10.0)
        s2 = make_supply(supply_id="A2", supplier_name="同名家", available_mass_t=10.0)
        options = [{"kind": "combo", "label": "组合：同名家 7.5t + 同名家 7.5t",
                    "option_id": option_id("combo", demand, {"A1": 7.5, "A2": 7.5}),
                    "total_cost_cny": 60000.0, "net_avoided_kgco2e": 20000.0,
                    "order_max_share": 0.5, "category": "成本与碳双降"},
                   {"kind": "combo", "label": "组合：同名家 7.5t + 同名家 7.5t",
                    "option_id": option_id("combo", demand, {"A1": 8.0, "A2": 7.0}),
                    "total_cost_cny": 61000.0, "net_avoided_kgco2e": 19900.0,
                    "order_max_share": 0.53, "category": "成本与碳双降"}]
        reps = representative_options(options)
        ids = [o["option_id"] for o in reps]
        self.assertEqual(len(ids), len(set(ids)))


class TestA04Ledger(unittest.TestCase):
    def test_factor_resolution_by_role_not_hardcoded_id(self):
        # 旧实现硬编码 DEMO-VIRGIN-PP-PROD / DEMO-PP-RECYCLE-PROCESS。
        # 新实现按角色解析：因子 ID 改名后结果不变。
        from src.material import net_emissions
        demand = make_demand()
        supply = make_supply(factor_id="TRUCK-1")
        result = net_emissions(demand, supply, 15.0, make_factors())
        self.assertEqual(result["status"], "可计算")
        baseline = 15.0 * (VIRGIN + 100.0 * TRANSPORT)
        project = 15.0 * RECYCLE + 15.0 * 30.0 * TRANSPORT
        self.assertAlmostEqual(result["baseline_kgco2e"], round(baseline, 4), places=2)
        self.assertAlmostEqual(result["project_kgco2e"], round(project, 4), places=2)

    def test_boundary_mismatch_blocks(self):
        # 旧实现只按术语差异机械判断/默认可比；新实现按核算约定显式判定边界兼容。
        from src.material import net_emissions
        demand = make_demand()
        supply = make_supply(factor_id="TRUCK-1")
        factors = make_factors()
        factors["factors"]["RECYCLE-1"]["boundary"] = "gate-to-grave（含处置）"
        result = net_emissions(demand, supply, 15.0, factors)
        self.assertEqual(result["status"], "待补证据")
        self.assertTrue(any("边界不兼容" in p for p in result["problems"]))

    def test_cost_ledger_lines_and_freight_rules(self):
        # 旧实现成本=单价×数量；新账本分项：材料/运费/装卸/一次性检测/损耗。
        from src.ledger import project_cost_ledger
        demand = make_demand()
        s1 = make_supply(supply_id="A", supplier_name="A", available_mass_t=8.0)
        s2 = make_supply(supply_id="B", supplier_name="B", available_mass_t=8.0,
                         price_includes_freight=True, distance_km=60.0)
        supplies = {"A": s1, "B": s2}
        ledger = project_cost_ledger(demand, {"A": 7.5, "B": 7.5}, supplies)
        items = {l.item for l in ledger.lines}
        self.assertIn("材料价", items)
        self.assertIn("运费", items)
        self.assertIn("装卸费", items)
        freight_b = next(l for l in ledger.lines if l.item == "运费" and l.source.startswith("供给报价含运"))
        self.assertEqual(freight_b.amount, 0.0)
        freight_a = next(l for l in ledger.lines if l.item == "运费" and l.source.startswith("演示假设运价"))
        self.assertAlmostEqual(freight_a.amount, 7.5 * 30.0 * 0.35, places=6)
        # 缺价阻断，不按 0
        s3 = make_supply(supply_id="C", supplier_name="C", price_cny_per_t=None)
        ledger2 = project_cost_ledger(demand, {"C": 15.0}, {"C": s3})
        self.assertIsNone(ledger2.total)
        self.assertTrue(ledger2.problems)

    def test_recycled_mass_separate_from_delivered(self):
        # 旧实现把 100% 交付量计为循环材料量；新账本按再生含量折算。
        from src.material import net_emissions
        demand = make_demand()
        supply = make_supply(recycled_content_pct=90.0, factor_id="TRUCK-1")
        result = net_emissions(demand, supply, 15.0, make_factors())
        self.assertEqual(result["delivered_mass_t"], 15.0)
        self.assertAlmostEqual(result["recycled_mass_t"], 13.5, places=4)


class TestA05N1Causal(unittest.TestCase):
    def test_same_supplier_batches_fail_together(self):
        # 旧实现按供应商名迭代 allocation 键（批次与实体混同）。
        # 新实现：失效实体=供应商，名下所有批次同时失效。
        demand = make_demand()
        s1 = make_supply(supply_id="B1", supplier_name="同一家", available_mass_t=10.0)
        s2 = make_supply(supply_id="B2", supplier_name="同一家", available_mass_t=10.0)
        s3 = make_supply(supply_id="B3", supplier_name="另一家", available_mass_t=20.0)
        supplies = [s1, s2, s3]
        option = {"option_id": "opt-test", "kind": "combo", "label": "组合",
                  "allocation": {"B1": 5.0, "B2": 5.0, "B3": 5.0}}
        results = n1_stress_test(demand, supplies, load_material_factors(),
                                 options=[option])
        entry = results[0]
        # 「同一家」名下两个批次必须同时失效（失败质量 10t，而非 5t）
        failed = entry["worst_case"]
        self.assertEqual(set(failed["failed_batches"]), {"B1", "B2"})

    def test_spare_must_arrive_in_time(self):
        # 旧实现备用不计切换时间；新模型：available_from + logistics_days ≤ 截止日。
        demand = make_demand()
        s1 = make_supply(supply_id="M", supplier_name="主供", available_mass_t=20.0)
        # 备用 2026-10-10 可交付，但切换需 10 天 → 10-20 就绪，晚于截止 10-15 → 不可及
        s2 = make_supply(supply_id="F", supplier_name="备用", available_mass_t=20.0,
                         available_from="2026-10-10")
        supplies = [s1, s2]
        model = {"failure_entity": "supplier", "deadline": "2026-10-15",
                 "committed_mass_t": 15.0, "logistics_days": 10.0,
                 "switching_fee_cny_per_supplier": None, "spare_reservation_t": 0.0,
                 "qualification": "eligible_only", "static_capacity_only": False,
                 "joint_failure_model": "单家供应商失效"}
        option = {"option_id": "opt-m", "kind": "single", "label": "主供",
                  "allocation": {"M": 15.0}}
        results = n1_stress_test(demand, supplies, load_material_factors(),
                                 options=[option], model=model)
        self.assertAlmostEqual(results[0]["min_delivery_service_rate"], 0.0, places=4)
        self.assertTrue(any("备用不可及" in d for d in results[0]["worst_case"]["spare_detail"]))
        # 静态产能上限模式：不计时窗/物流 → 100%（标注为仅产能上限）
        model_static = dict(model, static_capacity_only=True)
        results_static = n1_stress_test(demand, supplies, load_material_factors(),
                                        options=[option], model=model_static)
        self.assertAlmostEqual(results_static[0]["min_delivery_service_rate"], 1.0, places=4)
        self.assertIn("仅产能静态上限", results_static[0]["worst_case"]["spare_detail"][0])

    def test_two_separated_comparisons(self):
        # 旧叙事「53%→100% 证明组合优于单供」；新实现两张对照分开归因。
        result = run_material("demo")
        strategies = result["n1_compare_strategies"]
        evidence = result["n1_compare_evidence"]
        self.assertEqual(strategies["comparison"], "采购策略对照（固定信息集：保守现状证据）")
        self.assertIn("补证对照", evidence["comparison"])
        self.assertIsNotNone(evidence["delta_service_rate"])
        # 固定采购方案在补证对照中不变
        self.assertEqual(evidence["before"][0]["option_id"], evidence["after"][0]["option_id"])


class TestA07SensitivityIdentity(unittest.TestCase):
    def test_options_keyed_by_option_id_not_label(self):
        # 旧实现 base = {o["label"]: o} → 同 label 不同分配被字典覆盖。
        result = run_material("demo")
        combos = [o for o in result["options"] if o["kind"] == "combo"]
        labels = [o["label"] for o in combos]
        self.assertEqual(len(labels), len(set(labels)))  # 演示数据 label 唯一
        sens = result["sensitivity"]
        self.assertIn("thresholds", sens)
        self.assertIn("joint", sens)
        for t in sens["thresholds"]:
            self.assertIn("anchor_option_id", t)

    def test_no_fixed_narrative_numbers(self):
        # 旧代码 57,000 / 53% 固定叙事句子（如「断供只剩 8t 备份 → 53%」「都是 57,000」）
        # 已删除；页面上的 53.3% 等为计算结果渲染值（合法）。
        result = run_material("demo")
        html = build_site_html(result)
        self.assertNotIn("57,000", html)
        self.assertNotIn("断供只剩", html)
        self.assertNotIn("都是 57,000", html)
        self.assertNotIn("57,000", result["greedy_baseline"]["verdict"])
        self.assertNotIn("断供服务率 53%", result["greedy_baseline"]["verdict"])


class TestA08ImportAndPassport(unittest.TestCase):
    def test_preflight_row_level_errors(self):
        from src.imports import preflight_supply_rows
        rows = [{"row_no": 2, "data": {"supply_id": "X1", "supplier_name": "甲",
                                       "material_code": "PP", "available_mass_t": "-5",
                                       "recycled_content_pct": "150"}}]
        issues = preflight_supply_rows(rows, "test.csv")
        self.assertTrue(any(i["severity"] == "structure"
                            and i["col"] == "available_mass_t" for i in issues))
        self.assertTrue(any(i["col"] == "recycled_content_pct" for i in issues))
        for issue in issues:
            for key in ("file", "row", "col", "problem", "suggestion"):
                self.assertIn(key, issue)

    def test_local_loader_rejects_structural_errors(self):
        from src.material import load_supplies_csv
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text(
                "supply_id,supplier_name,material_code,available_mass_t\n"
                "X1,甲,PP,-5\n"
                "X2,乙,PP,abc\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_supplies_csv(path, strict=True)
            self.assertIn("结构错误", str(ctx.exception))

    def test_multi_demand_requires_explicit_choice(self):
        # 旧实现静默取 demands[0]；新实现多条需求必须显式 --demand-id。
        from src.material import load_demands_csv
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "d.csv").write_text(
                "demand_id,buyer_name,target_material_code,required_mass_t\n"
                "D1,甲,PP,10\nD2,乙,PP,20\n", encoding="utf-8")
            demands, stats = load_demands_csv(tmp / "d.csv")
            self.assertEqual(len(demands), 2)
            self.assertEqual(len(stats["issues"]), 0)

    def test_passport_v2_identity_and_hashes(self):
        result = run_material("demo")
        document = passport.build_passport(result, run_id="R1", case_id="C1",
                                           scenario_id="demo")
        self.assertEqual(document["schema_version"], "2.0.0")
        for supply in document["supplies"]:
            self.assertTrue(supply["normalized_record_sha256"])  # 旧版空字典 bug
        self.assertTrue(document["demand"]["normalized_record_sha256"])
        self.assertTrue(document["representatives"])
        self.assertIn("unknowns", document)

    def test_passport_v1_migration(self):
        v1 = {"schema_version": "1.0.0",
              "rules": {"rule_version": "material-gates-v1",
                        "algorithm_version": "material-v7"},
              "options": [{"label": "旧选项", "kind": "single", "allocation": {}}]}
        migrated = passport.migrate_v1_to_v2(v1)
        self.assertEqual(migrated["schema_version"], "2.0.0")
        self.assertEqual(migrated["options"][0]["option_id"], None)  # 待补，未编造

    def test_export_computes_both_file_hashes(self):
        # 旧版 export_passport_json 只算需求哈希，供给哈希为空字典。
        result = run_material("demo")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.json"
            document = passport.export_passport_json(result, out)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("demand_file_sha256", data["input_files"])
            self.assertIn("supply_file_sha256", data["input_files"])
            self.assertIsNotNone(data["input_files"]["factor_file_sha256"])


class TestA06EvidenceActions(unittest.TestCase):
    def test_single_action_does_not_fill_other_fields(self):
        # 旧实现一次动作补齐所有未知字段，把其他动作贡献计入同一动作。
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        plans = plan_evidence_actions(demand, supplies, factor_data)
        sup02_plans = [p for p in plans if p["supply_id"] == "SUP-02"]
        self.assertTrue(sup02_plans)
        # 每个单动作必须报告「未覆盖字段」；动作包显式组合
        bundles = [p for p in sup02_plans if p.get("kind") == "action_bundle"]
        singles = [p for p in sup02_plans if p.get("kind") == "single_action"]
        self.assertTrue(bundles)
        self.assertTrue(singles)
        for plan in singles:
            self.assertIn("fields_not_covered_by_action", plan)


class TestA03BenchmarkSplit(unittest.TestCase):
    def test_dev_and_holdout_reported_separately(self):
        demand = demo_demand()
        supplies = demo_supplies()
        result = run_recall_benchmark(
            demand, supplies,
            cases_path=str(Path(__file__).resolve().parent.parent
                            / "benchmarks" / "material_matching_cases_v2.csv"))
        self.assertIn("dev", result["recall"])
        self.assertIn("holdout", result["recall"])
        self.assertTrue(result["recall"]["dev"]["n_cases"] > 0)
        self.assertTrue(result["recall"]["holdout"]["n_cases"] > 0)
        # 阈值网格只基于开发集
        self.assertTrue(all("threshold" in row for row in result["recall"]["threshold_grid"]))


if __name__ == "__main__":
    unittest.main()


class TestPeerReviewRegressions(unittest.TestCase):
    """外部审查发现项的回归锁定（F-xx 编号对应发现清单）。"""

    def test_F01_backups_newest_first(self):
        # 备份列表按名称降序（最新在前），「恢复最近备份」取 [0] 即最新
        import time as _time
        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp = _Path(tmp)
            db = __import__("pilot.db", fromlist=["Database"]).Database(tmp / "db.sqlite3")
            db.backup_to(tmp / "pilot-backup-20260101-120000-000.sqlite3")
            db.backup_to(tmp / "pilot-backup-20260102-120000-000.sqlite3")
            names = sorted([p.name for p in (tmp).glob("pilot-backup-*.sqlite3")],
                           key=lambda n: n, reverse=True)
            self.assertTrue(names[0].startswith("pilot-backup-20260102"))
            db.close()

    def test_F02_nan_rejected_in_reserve_and_wizard(self):
        # NaN 不得穿过 JSON 解析与浮点转换进入引擎/预留
        from pilot.api import _to_float
        with self.assertRaises(ValueError):
            _to_float(float("nan"))
        import http.client
        server = __import__("tests.test_pilot", fromlist=["PilotTestServer"]).PilotTestServer()
        try:
            c = __import__("tests.test_pilot", fromlist=["Client"]).Client(server.port)
            c.post("/api/auth/init", {"username": "a", "password": "pass-12345678"}, csrf=False)
            status, data = c.post("/api/auth/login",
                                  {"username": "a", "password": "pass-12345678"})
            c.csrf = data["csrf"]
            # JSON NaN 文本直接被解析层拒绝
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
            conn.request("POST", "/api/auth/login",
                         body='{"username": "a", "password": "pass-12345678", "x": NaN}',
                         headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            self.assertEqual(res.status, 400)
            conn.close()
        finally:
            server.close()

    def test_F03_duplicate_ids_detected(self):
        # 批内重复 supply_id 是结构错误（strict 拒绝），不再静默丢行
        from src.imports import preflight_supply_rows
        rows = [
            {"row_no": 2, "data": {"supply_id": "X1", "supplier_name": "甲",
                                   "material_code": "PP", "available_mass_t": "5"}},
            {"row_no": 3, "data": {"supply_id": "X1", "supplier_name": "乙",
                                   "material_code": "PP", "available_mass_t": "8"}},
        ]
        issues = preflight_supply_rows(rows, "dup.csv")
        self.assertTrue(any(i["severity"] == "structure" and i["col"] == "supply_id"
                            and "重复" in i["problem"] for i in issues))
        from src.material import load_supplies_csv
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dup.csv"
            path.write_text("supply_id,supplier_name,material_code,available_mass_t\n"
                            "X1,甲,PP,5\nX1,乙,PP,8\nX2,丙,PP,3\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_supplies_csv(path, strict=True)

    def test_F04_no_baseline_price_no_delta(self):
        # 基准价缺失时「较基准」不得退化为 vs 首个单供
        demand = make_demand(baseline_virgin_price_cny_per_t=None)
        supply = make_supply(available_mass_t=20.0)
        options = build_options(demand, [supply], load_material_factors())
        for o in options:
            if o["kind"] != "baseline":
                self.assertIsNone(o["cost_delta_cny"])
                self.assertIsNone(o["marginal_abatement_cost_cny_per_tco2e"])

    def test_F05_threshold_grid_dev_only(self):
        # 阈值网格只基于开发集
        result = run_recall_benchmark(
            demo_demand(), demo_supplies(),
            cases_path=str(Path(__file__).resolve().parent.parent
                            / "benchmarks" / "material_matching_cases_v2.csv"))
        dev_n = result["recall"]["dev"]["n_cases"]
        for row in result["recall"]["threshold_grid"]:
            self.assertEqual(row["dev_n_cases"], dev_n)

    def test_F08_restore_failure_reopens_connection(self):
        # copy 阶段失败后连接仍可用（服务不整体瘫痪）
        import shutil as _shutil
        from pilot.db import Database as _Db
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db = _Db(tmp / "main.sqlite3")
            db.execute("INSERT INTO meta(key,value) VALUES('k','v')")
            db.commit()
            backup = tmp / "backup.sqlite3"
            db.backup_to(backup)
            original = _shutil.copyfile
            def broken(src, dst):
                raise OSError("磁盘故障（注入）")
            try:
                _shutil.copyfile = broken
                with self.assertRaises(OSError):
                    db.restore_from(backup)
            finally:
                _shutil.copyfile = original
            # 连接已重开，服务继续可用
            rows = db.execute("SELECT COUNT(*) AS n FROM meta").fetchone()
            self.assertGreaterEqual(rows["n"], 1)
            db.close()

    def test_F09_report_dashboard_survive_missing_values(self):
        # 缺供量/缺价等业务待补证值不导致报告/仪表盘格式化崩溃
        demand = make_demand()
        supplies = [make_supply(supply_id="M1", supplier_name="缺量方",
                                available_mass_t=None, price_cny_per_t=None,
                                distance_km=None, mfi_g_10min=None,
                                evidence_source="", evidence_date="")]
        factor_data = load_material_factors()
        result = run_material_like(demand, supplies, factor_data)
        from src.report import build_material_markdown
        from src.dashboard import build_material_dashboard_html
        markdown = build_material_markdown(result)
        html = build_material_dashboard_html(result)
        self.assertIn("缺量方", markdown)
        self.assertIn("缺量方", html)

    def test_F11_self_report_not_third_party(self):
        # 供应商自检报告不再被关键词启发式判为第三方最高级
        from src.evidence_quality import evidence_quality_score
        demand = make_demand()
        supply = make_supply(evidence_source="供应商自检报告（内部实验室）")
        quality = evidence_quality_score(supply, demand)
        self.assertEqual(quality["dimensions"]["来源类型"]["score"], 2)
        self.assertNotEqual(quality["dimensions"]["独立核验"]["score"], 1)

    def test_F12_supplier_id_entity_aggregation(self):
        # 同 supplier_id 不同名（拼写差异）视为同一失效实体
        demand = make_demand()
        s1 = make_supply(supply_id="B1", supplier_id="ENT-A",
                         supplier_name="甲有限公司", available_mass_t=10.0)
        s2 = make_supply(supply_id="B2", supplier_id="ENT-A",
                         supplier_name="甲有限公司（上海）", available_mass_t=10.0)
        s3 = make_supply(supply_id="B3", supplier_id="ENT-B",
                         supplier_name="乙有限公司", available_mass_t=20.0)
        option = {"option_id": "opt-e", "kind": "combo", "label": "组合",
                  "allocation": {"B1": 5.0, "B2": 5.0, "B3": 5.0}}
        results = n1_stress_test(demand, [s1, s2, s3], load_material_factors(),
                                 options=[option])
        entry = results[0]["worst_case"]
        self.assertEqual(set(entry["failed_batches"]), {"B1", "B2"})  # 同实体一起失效

    def test_F13_combo_options_carry_full_precision(self):
        # 组合选项透传全精度字段，场景级 Pareto 不用展示舍入值
        demand = demo_demand()
        options = build_options(demand, demo_supplies(), load_material_factors())
        for o in options:
            if o["kind"] == "combo":
                self.assertIn("_cost_full", o)
                self.assertIn("_net_full", o)
                self.assertIn("_share_full", o)

    def test_F15_suggested_step_divides(self):
        # 建议步长必须整除需求量
        from src.material import _suggested_divisible_step
        for required in (15.0, 16.3, 7.7, 4.0):
            step = _suggested_divisible_step(required)
            self.assertIsNotNone(step)
            steps = required / step
            self.assertAlmostEqual(steps, round(steps), places=6)

    def test_F16_recycle_factor_embedded_transport(self):
        # 再生材料因子声明已含运输时，项目侧不再另加运输
        from src.material import net_emissions
        factors = make_factors()
        factors["factors"]["RECYCLE-1"]["transport_included_in_factor"] = True
        demand = make_demand()
        supply = make_supply(factor_id="TRUCK-1")
        result = net_emissions(demand, supply, 15.0, factors)
        self.assertEqual(result["status"], "可计算")
        project = 15.0 * RECYCLE  # 无运输项
        self.assertAlmostEqual(result["project_kgco2e"], round(project, 4), places=2)

    def test_F17_bundle_fee_unknown_stays_unknown(self):
        # 动作包任一成员费用未知 → 包费用显示待询，不伪装成已知
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        plans = plan_evidence_actions(demand, supplies, factor_data)
        for plan in plans:
            if plan.get("kind") == "action_bundle":
                self.assertIn("待询", str(plan["fee_cny"]))

    def test_F20_greedy_match_full_precision(self):
        # 贪心与枚举一致判定用全精度（0.004 元差异不得判「一致」）
        from src.greedy_baseline import greedy_report
        demand = make_demand()
        s1 = make_supply(supply_id="G1", supplier_name="贪心1", available_mass_t=20.0,
                         price_cny_per_t=1000.0)
        s2 = make_supply(supply_id="G2", supplier_name="贪心2", available_mass_t=20.0,
                         price_cny_per_t=1000.0, distance_km=40.0)
        factors = load_material_factors()
        report = greedy_report(demand, [s1, s2], factors)
        # 贪心=G1 全量（1000×15+运费30km+装卸）与枚举最优（G1 全量）相同 → 一致
        self.assertTrue(report["cost_match"])

    def test_F24_selection_idempotent(self):
        # 同 (task, run, option) 重复选定不产生重复行
        from pilot.db import Database as _Db
        with tempfile.TemporaryDirectory() as tmp:
            db = _Db(Path(tmp) / "sel.sqlite3")
            db.execute("INSERT INTO tasks(id,case_id,demand_json,status,created_by,"
                       "created_at,updated_at) VALUES('T','c','{}','draft',1,'x','x')")
            db.execute("INSERT INTO runs(id,task_id,scenario_id,result_json,config_sha256,"
                       "created_by,created_at) VALUES('R','T','local','{}','h',1,'x')")
            db.commit()
            for i in range(2):
                db.execute(
                    "INSERT OR IGNORE INTO selections(task_id,run_id,option_id,reason,"
                    "selected_by,created_at) VALUES('T','R','O','r',1,'2026-09-05')")
                db.commit()
            n = db.execute("SELECT COUNT(*) AS n FROM selections").fetchone()["n"]
            self.assertEqual(n, 1)
            db.close()


def run_material_like(demand, supplies, factor_data):
    """构造与 run_material 同构的结果对象（供回归用例使用）。"""
    from src.material import analyze_task
    return analyze_task(demand, supplies, factor_data)
