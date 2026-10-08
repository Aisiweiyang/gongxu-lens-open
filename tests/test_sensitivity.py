"""sensitivity 结论稳定性核查测试：确定性、场景覆盖、演示数据结论锁定。"""

import unittest

from src.material import run_material


class TestSensitivity(unittest.TestCase):
    def test_deterministic(self):
        r1 = run_material("demo")["sensitivity"]
        r2 = run_material("demo")["sensitivity"]
        self.assertEqual(r1, r2)

    def test_six_scenarios_cover_three_levers(self):
        sens = run_material("demo")["sensitivity"]
        self.assertEqual(len(sens["scenarios"]), 6)
        names = "；".join(s["name"] for s in sens["scenarios"])
        self.assertIn("基准运输距离", names)
        self.assertIn("再生PP颗粒生产因子", names)
        self.assertIn("运输因子", names)

    def test_demo_conclusion_stable(self):
        """锁定：演示数据下结论不随 ±20%/±10% 确定性扰动翻转。"""
        sens = run_material("demo")["sensitivity"]
        self.assertEqual(sens["sign_flips"], 0)
        self.assertEqual(sens["category_changes"], 0)
        self.assertEqual(sens["selection_changes"], 0)
        self.assertEqual(sens["verdict"], "结论稳定")

    def test_factor_data_not_mutated(self):
        """扰动在深拷贝上进行，原因子表不得被改动。"""
        r = run_material("demo")
        f = r["factor_data"]
        self.assertEqual(f["baseline_transport_distance_km"], 100.0)
        self.assertEqual(float(f["factors"]["DEMO-PP-RECYCLE-PROCESS"]["value"]), 400.0)

    def test_anchor_swings_locked(self):
        """锁定 tornado 数据源：三个扰动杠杆的摆动值与手算一致，且按幅度降序。"""
        sens = run_material("demo")["sensitivity"]
        self.assertEqual(sens["anchor_label"], "外省再生企业（演示）")
        swings = {s["name"]: s["anchor_swing"] for s in sens["scenarios"]}
        self.assertAlmostEqual(swings["再生PP颗粒生产因子 −10%"], 600.0, places=2)
        self.assertAlmostEqual(swings["再生PP颗粒生产因子 +10%"], -600.0, places=2)
        self.assertAlmostEqual(swings["运输因子 −10%"], 42.45, places=2)
        self.assertAlmostEqual(swings["运输因子 +10%"], -42.45, places=2)
        self.assertAlmostEqual(swings["基准运输距离 −20%"], -22.34, places=2)
        self.assertAlmostEqual(swings["基准运输距离 +20%"], 22.34, places=2)

if __name__ == "__main__":
    unittest.main()