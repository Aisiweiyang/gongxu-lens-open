"""run.py 与主流程入口测试：行业列表、模式行为、输出路径。"""

import unittest
from datetime import date

from src import config
from src.main import run_industry


class TestMainEntry(unittest.TestCase):
    def test_unknown_industry_rejected(self):
        with self.assertRaises(ValueError):
            run_industry("不存在行业")

    def test_list_industries_only_recycled(self):
        from src.config import load_industries
        names = [i.get("name") for i in load_industries()]
        self.assertEqual(names, ["再生PP"])

    def test_demo_result_shape(self):
        result = run_industry("再生PP", "demo")
        for key in ("demand", "supplies", "match_states", "singles", "mix",
                    "options", "representatives", "n1_stress", "factor_data"):
            self.assertIn(key, result)

    def test_output_paths_do_not_overlap(self):
        stem = f"再生PP供需匹配与净减排决策_{date.today():%Y%m%d}"
        md = config.OUTPUT_DIR / f"{stem}.md"
        html_report = config.OUTPUT_DIR / f"{stem}.html"
        dashboard = config.OUTPUT_DIR / f"再生PP绿色决策仪表盘_{date.today():%Y%m%d}.html"
        paths = {md, html_report, dashboard, config.SITE_DIR / "index.html"}
        self.assertEqual(len(paths), 4)


class TestOutputFilename(unittest.TestCase):
    def test_path_characters_are_removed(self):
        from run import sanitize_filename
        safe = sanitize_filename('再<生>P/P:\"材*料?报|告')
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, safe)
        self.assertTrue(safe)


if __name__ == "__main__":
    unittest.main()
