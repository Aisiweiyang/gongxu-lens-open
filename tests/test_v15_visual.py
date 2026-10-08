"""v15 任务 4：零依赖 SVG 可视化回归（站点 + 仪表盘）。

约束验证：demo 冻结数值逐字符不变；新增图表（Pareto/N-1 条形/敏感性 tornado）
存在且确定性渲染（剥离护照时间戳后字节一致）；区间图仅在因子带
value_min/value_max 时出现（demo 自动隐藏）；每图伴随等价数据表。
"""

import copy
import re
import unittest
from html import escape

from src.dashboard import build_material_dashboard_html
from src.material import analyze_task, load_material_factors, run_material
from src.site_builder import build_site_html
from src.visuals import interval_svg, n1_service_svg, pareto_scatter_svg

_TS_RE = re.compile(r'"generated_at": "[^"]*"')


def _render_demo():
    result = run_material("demo")
    return result, build_site_html(result), build_material_dashboard_html(result)


class V15SiteVisuals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, cls.site, cls.dashboard = _render_demo()

    def test_demo_charts_present(self):
        self.assertIn("id='pareto-svg'", self.site)
        self.assertIn("id='n1-service-svg'", self.site)
        self.assertIn("id='tornado-svg'", self.site)
        self.assertIn("id='pareto-svg'", self.dashboard)
        self.assertIn("id='n1-service-svg'", self.dashboard)

    def test_demo_interval_chart_hidden(self):
        """demo 因子无区间：区间图与区间表不出现（口径不变）。"""
        self.assertNotIn("id='interval-svg'", self.site)
        self.assertNotIn("id='interval-svg'", self.dashboard)
        self.assertNotIn("区间下界", self.site)

    def test_demo_frozen_values_unchanged(self):
        """冻结数值逐字符不变（基准 83,025；组合前沿最低 59,941）。

        试点端固定案例锚点（64,182 / 21,072.98）由 Windows e2e_v12 四方核对把守，
        不在静态站点渲染范围。"""
        self.assertIn("83,025", self.site)
        self.assertIn("59,941", self.site)

    def test_pareto_upgrade_elements(self):
        """v15 升级元素：气泡=份额、颜色=分类、基准参考线、图例。"""
        self.assertIn("data-category=", self.site)
        self.assertIn("气泡大小=订单最大份额", self.site)
        self.assertIn("基准 83,025", self.site)  # 基准成本参考线标注
        for category in ("成本与碳双降", "绿色溢价", "省钱但增排", "成本与碳双升"):
            self.assertIn(category, self.site)

    def test_render_deterministic_modulo_timestamp(self):
        """同输入两次渲染：剥离护照 generated_at 时间戳后字节一致。"""
        result = run_material("demo")
        a = _TS_RE.sub('"generated_at": ""', build_site_html(result))
        b = _TS_RE.sub('"generated_at": ""', build_site_html(result))
        self.assertEqual(a, b)

    def test_interval_chart_with_ranged_factors(self):
        """因子带区间：区间图 + 等价数据表出现，注记非统计区间。"""
        factor_data = copy.deepcopy(load_material_factors())
        factor_data["factors"]["DEMO-PP-RECYCLE-PROCESS"]["value_min"] = 350.0
        factor_data["factors"]["DEMO-PP-RECYCLE-PROCESS"]["value_max"] = 450.0
        result = analyze_task(self.result["demand"], self.result["supplies"],
                              factor_data, resolved_mode="demo", requested_mode="demo")
        site = build_site_html(result)
        self.assertIn("id='interval-svg'", site)
        self.assertIn("区间下界", site)
        self.assertIn("非统计置信区间", site)


class VisualHelpers(unittest.TestCase):
    def test_pareto_svg_requires_two_points(self):
        self.assertIn("不足两个", pareto_scatter_svg([{"cost": 1, "net": 2,
                                                       "option_id": "x", "label": "x",
                                                       "on_frontier": True}]))

    def test_n1_svg_empty_and_rows(self):
        self.assertEqual(n1_service_svg([]), "")
        svg = n1_service_svg([{"label": "方案甲", "min_service": 1.0, "gap_t": 0.0},
                              {"label": "方案乙", "min_service": 0.8, "gap_t": 3.0}])
        self.assertIn("id='n1-service-svg'", svg)
        self.assertIn("80.0%", svg)
        self.assertIn("缺口 3 t", svg)

    def test_interval_svg_empty_without_intervals(self):
        self.assertEqual(interval_svg([{"label": "x", "option_id": "x"}]), "")

    def test_interval_svg_rows_ordered(self):
        svg = interval_svg([
            {"label": "方案甲", "option_id": "a", "net_avoided_kgco2e": 21000.0,
             "net_interval_kgco2e": [19000.0, 23000.0]},
            {"label": "方案乙", "option_id": "b", "net_avoided_kgco2e": 20500.0,
             "net_interval_kgco2e": [18000.0, 24000.0]}])
        self.assertIn("id='interval-svg'", svg)
        self.assertIn("19,000", svg)
        self.assertIn("24,000", svg)
        self.assertIn("21,000", svg)


class LabelSlotFitting(unittest.TestCase):
    """标签槽自适应：长标签加宽槽位后完整落在视口内；超上限截断加省略号、<title> 保留全名。"""

    LONG_DEMO_LABEL = "组合：外省再生企业（演示） 7t + 本地再生资源有限公司（演示） 8t"

    @staticmethod
    def _viewbox_w(svg):
        return float(re.search(r"viewBox='0 0 ([\d.]+) ", svg).group(1))

    def test_n1_long_label_fully_inside_viewport(self):
        from src.visuals import _est_text_width
        svg = n1_service_svg([{"label": self.LONG_DEMO_LABEL, "min_service": 0.8,
                               "gap_t": 3.0},
                              {"label": "方案乙", "min_service": 1.0, "gap_t": 0.0}])
        self.assertGreater(self._viewbox_w(svg), 660.0)  # 视口按最长标签加宽
        m = re.search(r"<text x='([\d.]+)' y='43'[^>]*text-anchor='end'>", svg)
        anchor_x = float(m.group(1))
        # 标签锚点 − 标签估算宽度 ≥ 0：文本整体落在视口内
        self.assertGreaterEqual(anchor_x - _est_text_width(self.LONG_DEMO_LABEL), 0.0)
        self.assertIn(self.LONG_DEMO_LABEL, svg)  # 未截断：全名可见

    def test_n1_extreme_label_ellipsis_with_title(self):
        """超加宽上限的极端长标签：截断加省略号，<title> 保留全名。"""
        label = "超长供方名称" * 60
        svg = n1_service_svg([{"label": label, "min_service": 0.5, "gap_t": 1.0}])
        self.assertIn(f">{escape(label)}</title></text>", svg)
        self.assertRegex(svg, r"text-anchor='end'>[^<]*…<title>")

    def test_interval_long_label_widens_too(self):
        from src.visuals import _est_text_width
        label = "组合：" + "很长的再生供给企业名称（演示）" * 2  # 33 全角，加宽后可完整容纳
        svg = interval_svg([
            {"label": label, "option_id": "a", "net_avoided_kgco2e": 21000.0,
             "net_interval_kgco2e": [19000.0, 23000.0]}])
        self.assertGreater(self._viewbox_w(svg), 660.0)
        m = re.search(r"<text x='([\d.]+)'[^>]*text-anchor='end'>", svg)
        self.assertGreaterEqual(float(m.group(1)) - _est_text_width(label), 0.0)
        self.assertIn(label, svg)  # 未截断：全名可见

    def test_interval_extreme_label_ellipsis_with_title(self):
        """区间图超上限标签：截断加省略号 + <title> 全名（与 N-1 图同口径）。"""
        label = "超长供方名称" * 60
        svg = interval_svg([
            {"label": label, "option_id": "a", "net_avoided_kgco2e": 21000.0,
             "net_interval_kgco2e": [19000.0, 23000.0]}])
        self.assertIn(f">{escape(label)}</title></text>", svg)

    def test_short_labels_output_unchanged(self):
        """短标签：不触发加宽与截断。"""
        svg = n1_service_svg([{"label": "方案甲", "min_service": 1.0, "gap_t": 0.0}])
        self.assertIn("viewBox='0 0 660 ", svg)
        self.assertNotIn("<title>", svg)


if __name__ == "__main__":
    unittest.main()
