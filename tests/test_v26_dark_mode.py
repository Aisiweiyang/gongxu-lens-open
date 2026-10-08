"""深色模式回归：四端深色块与配色声明不被后续改动静默移除。

锁定对象：评委网站与离线仪表盘的 dark 媒体查询块、试点工作台样式表的
dark 令牌块、撮合建议单的亮色钉定 meta。人工走查截图见 docs/e2e-shots/v26-dark/。
"""

import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class TestV26DarkMode(unittest.TestCase):
    def test_dark_mode_surfaces(self):
        # 评委网站：dark 媒体查询与双配色声明在生成产物中
        site_css = (PROJECT / "src" / "site_builder.py").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-color-scheme: dark)", site_css)
        self.assertIn("color-scheme' content='light dark'", site_css)
        self.assertIn("color-scheme:dark;--ink:", site_css)

        # 离线仪表盘：dark 块与双配色声明
        dash_css = (PROJECT / "src" / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-color-scheme:dark)", dash_css)
        self.assertIn("color-scheme:light dark;", dash_css)

        # 试点工作台：dark 令牌块
        pilot_css = (PROJECT / "pilot" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-color-scheme:dark)", pilot_css)
        self.assertIn("color-scheme:dark;", pilot_css)

        # 撮合建议单：打印友好设计钉定亮色（深色 UA 下画布不得变暗）
        sheet_src = (PROJECT / "src" / "matching_sheet.py").read_text(encoding="utf-8")
        self.assertIn('content=\\"light\\"', sheet_src)


if __name__ == "__main__":
    unittest.main()
