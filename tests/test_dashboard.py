"""dashboard.py 单元测试：八板块、无外部资源、数据来源、转义与演示标注。"""

import unittest

from src.dashboard import build_material_dashboard_html
from src.material import run_material


class TestBoards(unittest.TestCase):
    def test_eight_boards_present(self):
        result = run_material("demo")
        html = build_material_dashboard_html(result)
        for board in ("board-overview", "board-gates", "board-net", "board-mix",
                      "board-options", "board-n1", "board-passport", "board-evidence"):
            self.assertIn(f"id='{board}'", html)

    def test_no_external_resources(self):
        # 零外部资源：link 仅允许 data: 内联 favicon，无任何脚本/样式/网络 API 引用
        import re
        html = build_material_dashboard_html(run_material("demo"))
        self.assertNotIn("<script src", html)
        links = re.findall(r"<link[^>]*>", html)
        for link in links:
            self.assertIn("rel='icon'", link)
            self.assertIn("href='data:", link)
        self.assertNotIn("<script src", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("url(http", html)

    def test_numbers_come_from_result(self):
        result = run_material("demo")
        html = build_material_dashboard_html(result)
        for option in result["options"]:
            if option.get("net_avoided_kgco2e") is not None:
                self.assertIn(f"{option['net_avoided_kgco2e']:.0f}", html)
        for entry in result["n1_stress"]:
            self.assertIn(f"{entry['min_delivery_service_rate']:.0%}", html)

    def test_hostile_names_escaped(self):
        result = run_material("demo")
        hostile = '<script>alert(1)</script>'
        result["supplies"][0].supplier_name = hostile
        html = build_material_dashboard_html(result)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)", html)
        self.assertEqual(html.count("</script>"), 0)

    def test_demo_notice_visible(self):
        html = build_material_dashboard_html(run_material("demo"))
        self.assertIn("演示", html)
        self.assertIn("不得用于真实决策", html)


if __name__ == "__main__":
    unittest.main()
