"""v14 任务 2：CP-SAT 完整非支配前沿与穷举互验（ε-约束精确枚举）。

互验核心：随机案例（引擎上限调小强制走求解器）——求解器前沿与独立穷举前沿
**集合相等**（按 option_id），且每个共同方案的 (成本, 净减排, 份额) 逐位一致；
v13 数学事实的两侧（无遗漏/无多余）由集合相等直接锁定。
另验证 FRONTIER_CAP 触顶的诚实截断语义。
"""

import unittest
from unittest import mock

from src import material
from src.material import simulate_supply_mix

from tests.test_v13_solver import (HAS_ORTOOLS, SOLVER_THRESHOLD, build_case,
                                   enumerate_ground_truth, load_cases, sort_key)


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools 未安装")
class FullFrontierMutualVerification(unittest.TestCase):
    """≥10 组随机案例：求解器完整前沿 == 穷举前沿（集合相等 + 数值逐位一致）。"""

    @classmethod
    def setUpClass(cls):
        cls.cases, cls.rejected = load_cases(10)

    def run_frontier_equality(self, seed, case):
        with mock.patch.object(material, "MAX_COMBINATIONS", SOLVER_THRESHOLD):
            mix = simulate_supply_mix(case["demand"], case["eligible"], case["factor_data"],
                                      step_t=case["step_t"], max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal", f"seed={seed}")
        self.assertTrue(mix["is_complete"], f"seed={seed} 随机小案例不应触达 CAP")
        truth_by_id = {p["option_id"]: p for p in case["frontier"]}
        solver_by_id = {p["option_id"]: p for p in mix["frontier"]}
        # 集合相等：不多（无多余=三维支配者不混入）、不少（无遗漏）
        self.assertEqual(set(solver_by_id), set(truth_by_id),
                         f"seed={seed} 前沿集合不一致："
                         f"仅穷举有={sorted(set(truth_by_id) - set(solver_by_id))} "
                         f"仅求解器有={sorted(set(solver_by_id) - set(truth_by_id))}")
        # 共同方案的三目标值逐位一致（同分配同账本）
        for oid, truth in truth_by_id.items():
            got = solver_by_id[oid]
            self.assertEqual(got["_cost_full"], truth["_cost_full"], f"seed={seed} {oid}")
            self.assertEqual(got["_net_full"], truth["_net_full"], f"seed={seed} {oid}")
            self.assertEqual(got["_share_full"], truth["_share_full"], f"seed={seed} {oid}")

    def test_case_01(self):
        seed, case = self.cases[0]
        self.run_frontier_equality(seed, case)

    def test_case_02(self):
        seed, case = self.cases[1]
        self.run_frontier_equality(seed, case)

    def test_case_03(self):
        seed, case = self.cases[2]
        self.run_frontier_equality(seed, case)

    def test_case_04(self):
        seed, case = self.cases[3]
        self.run_frontier_equality(seed, case)

    def test_case_05(self):
        seed, case = self.cases[4]
        self.run_frontier_equality(seed, case)

    def test_case_06(self):
        seed, case = self.cases[5]
        self.run_frontier_equality(seed, case)

    def test_case_07(self):
        seed, case = self.cases[6]
        self.run_frontier_equality(seed, case)

    def test_case_08(self):
        seed, case = self.cases[7]
        self.run_frontier_equality(seed, case)

    def test_case_09(self):
        seed, case = self.cases[8]
        self.run_frontier_equality(seed, case)

    def test_case_10(self):
        seed, case = self.cases[9]
        self.run_frontier_equality(seed, case)


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools 未安装")
class FrontierCapHonesty(unittest.TestCase):
    """FRONTIER_CAP 触顶：如实截断（is_complete=False + note 标注），不冒充完整。"""

    def test_cap_truncates_honestly(self):
        case = None
        for seed in range(1, 400):
            candidate = build_case(seed)
            if candidate and len(candidate["frontier"]) >= 4:
                case = (seed, candidate)
                break
        self.assertIsNotNone(case, "未找到前沿 ≥4 点的随机案例")
        seed, case = case
        truth_ids = {p["option_id"] for p in case["frontier"]}
        with mock.patch.object(material, "MAX_COMBINATIONS", SOLVER_THRESHOLD), \
                mock.patch.object(material, "FRONTIER_CAP", 2):
            mix = simulate_supply_mix(case["demand"], case["eligible"],
                                      case["factor_data"], step_t=case["step_t"],
                                      max_plans=None)
        self.assertEqual(mix.get("solver_status"), "optimal")
        self.assertTrue(mix["truncated"])
        self.assertFalse(mix["is_complete"])
        self.assertIsNone(mix["frontier_count"])
        # CAP=2 + 三锚点恒在（锚点豁免于 CAP，全局精确是硬保证）
        self.assertLessEqual(len(mix["frontier"]), 2 + 3)
        self.assertIn("FRONTIER_CAP", mix["note"])
        # 截断点必须是真实前沿的子集（截断不造假）
        self.assertTrue({p["option_id"] for p in mix["frontier"]} <= truth_ids)


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools 未安装")
class FrontierAnchorExtraction(unittest.TestCase):
    """三锚点（最低成本/最高净减排/最低集中度）从完整前沿中提取且与穷举一致。"""

    def test_anchors_from_full_frontier(self):
        case = None
        for seed in range(1, 400):
            candidate = build_case(seed)
            if candidate:
                case = (seed, candidate)
                break
        self.assertIsNotNone(case)
        seed, case = case
        with mock.patch.object(material, "MAX_COMBINATIONS", SOLVER_THRESHOLD):
            mix = simulate_supply_mix(case["demand"], case["eligible"], case["factor_data"],
                                      step_t=case["step_t"], max_plans=None)
        frontier = mix["frontier"]
        truth = case["frontier"]
        cost_anchor = min(frontier, key=lambda p: (p["_cost_full"], -p["_net_full"],
                                                   p["_share_full"] or 1.0))
        net_anchor = min(frontier, key=lambda p: (-p["_net_full"], p["_cost_full"],
                                                  p["_share_full"] or 1.0))
        share_anchor = min(frontier, key=lambda p: (p["_share_full"] or 1.0, p["_cost_full"],
                                                    -p["_net_full"]))
        for got, name in ((cost_anchor, "cost"), (net_anchor, "net"), (share_anchor, "share")):
            truth_anchor = min(truth, key=lambda p: {
                "cost": (p["_cost_full"], -p["_net_full"], p["_share_full"] or 1.0),
                "net": (-p["_net_full"], p["_cost_full"], p["_share_full"] or 1.0),
                "share": (p["_share_full"] or 1.0, p["_cost_full"], -p["_net_full"])}[name])
            self.assertEqual(got["option_id"], truth_anchor["option_id"],
                             f"seed={seed} {name} 锚点与穷举不一致")


if __name__ == "__main__":
    unittest.main()
