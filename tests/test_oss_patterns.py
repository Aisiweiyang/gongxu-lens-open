"""吸收大型开源项目范式的三个模块测试：
证据质量五维评分（ecoinvent pedigree）、贪心基线对照（SupplyNetPy 式互验）、
配置驱动敏感性（SALib 式问题定义）。"""

import unittest

from src.material import run_material
from src.site_builder import build_site_html


class TestEvidenceQuality(unittest.TestCase):
    def test_pending_batch_scores_lower_than_eligible(self):
        # 业务理由：质量规则拆分为 8 维、演示来源降级、
        # 来源缺失列为关键缺证据；无任何证据来源的 SUP-02 由旧「中」修正为「低」。
        r = run_material("demo")
        by_id = {row["supply_id"]: row for row in r["quality"]}
        self.assertGreater(by_id["SUP-02"]["score"], by_id["SUP-01"]["score"])
        self.assertEqual(by_id["SUP-02"]["grade"], "低")
        self.assertIn("证据来源缺失", by_id["SUP-02"]["critical_missing"])

    def test_full_scores_have_no_weakest(self):
        # 业务理由：演示来源不自动获得真实证据最高评级
        # （SUP-01 的「独立核验」维度因演示来源降级为 4，最短板不再是 None）；
        # 全 1 分时最短板才为 None。改用合成全优来源验证「1 最优」方向。
        from src.evidence_quality import evidence_quality_score
        from src.material import MaterialSupply
        r = run_material("demo")
        by_id = {row["supply_id"]: row for row in r["quality"]}
        self.assertIsNotNone(by_id["SUP-01"]["weakest"])
        self.assertIn("独立核验", by_id["SUP-01"]["dimensions"])
        demand = r["demand"]
        perfect = MaterialSupply(
            supply_id="PERF", supplier_name="全优（合成）", material_code="PP",
            material_name_raw="再生PP颗粒", polymer_type="PP", form="颗粒", color="黑色",
            grade="注塑级", mfi_g_10min=12.0, moisture_pct=0.3, ash_pct=3.0,
            recycled_content_pct=95.0, available_mass_t=20.0, available_from="2026-09-10",
            available_to="2026-10-31", origin=demand.destination, price_cny_per_t=4200.0,
            distance_km=30.0, evidence_source="第三方独立检测报告（非演示）",
            evidence_date="2026-08-15", evidence_attachment="https://example.invalid/report-123.pdf",
            source="第三方")
        quality = evidence_quality_score(perfect, demand)
        self.assertEqual(quality["weakest"], None)
        self.assertEqual(quality["grade"], "高")

    def test_ineligible_batch_carries_gate_annotation(self):
        """被硬门槛淘汰的批次：质量分不改变淘汰结论，须带标注。"""
        r = run_material("demo")
        by_id = {row["supply_id"]: row for row in r["quality"]}
        self.assertEqual(by_id["SUP-03"]["gate_label"], "不符合")
        self.assertIn("已被硬门槛淘汰", by_id["SUP-03"]["gate_note"])


class TestGreedyBaseline(unittest.TestCase):
    def test_greedy_matches_enumeration_on_demo(self):
        # 业务理由：贪心与枚举比较使用同一到厂成本账本，
        # 数值来自真实计算（旧锁定值 57,000 为纯材料价口径，已废止）。
        r = run_material("demo")
        g = r["greedy_baseline"]
        self.assertTrue(g["computable"])
        expected = 3800.0 * 15.0 + 15 * 480 * 0.35 + 15 * 20.0  # SUP-04 单供到厂成本
        self.assertEqual(g["total_cost_cny"], round(expected, 2))
        self.assertEqual(g["enumeration_best_cost"], g["total_cost_cny"])
        self.assertTrue(g["cost_match"])
        self.assertEqual(g["n_suppliers"], 1)
        # 结论文案不含固定叙事数字（旧 57,000/53% 已删除）
        self.assertNotIn("57,000", g["verdict"])
        self.assertNotIn("53%", g["verdict"])


class TestConfigDrivenSensitivity(unittest.TestCase):
    def test_registry_in_config(self):
        """扰动范围来自 yaml 注册表（SALib 式问题定义），不是代码硬编码。"""
        r = run_material("demo")
        registry = r["factor_data"]["sensitivity"]
        self.assertEqual(registry["baseline_transport_distance_km"]["low_factor"], 0.8)
        self.assertEqual(registry["baseline_transport_distance_km"]["high_factor"], 1.2)
        self.assertEqual(registry["DEMO-PP-RECYCLE-PROCESS"]["low_factor"], 0.9)
        self.assertEqual(registry["transport_factor"]["high_factor"], 1.1)
        self.assertEqual(len(r["sensitivity"]["scenarios"]), 6)


class TestSiteRenders(unittest.TestCase):
    def test_quality_and_greedy_sections_present(self):
        # 业务理由：质量规则拆分为 8 维，标题改为「多维评分」。
        html = build_site_html(run_material("demo"))
        self.assertIn("证据质量多维评分", html)
        self.assertIn("贪心基线对照", html)
        self.assertIn("pedigree", html)


if __name__ == "__main__":
    unittest.main()
