"""feedback.py 单元测试：只读统计与复盘，不自动修改任何规则配置。"""

import tempfile
import unittest
from pathlib import Path

from src import feedback


class TestFeedback(unittest.TestCase):
    def test_stats_only(self):
        content = (
            "scenario_id,candidate_id,decision,reason_code,notes,reviewed_at,rule_version\n"
            "DEM-1,SUP-01,通过,M-OK,批次证据已核,2026-09-01,v1\n"
            "DEM-1,SUP-02,否决,M-BAD,规格不符,2026-09-01,v1\n"
            "DEM-1,SUP-03,补证,M-DATA,缺检测报告,2026-09-02,v1\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.csv"
            path.write_text(content, encoding="utf-8-sig")
            records, stats = feedback.load_feedback_csv(path)
        self.assertEqual(stats["accepted"], 3)
        summary = feedback.feedback_stats(records)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_decision"], {"通过": 1, "否决": 1, "补证": 1})

    def test_review_validation_and_disagreement(self):
        records = [
            {"scenario_id": "D1", "candidate_id": "SUP-01", "decision": "通过", "reason_code": "M-OK",
             "reviewed_at": "2026-09-01", "rule_version": "v1"},
            {"scenario_id": "D1", "candidate_id": "SUP-01", "decision": "通过", "reason_code": "M-OK",
             "reviewed_at": "2026-09-01", "rule_version": "v1"},  # 重复
            {"scenario_id": "D2", "candidate_id": "SUP-02", "decision": "其他", "reason_code": "M-BAD",
             "reviewed_at": "bad-date", "rule_version": ""},
        ]
        system_states = {("D1", "SUP-01"): "eligible"}
        review = feedback.review_feedback(records, system_states)
        self.assertEqual(review["validation"]["duplicates_removed"], 1)
        self.assertEqual(review["validation"]["invalid_decision"], 1)
        self.assertEqual(review["validation"]["invalid_date"], 1)
        self.assertEqual(review["validation"]["missing_rule_version"], 1)
        self.assertEqual(review["disagreement_matrix"], {("eligible", "通过"): 1})
        self.assertIn("尚不能校准", review["calibration_status"])
        self.assertIn("人工", review["note"])


if __name__ == "__main__":
    unittest.main()
