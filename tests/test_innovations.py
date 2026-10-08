"""v7 创新模块测试：材料召回基准、证据价值排序、证据护照、N-1 已含于 test_material。"""

import json
import unittest
from pathlib import Path

from src import passport
from src.evidence_value import load_evidence_actions, plan_evidence_actions
from src.material import (
    build_options,
    demo_demand,
    demo_supplies,
    load_material_factors,
    run_material,
    simulate_supply_mix,
    supply_state,
)
from src.material_match import (
    dice_similarity,
    matched_synonyms,
    normalize_material_name,
    recall_candidates,
    run_recall_benchmark,
)


class TestMaterialRecall(unittest.TestCase):
    def test_normalize_and_similarity(self):
        self.assertEqual(normalize_material_name("PP 再生颗粒（黑色）"), "pp 再生颗粒 黑色")
        self.assertGreater(dice_similarity("pp再生颗粒黑色", "pp再生颗粒灰色"), 0.5)
        self.assertEqual(dice_similarity("pp再生颗粒黑色", "abs原生粒料"), 0.0)

    def test_synonyms_hit(self):
        hits = matched_synonyms(normalize_material_name("rPP 黑色粒料"), "PP")
        self.assertTrue(any("pp" in h or "PP" in h for h in hits))

    def test_recall_and_hard_gate_order(self):
        # 文本相似只召回；硬门槛仍由规格决定（名称相似但熔指超范围 → 淘汰）
        demand = demo_demand()
        supplies = demo_supplies()
        scored = recall_candidates(demand, supplies)
        by_id = {s["supply_id"]: s for s in scored}
        self.assertTrue(by_id["SUP-01"]["recalled"])
        state = supply_state(demand, next(s for s in supplies if s.supply_id == "SUP-03"))
        self.assertEqual(state["state"], "ineligible")

    def test_benchmark_runs_and_is_stable(self):
        # 业务理由：基准重定义为固定候选池 + Top-k 排名 + 负例标注 +
        # 空查询显式拒绝 + 硬门槛误放行按标签统计 + 开发/保留集隔离。
        demand = demo_demand()
        supplies = demo_supplies()
        cases_path = Path(__file__).resolve().parent.parent / "benchmarks" / "material_matching_cases_v2.csv"
        result = run_recall_benchmark(demand, supplies, cases_path=cases_path)
        self.assertTrue(result["stable"])
        self.assertIn("人工标注演示集", result["benchmark"])
        self.assertGreaterEqual(result["candidate_pool_size"], 4)
        recall = result["recall"]
        self.assertIsNotNone(recall["dev"].get("recall_at_1"))
        self.assertIsNotNone(recall["holdout"].get("recall_at_1"))
        self.assertIn("hit_at_1", recall["dev"])
        # 空查询显式拒绝
        self.assertTrue(any(r.get("rejected") for r in recall["cases"]))
        empty = [r for r in recall["cases"] if r.get("rejected")]
        self.assertTrue(all("拒绝" in r["rejection_reason"] for r in empty))
        # 硬门槛误放行按标签统计：正负规格/临界值/缺证据/时窗不符用例全部分类
        gate = result["gate"]
        labels = set(gate["by_label"])
        for expected in ("正负规格（熔指超上限）", "临界值（恰在上限，应通过）",
                         "缺证据（数值无报告支撑）", "时窗不符"):
            self.assertIn(expected, labels)
        # 近似但不兼容材料（ABS 负例）在召回任务中被标注为负例，不得放行
        self.assertTrue(any(r["case_id"] == "CASE-07" and not r["ok"] for r in recall["cases"]))

    def test_benchmark_single_action_covers_only_blocked_fields(self):
        # 业务理由：单个动作只补它覆盖的阻断字段；其余未知字段保持缺失。
        from src.evidence_value import _apply_action_fields, _blocked_fields_of
        demand = demo_demand()
        supplies = demo_supplies()
        sup02 = next(s for s in supplies if s.supply_id == "SUP-02")
        price_action = {"action_id": "PRICE_QUOTE", "blocks": ["价格"], "optimistic_values": {}}
        import copy
        fake = copy.copy(sup02)
        _apply_action_fields(fake, price_action, ["价格"], demand)
        # 价格被补上；检测类字段（如证据来源）未被本动作触碰
        self.assertIsNotNone(fake.price_cny_per_t)
        self.assertEqual(fake.evidence_source, "")


class TestEvidenceValue(unittest.TestCase):
    def test_actions_config_loads(self):
        actions = load_evidence_actions()
        self.assertGreaterEqual(len(actions), 4)
        self.assertTrue(all(a.get("fee_cny") is not None for a in actions))

    def test_releasing_combo_evidence_ranks_first(self):
        # 现状：SUP-02（7t）待核验；补检测可释放 8t+7t 组合 → 排在最前
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        plans = plan_evidence_actions(demand, supplies, factor_data)
        self.assertTrue(plans)
        sup02 = [p for p in plans if p["supply_id"] == "SUP-02"]
        self.assertTrue(sup02)
        top = plans[0]
        self.assertIn("SUP-02", top["supply_id"])
        self.assertGreater(top["value_interval"]["extra_supply_t"], 0)

    def test_irrelevant_evidence_ranks_last(self):
        # 对无影响批次的动作价值为 0，排在有价值动作之后
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        plans = plan_evidence_actions(demand, supplies, factor_data)
        values = [p["value_interval"]["extra_supply_t"] + p["value_interval"]["net_avoided_gain_kgco2e"]
                  for p in plans]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_unknown_fee_gives_no_roi(self):
        # 业务理由：费用未知时不输出精确 ROI；通过概率未知时
        # 只输出「有利情景净值上限」（字段改名 net_value_upper_bound_cny），非 EVSI。
        demand = demo_demand()
        supplies = demo_supplies()
        factor_data = load_material_factors()
        plans = plan_evidence_actions(demand, supplies, factor_data)
        for plan in plans:
            if plan["fee_cny"] == "待询":
                self.assertIsNone(plan["net_value_upper_bound_cny"])
            self.assertIn("未知", plan["probability"])

    def test_deterministic_and_no_mutation(self):
        demand = demo_demand()
        supplies = demo_supplies()
        snapshot = [(s.supply_id, s.evidence_source, s.mfi_g_10min, s.price_cny_per_t)
                    for s in supplies]
        factor_data = load_material_factors()
        first = plan_evidence_actions(demand, supplies, factor_data)
        second = plan_evidence_actions(demand, supplies, factor_data)
        self.assertEqual([p["supply_id"] for p in first], [p["supply_id"] for p in second])
        self.assertEqual(snapshot, [(s.supply_id, s.evidence_source, s.mfi_g_10min, s.price_cny_per_t)
                                    for s in supplies])


class TestPassport(unittest.TestCase):
    def test_valid_document(self):
        # 业务理由：护照升级 Schema v2（身份/哈希/账本/未知项），
        # 校验优先用成熟 JSON Schema 验证器。
        result = run_material("demo")
        document = passport.build_passport(result, run_id="RUN-TEST", case_id="CASE-TEST")
        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "schemas" / "material_evidence_passport.schema.v2.json")
            .read_text(encoding="utf-8"))
        outcome = passport.validate_against_schema(document, schema)
        self.assertEqual(outcome["problems"], [])
        self.assertEqual(document["schema_version"], "2.0.0")
        self.assertEqual(document["run_id"], "RUN-TEST")
        self.assertTrue(document["supplies"])
        # v2 关键字段：供给批次规范化记录哈希不得为空字典（旧版 bug）
        for supply in document["supplies"]:
            self.assertTrue(supply["normalized_record_sha256"])
        # 选项带 option_id 与账本
        for option in document["options"]:
            self.assertTrue(option["option_id"])
            self.assertIn("cost_ledger", option)

    def test_invalid_document_caught(self):
        result = run_material("demo")
        document = passport.build_passport(result)
        del document["demand"]["required_mass_t"]  # 非法：必填字段缺失
        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "schemas" / "material_evidence_passport.schema.v2.json")
            .read_text(encoding="utf-8"))
        outcome = passport.validate_against_schema(document, schema)
        self.assertTrue(any("required_mass_t" in p for p in outcome["problems"]))

    def test_export_writes_json(self):
        import tempfile
        result = run_material("demo")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "passport.json"
            document = passport.export_passport_json(result, out)
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["schema_version"],
                             document["schema_version"])


if __name__ == "__main__":
    unittest.main()
