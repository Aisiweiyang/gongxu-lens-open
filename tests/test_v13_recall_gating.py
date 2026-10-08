"""v13 召回阈值门控 + 兜底回归。

背景：v12 规则 `相似度≥阈值 或 供给名同义词命中` 的同义词只看供给名——含材料代号的
供给对任意查询都「命中」，实测 PP 池对「黑色粒料现货」这类无材料语义查询全部被召回
（精度漏洞）。v13 借鉴 industry-chain-matcher 的「阈值门控 + 兜底」结构思路（只借思路，
未用其代码、无在线依赖、不涉及任何模型）：
  G1 高相似通道（≥0.45 直接召回）；G2 类别联合通道（查询侧与供给名都有材料类别证据）；
  兜底层（有信号未过门控 → tier=fallback，展示可见、不进指标）；其余 tier=out。

口径说明：README 旧「6 案例 Recall@1=1.0」为已废止的单候选口径（见 docs/archive/材料事实修订清单-v10.md），
v10 起重定义为 v2 固定候选池 Top-k（开发/保留集分离），当时实测 dev 0.7 / holdout 0.5、
误放行 0。本文件把「不回退」锚定在该真实口径上，并固化 v13 门控的行为改进。
"""

import unittest
from pathlib import Path

from src.material import MaterialSupply, demo_demand, demo_supplies
from src.material_match import recall_candidates, run_recall_benchmark

CASES_V2 = Path(__file__).resolve().parent.parent / "benchmarks" / "material_matching_cases_v2.csv"


def run_v2_benchmark():
    demand, supplies = demo_demand(), demo_supplies()
    return run_recall_benchmark(demand, supplies, cases_path=CASES_V2)


class RecallGateNoRegression(unittest.TestCase):
    """v13 门控不得回退 v10 记录的基准口径（dev 0.7 / holdout 0.5 / 误放行 0）。"""

    def test_v2_metrics_no_regression(self):
        result = run_v2_benchmark()
        self.assertTrue(result["stable"])
        recall = result["recall"]
        self.assertEqual(recall["dev"]["recall_at_1"], 0.7)
        self.assertEqual(recall["dev"]["recall_at_3"], 1.0)
        self.assertEqual(recall["holdout"]["recall_at_1"], 0.5)
        self.assertEqual(recall["holdout"]["recall_at_3"], 1.0)
        # 硬门槛误放行/误杀保持 0（召回层改动不得影响三态硬门槛）
        gate = result["gate"]
        self.assertEqual(sum(v["false_pass"] for v in gate["by_label"].values()), 0)
        self.assertEqual(sum(v["false_kill"] for v in gate["by_label"].values()), 0)

    def test_new_noise_cases_pass_and_not_recalled(self):
        """v13 新增回归用例：无材料语义查询不再误召回（v12 下全部误召回、ok=False）。"""
        result = run_v2_benchmark()
        rows = {r["case_id"]: r for r in result["recall"]["cases"]}
        for case_id in ("CASE-18", "CASE-19", "CASE-20"):
            self.assertTrue(rows[case_id]["ok"], f"{case_id} 应通过（无误召回）")
            self.assertEqual(rows[case_id]["ranking"], [],
                             f"{case_id} 不应有被召回的候选")
        demand, supplies = demo_demand(), demo_supplies()
        for query in ("黑色粒料现货", "灰色粒子", "黑色注塑料 无牌号"):
            tiers = [r["tier"] for r in recall_candidates(demand, supplies, query_name=query)]
            self.assertNotIn("gated", tiers, f"{query!r} 无材料语义，不应有候选过门控")
            self.assertIn("fallback", tiers, f"{query!r} 应有兜底展示候选（不进指标）")


class RecallGateBehavior(unittest.TestCase):
    def test_class_query_behavior_preserved(self):
        """类别查询（v12 已正确的行为）在 v13 门控下逐位保留。"""
        demand, supplies = demo_demand(), demo_supplies()
        # 类别同义词变体查询：类别联合证据成立 → 全部过门控（与 v12 召回一致）
        tiers = [r["tier"] for r in recall_candidates(demand, supplies,
                                                      query_name="聚丙稀粒子 黑色")]
        self.assertEqual(tiers, ["gated"] * len(supplies))
        # 生产调用（无 query_name：代码+形态+颜色构造）自带类别证据 → 演示供给全召回
        tiers = [r["tier"] for r in recall_candidates(demand, supplies)]
        self.assertEqual(tiers, ["gated"] * len(supplies))

    def test_cross_material_precision_improved(self):
        """跨材料负例的误召回收缩：ABS 查询 4→1、PE 查询 4→2（仅高相似通道放行）。"""
        result = run_v2_benchmark()
        rows = {r["case_id"]: r for r in result["recall"]["cases"]}
        self.assertEqual(rows["CASE-07"]["ranking"], ["SUP-04"])
        self.assertEqual(rows["CASE-09"]["ranking"], ["SUP-04", "SUP-01"])

    def test_tier_out_and_fallback_distinction(self):
        """tier 三态：无信号=out；有信号未过门控=fallback；过门控=gated。"""
        demand, supplies = demo_demand(), demo_supplies()
        scored = recall_candidates(demand, supplies, query_name="黑色粒料现货")
        by_id = {r["supply_id"]: r for r in scored}
        # PP 供给名永远带类别/同义词信号 → 至少 fallback，不会是 out
        for r in scored:
            self.assertEqual(r["recalled"], r["tier"] == "gated")
            self.assertIn(r["tier"], ("gated", "fallback"))
        # 非 PP 且无任何同义词信号的供给：相似度也过低 → out
        mystery = MaterialSupply(
            supply_id="SUP-90", supplier_name="神秘物料（测试）", material_code="XX",
            material_name_raw="神秘料X", polymer_type="XX", form="块", color="透明",
            available_mass_t=5.0, available_from="2026-09-01", available_to="2026-12-31",
            price_cny_per_t=1000.0, distance_km=50.0, source="测试")
        scored = recall_candidates(demand, supplies + [mystery], query_name="黑色粒料现货")
        self.assertEqual({r["supply_id"]: r["tier"] for r in scored}["SUP-90"], "out")


if __name__ == "__main__":
    unittest.main()
