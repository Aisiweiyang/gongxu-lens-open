"""site_builder.py 单元测试：首屏结论、九段叙事、企业检索、离线与可访问性。"""

import unittest

from src.material import run_material
from src.site_builder import build_site_html


class TestSite(unittest.TestCase):
    def setUp(self):
        self.result = run_material("demo")
        self.html = build_site_html(self.result)

    def test_hero_verdict_has_conclusion(self):
        self.assertIn("本次决策结论", self.html)
        # 首屏结论数字来自结果对象（最低成本方案成本必须出现在结论里）
        cheapest = min((o for o in self.result["representatives"]
                        if o.get("total_cost_cny") is not None and o["kind"] != "baseline"),
                       key=lambda o: o["total_cost_cny"])
        self.assertIn(f"{cheapest['total_cost_cny']:,.0f}", self.html)

    def test_nine_sections_and_steps(self):
        for section in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9"):
            self.assertIn(f"id='{section}'", self.html)
        self.assertIn("1. 供需", self.html)
        self.assertIn("商业闭环与 2–4 周试点入口", self.html)

    def test_company_search_present(self):
        self.assertIn("company-search", self.html)
        self.assertIn("企业报告检索", self.html)

    def test_no_external_resources(self):
        # 业务理由：站点必须零外部资源；favicon 只允许 data: 内联
        # （data URI 内部的 XML 命名空间文字是声明，不是网络引用）；
        # 因子来源 URL 只出现在内嵌 JSON 数据中（数据，非资源引用），
        # 页面标记层不允许任何 http(s) 资源引用。
        import re
        markup = re.sub(r"<script type='application/json'.*?</script>", "", self.html, flags=re.S)
        markup = re.sub(r"<link[^>]*href='data:[^']*'\s*>", "", markup)
        self.assertNotIn("<script src", self.html)
        self.assertNotIn("<link rel=\"stylesheet\"", self.html)
        self.assertNotIn("http://", markup)
        self.assertNotIn("https://", markup)
        icon = re.search(r"<link[^>]*rel='icon'[^>]*>", self.html)
        self.assertIsNotNone(icon)
        self.assertIn("href='data:image/", icon.group(0))
        self.assertNotIn("fetch(", self.html)
        self.assertNotIn("XMLHttpRequest", self.html)

    def test_aria_buttons(self):
        self.assertGreaterEqual(self.html.count("aria-pressed"), 4)

    def test_demo_badge_and_disclaimer(self):
        self.assertIn("演示数据", self.html)
        self.assertIn("不得用于真实决策", self.html)

    def test_optimistic_toggle_real_data(self):
        # 业务理由：两个口径的选项与 N-1 改为按场景快照组织
        # （data.scenarios.conservative / optimistic），JS 单一渲染管线消费同一快照。
        import json
        import re
        m = re.search(r"id='data-json'>(.*?)</script>", self.html, re.S)
        data = json.loads(m.group(1))
        self.assertIn("scenarios", data)
        cons = data["scenarios"]["conservative"]
        opt = data["scenarios"]["optimistic"]
        self.assertIn("options", cons)
        self.assertIn("n1", opt)
        cons_labels = " ".join(o["label"] for o in cons["options"])
        opt_labels = " ".join(o["label"] for o in opt["options"])
        # 保守口径下待核验的邻区批次不进入任何方案；乐观口径下进入组合
        self.assertNotIn("邻区再生企业", cons_labels)
        self.assertIn("邻区再生企业", opt_labels)
        # 乐观口径下断供服务率达到 100%（保守 <100%）；归因分开：见 n1_compare_*
        self.assertLess(min(e["min_service"] for e in cons["n1"]), 1.0)
        self.assertEqual(min(e["min_service"] for e in opt["n1"]), 1.0)
        # 同一场景内选项、Pareto、N-1 用 option_id 对齐
        cons_option_ids = {o["option_id"] for o in cons["options"]}
        self.assertTrue(cons_option_ids)
        for point in cons["pareto"]:
            self.assertIn(point["option_id"], cons_option_ids)

    def test_hero_comparison_bars(self):
        # 业务理由：成本改为到厂成本分项口径、删除固定叙事数字，
        # 首屏数字全部来自结果对象。
        baseline = next(o for o in self.result["representatives"] if o["kind"] == "baseline")
        cheapest = min((o for o in self.result["representatives"]
                        if o.get("total_cost_cny") is not None and o["kind"] != "baseline"),
                       key=lambda o: o["total_cost_cny"])
        self.assertIn(f"{baseline['total_cost_cny']:,.0f}", self.html)
        self.assertIn(f"{cheapest['total_cost_cny']:,.0f}", self.html)
        n1_single = next(e for e in self.result["n1_stress"] if e["kind"] == "single")
        self.assertIn(f"{n1_single['min_delivery_service_rate']:.1%}", self.html)
        # 旧的 82,500/57,000 硬编码叙事数字不再出现
        self.assertNotIn("82,500", self.html)
        self.assertNotIn("57,000", self.html)

    def test_js_rendered_tables(self):
        self.assertIn("id='options-table'", self.html)
        self.assertIn("id='n1-body'", self.html)
        self.assertIn("renderOptions", self.html)
        self.assertIn("renderN1", self.html)

    def test_pareto_svg_frontier_matches_computed(self):
        # 业务理由：图点与表格按 option_id 关联（同名称不同分配
        # 是不同方案），_nondominated 已改为按 option_id 身份比较。
        import re
        from src.site_builder import _nondominated_by_id
        m = re.search(r"<svg id='pareto-svg'.*?</svg>", self.html, re.S)
        self.assertIsNotNone(m)
        svg = m.group(0)
        opts = [o for o in self.result["options"] if o["kind"] != "baseline"
                and o.get("total_cost_cny") is not None and o.get("net_avoided_kgco2e") is not None]
        self.assertEqual(svg.count("<circle"), len(opts))
        self.assertIn("polyline", svg)
        front = _nondominated_by_id(opts)
        self.assertGreaterEqual(len(front), 1)
        for o in front:
            self.assertIn(o["option_id"], svg)

    def test_tornado_svg_six_scenarios(self):
        import re
        from src.material import run_material
        sens = run_material("demo")["sensitivity"]
        m = re.search(r"<svg id='tornado-svg'.*?</svg>", self.html, re.S)
        self.assertIsNotNone(m)
        svg = m.group(0)
        self.assertEqual(svg.count("<rect"), 6)
        self.assertIn(sens["anchor_label"], self.html)
        for s in sens["scenarios"]:
            self.assertIn(s["name"], svg)

    def test_demo_mode_and_speaker_notes(self):
        self.assertIn("demo-toggle", self.html)
        self.assertIn("demo-progress", self.html)
        for sid in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10"):
            self.assertIn('"' + sid + '"', self.html)  # 演讲者注释键覆盖全部 10 个板块
        self.assertIn("演讲者注", self.html)

if __name__ == "__main__":
    unittest.main()