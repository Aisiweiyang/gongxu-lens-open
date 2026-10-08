"""安全渲染再生材料决策报告：Markdown 与 HTML。"""

import re
from datetime import date
from html import escape

from . import config

_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def md_to_html(text):
    out, table_rows, in_table = [], [], False
    for line in text.split("\n"):
        if line.startswith("| ") and "|" in line[2:]:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not in_table:
                in_table, table_rows = True, []
            if not all(not cell or set(cell) <= {"-", ":"} for cell in cells):
                table_rows.append(cells)
            continue
        if in_table:
            out.append(_table_html(table_rows))
            in_table = False
        out.append(_para(line))
    if in_table:
        out.append(_table_html(table_rows))
    return "\n".join(out)


def _para(line):
    safe = escape(line, quote=False)
    safe = _BOLD_RE.sub(r"<b>\1</b>", safe)
    safe = _LINK_RE.sub(r'<a href="\2">\1</a>', safe)
    if safe.startswith("### "):
        return f"<h3>{safe[4:]}</h3>"
    if safe.startswith("## "):
        return f"<h2>{safe[3:]}</h2>"
    if safe.startswith("# "):
        return f"<h1>{safe[2:]}</h1>"
    if safe.strip() == "---":
        return "<hr>"
    if safe.startswith("&gt; ⚠️"):
        return f"<p style='background:#fff3cd;border-left:4px solid #f0ad4e;padding:10px'>{safe[5:].strip()}</p>"
    if safe.strip().startswith("- "):
        return f"<li>{safe.strip()[2:]}</li>"
    if not safe.strip():
        return "<br>"
    return f"<p>{safe}</p>"


def _table_html(rows):
    if not rows:
        return ""
    thead = "".join(f"<th>{escape(cell)}</th>" for cell in rows[0])
    tbody = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows[1:]
    )
    return (
        "<table border='1' cellpadding='6' style='border-collapse:collapse;width:100%'>"
        f"<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
    )


def to_html(markdown, title):
    body = md_to_html(markdown)
    return (
        "<html><head><meta charset='utf-8'><title>"
        + escape(title)
        + "</title></head><body style='font-family:sans-serif;line-height:1.7;max-width:900px;"
        "margin:0 auto;padding:24px;color:#222'>"
        + body
        + "<p style='color:#888;font-size:12px'>本报告只描述本次需求与导入供给的匹配结果与情景净减排，"
        "不构成交易、合规或投资结论。</p>"
        "</body></html>"
    )


def _inline(value):
    return " ".join(str(value or "").replace("|", "/").splitlines()).strip()


def _cell(value):
    return _inline(value).replace("|", "/")


def _fmt_opt(value, spec):
    return format(value, spec) if value is not None else "-"


# —— 再生材料主线报告 ——


def build_material_markdown(result):
    """再生 PP 主线报告：供需概览、三态门槛、净减排手算、组合方案、决策选项、
    绿色成本、N-1 断供压力测试、决策护照与口径边界。"""
    demand = result["demand"]
    lines = [
        f"# 再生{_inline(demand.target_material_code)}供需匹配与净减排决策",
        "",
        f"生成日期：{date.today():%Y-%m-%d}　　{_inline(result.get('data_note', ''))}",
        "",
    ]
    for warning in result.get("warnings") or []:
        lines.append(f"> ⚠️ {_inline(warning)}")
    lines += ["", "## 供需概览", "",
              f"- 需求：{_inline(demand.buyer_name)}，{_inline(demand.target_material_code)}，"
              f"{_inline(demand.application)}；需求 {_fmt_opt(demand.required_mass_t, 'g')} t；"
              f"再生含量 ≥ {_fmt_opt(demand.min_recycled_content_pct, 'g')}%；"
              f"熔指 {_fmt_opt(demand.mfi_min, 'g')}~{_fmt_opt(demand.mfi_max, 'g')} g/10min；"
              f"基准原生单价 {_fmt_opt(demand.baseline_virgin_price_cny_per_t, 'g')} CNY/t；"
              f"距离上限 {_fmt_opt(demand.max_distance_km, 'g')} km",
              "",
              "功能单位：交付 1 吨满足需方规格的合格颗粒；各方案分配总量必须等于需求量。",
              ""]
    lines += ["| 供给批次 | 供应商 | 状态 | 关键规格 | 价格 CNY/t | 距离 km | 可供 t |",
              "|---|---|---|---|---:|---:|---:|"]
    for supply, state in zip(result["supplies"], result["match_states"]):
        status = {"eligible": "可比较", "pending_evidence": "待核验", "ineligible": "不符合"}[state["state"]]
        lines.append(
            f"| {_cell(supply.supply_id)} | {_cell(supply.supplier_name)} | {_cell(status)} | "
            f"{_cell(supply.material_name_raw)}（熔指 {_fmt_opt(supply.mfi_g_10min, 'g')}） | "
            f"{_fmt_opt(supply.price_cny_per_t, 'g')} | "
            f"{_fmt_opt(supply.distance_km, 'g')} | "
            f"{_fmt_opt(supply.available_mass_t, 'g')} |"
        )
    lines += ["", "## 替代性硬门槛", ""]
    for state in result["match_states"]:
        label = {"eligible": "可比较", "pending_evidence": "待核验", "ineligible": "不符合"}[state["state"]]
        lines.append(f"### {_inline(state['supplier_name'])}（{_inline(label)}）")
        lines.append("")
        lines.append("| 门槛 | 判定 | 说明 |")
        lines.append("|---|---|---|")
        for gate in state["gates"]:
            gate_label = {"pass": "通过", "fail": "不通过", "unknown": "未知"}[gate["status"]]
            lines.append(f"| {_cell(gate['name'])} | {_cell(gate_label)} | {_cell(gate['detail'])} |")
        lines.append("")
    quality = result.get("quality") or []
    if quality:
        lines += ["## 证据质量多维评分（pedigree 思路）", "",
                  "来源存在/来源类型/独立核验/附件可追溯/字段覆盖/时效/地理代表性/技术相关性"
                  "各 1-5 分（1 最优），几何平均合成（启发式，不是统计置信度）；"
                  "质量分只描述证据强弱，不替代硬门槛判定。", ""]
        lines += ["| 批次 | 质量分 | 等级 | 最短板 | 门槛状态 |", "|---|---:|---|---|---|"]
        for row in quality:
            lines.append(f"| {_cell(row['supply_id'])} | {row['score']:.2f} | {_cell(row['grade'])} | "
                         f"{_cell(row['weakest'] or '无短板')} | {_cell(row['gate_label'])}"
                         + (f"（{_cell(row['gate_note'])}）" if row.get("gate_note") else ""))
        lines.append("")
    greedy = result.get("greedy_baseline") or {}
    if greedy.get("computable"):
        alloc = "、".join(f"{_inline(k)} {v:g} t" for k, v in greedy["allocation"].items())
        enum_text = (f"{greedy['enumeration_best_cost']:g}"
                     if greedy.get("enumeration_best_cost") is not None else "无可比较方案")
        lines.append("贪心基线对照：按成本最低优先采购得 " + alloc
                     + f"，总成本 {greedy['total_cost_cny']:g} CNY"
                     f"（枚举最优 {enum_text} CNY，"
                     f"{'一致' if greedy['cost_match'] else '不一致'}）。"
                     f"{_inline(greedy['verdict'])}")
        lines.append("")
    lines += ["## 净减排核算（可手算展开）", "",
              "E_baseline = Q × 原生PP生产因子 + Q × 基准运输距离 × 基准运输因子；",
              "E_project = Q × 再生PP颗粒生产因子 + Σ(分配量 × 各候选距离 × 各候选运输因子)。", ""]
    for item in result["singles"]:
        net = item.get("net")
        if not net:
            continue
        supply = item["supply"]
        lines.append(f"### {_inline(supply.supplier_name)}：{_fmt_opt(supply.distance_km, 'g')} km")
        lines.append("")
        lines.append("| 项目 | 活动量(t) | 因子 | 因子值 | 单位 | 边界 | 来源版本 |")
        lines.append("|---|---:|---|---|---|---|---|")
        for term in net["terms"]:
            factor = term["factor"]
            lines.append(
                f"| {_cell(term['term'])} | {term['activity_t']:.3f} | {_cell(factor.get('factor_id', ''))} | "
                f"{float(factor.get('value', 0)):g} | {_cell(factor.get('unit', ''))} | "
                f"{_cell(factor.get('boundary', ''))} | {_cell(factor.get('source_name', ''))} v{factor.get('version', '')} |"
            )
        lines.append("")
        lines.append(
            f"- 基准排放 {net['baseline_kgco2e']:.2f} kgCO2e，项目排放 {net['project_kgco2e']:.2f} kgCO2e，"
            f"净减排 {net['net_avoided_kgco2e']:.2f} kgCO2e（{_inline(net['net_sign'])}）"
        )
        lines.append(f"- {_inline(net['boundary_note'])}")
        lines.append("")
    mix = result.get("mix") or {}
    lines += ["## 组合供料方案", ""]
    if mix.get("plans"):
        lines.append(f"- {_inline(mix.get('note', ''))}")
        for plan in mix["plans"][:10]:
            labels = plan.get("allocation_labels") or {}
            alloc = "、".join(f"{_inline(labels.get(k, k))} {v:g} t" for k, v in plan["allocation"].items())
            lines.append(
                f"  - 方案（{_inline(plan.get('option_id', ''))}）：{alloc}；"
                f"到厂总成本 {plan['total_cost_cny']:g} CNY；"
                f"净减排 {plan['net_avoided_kgco2e']:.2f} kgCO2e；"
                f"订单最大份额 {plan['order_max_share']:.0%}"
            )
    elif mix.get("error"):
        lines.append(f"- {_inline(mix['error'])}")
    else:
        lines.append("- 无多供方组合方案。")
    lines += ["", "## 决策选项与绿色成本", ""]
    lines += ["| 方案 | 类型 | 方案ID | 总成本 CNY | 成本差 CNY | 净减排 kgCO2e | 边际减排成本 CNY/tCO2e | 分类 |"]
    lines += ["|---|---|---|---:|---:|---:|---:|---|"]
    for option in result["options"]:
        net_text = f"{option['net_avoided_kgco2e']:.0f}" if option.get("net_avoided_kgco2e") is not None else "待补证据"
        cost_text = f"{option['total_cost_cny']:g}" if option.get("total_cost_cny") is not None else "缺价"
        delta_text = f"{option['cost_delta_cny']:+.0f}" if option.get("cost_delta_cny") is not None else "—"
        marginal = f"{option['marginal_abatement_cost_cny_per_tco2e']:.0f}"             if option.get("marginal_abatement_cost_cny_per_tco2e") is not None else "—"
        lines.append(
            f"| {_cell(option['label'])} | {_cell(option['kind'])} | {_cell(option.get('option_id', ''))} | "
            f"{cost_text} | {delta_text} | {net_text} | {marginal} | {_cell(option['category'])} |"
        )
    lines += ["", "## N-1 断供压力测试", ""]
    n1 = result.get("n1_stress") or []
    if n1:
        for entry in n1:
            worst = entry["worst_case"]
            lines.append(
                f"- {_inline(entry['label'])}：最低交付服务率 {entry['min_delivery_service_rate']:.0%}"
                f"（{_inline(worst['failed_supplier'])} 不可用时缺口 {worst['gap_t']:g} t）；"
                f"单点故障：{'、'.join(_inline(x) for x in entry['single_point_failures']) or '无'}"
            )
    else:
        lines.append("- 无代表组合，未做压力测试。")
    sens = result.get("sensitivity")
    if sens:
        lines += ["", "## 结论稳定性核查（确定性扰动，非随机）", ""]
        lines.append("、".join(s["name"] for s in sens["scenarios"]))
        lines.append(
            f"- 共 {len(sens['scenarios'])} 个扰动场景：净减排方向翻转 {sens['sign_flips']} 处；"
            f"方案分类变化 {sens['category_changes']} 处；入选方案集合变化 {sens['selection_changes']} 个场景。"
        )
        lines.append(f"- {sens['note']}")
        lines.append(f"- **{sens['verdict']}**（扰动范围为演示假设，来自因子配置注册表，"
                      "不是统计置信区间）；真实数据下若结论会翻转，如实报告翻转点与场景，不加工。")
        lines.append("")
    lines += ["", "## 绿色匹配决策护照", ""]
    for option in result.get("representatives") or []:
        lines.append(
            f"- {_inline(option['label'])}：总成本 {_fmt_opt(option.get('total_cost_cny'), 'g')} CNY"
            f"（较基准 {_fmt_opt(option.get('cost_delta_cny'), '+.0f')} CNY）；"
            f"净减排 {_fmt_opt(option.get('net_avoided_kgco2e'), '.0f')} kgCO2e；"
            f"分类：{_inline(option['category'])}"
        )
    lines += ["", "- 下一步不是立即交易：对「待核验」供给补送样检测；与最低成本供给议价；",
              "- 核对基准运输距离假设与各批次实际运输路线。",
              "", "## 公开市场背景（真实企业，与演示数据分开）", "",
              "以下为公开报道中的行业参与者，仅作市场背景与候选线索，不是本演示的供给批次；"
              "演示批次全部为虚构。来源以官方公告为准，未逐一核实。", ""]
    import csv as _csv
    players_path = config.BASE_DIR / "benchmarks" / "industry_players.csv"
    if players_path.exists():
        with open(players_path, encoding="utf-8-sig", newline="") as _f:
            players = [row for row in _csv.DictReader(_f)]
        lines += ["| 企业 | 代码 | 再生材料业务 | 关键事实 | 来源日期 |",
                  "|---|---|---|---|---|"]
        for p in players:
            lines.append(f"| {_cell(p['company_name'])} | {_cell(p.get('stock_code') or '-')} | "
                         f"{_cell(p['business_recycled'])} | {_cell(p['key_fact'])} | "
                         f"{_cell(p.get('source_date') or '-')} |")
    lines += ["", "## 口径边界", "",
              "- 净减排为情景核算（系统边界：原生颗粒生产与运输、再生颗粒生产与运输），不是完整生命周期碳足迹。",
              "- 基准情景假设：原生粒料运输距离取配置值（演示假设），改变它只影响基准侧。",
              "- 材料因子为演示假设因子（虚构值）；运输因子为英国 DEFRA 2024（TTW，非中国口径）。",
              "- 演示企业、批次、价格全部虚构，不得用于真实决策。",
              "- 只描述本次需求与导入供给的匹配结果，不构成全市场供需结论。"]
    return "\n".join(lines)
