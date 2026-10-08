"""离线仪表盘：把再生材料决策结果渲染成自包含 HTML（单文件、无外部资源）。

板块：供需概览、替代性硬门槛、净减排核算（逐项可手算）、组合供料方案、
决策选项与绿色成本、N-1 断供压力测试、绿色匹配决策护照、证据与口径边界。
数据只来自统一结果对象，不在模板里另写数字。
"""

from datetime import date
from html import escape

from .scenarios import pareto_points
from .visuals import interval_svg, n1_service_svg, pareto_scatter_svg

_STATE_LABEL = {"eligible": "可比较", "pending_evidence": "待核验", "ineligible": "不符合"}
_GATE_STATUS_LABEL = {"pass": "通过", "fail": "不通过", "unknown": "未知"}

FAVICON = "data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2032%2032%27%3E%3Cdefs%3E%3ClinearGradient%20id%3D%27g%27%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%271%27%20y2%3D%271%27%3E%3Cstop%20offset%3D%270%27%20stop-color%3D%27%233f9d74%27%2F%3E%3Cstop%20offset%3D%271%27%20stop-color%3D%27%23144d33%27%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Crect%20width%3D%2732%27%20height%3D%2732%27%20rx%3D%278%27%20fill%3D%27url%28%23g%29%27%2F%3E%3Ccircle%20cx%3D%2716%27%20cy%3D%2716%27%20r%3D%276.5%27%20fill%3D%27%23fff%27%2F%3E%3C%2Fsvg%3E"

_CSS = """
:root{color-scheme:light dark;--ink:#18222c;--muted:#5f6f7d;--faint:#8b99a6;--line:#dfe6ec;--line-soft:#e9eef3;--bg:#f2f6f4;--panel:#ffffff;--panel-soft:#f8fbf9;--accent:#1e6b49;--accent-deep:#14503a;--accent-soft:#e9f3ed;--accent-softer:#f2f8f4;--warn:#8a6d1d;--warn-bg:#faf3dd;--danger:#a53333;--danger-bg:#f7e5e5;--info:#34597a;--info-bg:#e8eff6;--shadow:0 1px 2px rgba(24,34,44,.05),0 6px 18px rgba(24,34,44,.06);--shadow-lift:0 2px 6px rgba(24,34,44,.07),0 12px 28px rgba(24,34,44,.09);--radius:14px;--radius-sm:9px}
*{box-sizing:border-box}
::selection{background:var(--accent-soft)}
body{font-family:'Segoe UI','Microsoft YaHei UI','Microsoft YaHei','PingFang SC',system-ui,sans-serif;line-height:1.68;max-width:1080px;margin:0 auto;padding:26px 20px 52px;color:var(--ink);font-size:14px;background:radial-gradient(1100px 400px at 16% -8%,rgba(30,107,73,.055),transparent 60%),var(--bg);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
h1{font-size:24px;margin:8px 0 10px;letter-spacing:.3px;display:flex;align-items:center;gap:11px}
h1::before{content:"";width:12px;height:12px;border-radius:4px;flex:0 0 auto;background:linear-gradient(135deg,var(--accent) 20%,#3f9d74 100%);box-shadow:0 0 0 4px var(--accent-soft)}
h2{font-size:17px;border-bottom:1px solid var(--line);padding-bottom:8px;margin-top:32px;display:flex;align-items:center;gap:10px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;flex:0 0 auto;background:linear-gradient(180deg,var(--accent),#3f9d74)}
h3{font-size:15px;margin:14px 0 6px;color:var(--accent-deep)}
section[id^="board-"]{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px 22px;margin:14px 0;box-shadow:var(--shadow);transition:border-color .18s ease,box-shadow .18s ease}
section[id^="board-"]:hover{border-color:#bcd4c6}
section[id^="board-"] > h2:first-child{margin-top:2px}
svg{max-width:100%;height:auto}
.meta{color:var(--muted);font-size:13px}
.note{background:var(--warn-bg);border-left:4px solid var(--warn);padding:9px 15px;margin:12px 0;font-size:13px;border-radius:0 10px 10px 0}
table{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0;background:var(--panel);font-variant-numeric:tabular-nums;box-shadow:var(--shadow);border-radius:var(--radius-sm)}
th,td{border:1px solid var(--line-soft);padding:7px 11px;text-align:left;vertical-align:top}
th{background:linear-gradient(180deg,#f0f7f2,#e9f2ec);color:var(--accent-deep);font-weight:600;font-size:12px;letter-spacing:.4px}
tbody tr{transition:background .1s ease}
tbody tr:nth-child(even){background:var(--panel-soft)}
tbody tr:hover{background:var(--accent-soft)}
.pos{color:var(--accent-deep);font-weight:600}.neg{color:var(--danger);font-weight:600}.na{color:var(--faint)}
.card{min-width:300px;border:1px solid var(--line);border-radius:var(--radius);padding:14px 18px;background:var(--panel);box-shadow:var(--shadow);transition:box-shadow .18s ease,transform .18s ease;margin:12px 0}
.card:hover{box-shadow:var(--shadow-lift);transform:translateY(-1px)}
.card b{font-size:14px;color:var(--accent-deep)}
.verdict-line{color:var(--muted);font-size:12.5px;line-height:1.75;margin:8px 0 2px}
.verdict-line b{color:var(--ink)}
.grade{background:var(--info-bg);border:1px solid #d2e0ee;border-radius:5px;padding:1px 8px;font-size:12px;color:var(--info)}
footer{color:var(--muted);font-size:12px;margin-top:32px;border-top:1px solid var(--line);padding-top:12px;line-height:1.8}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--ink:#e4ede8;--muted:#9caf9f;--faint:#82968a;--line:#26352d;--line-soft:#1f2b24;--bg:#0e1512;--panel:#151f1a;--panel-soft:#1a2620;--accent:#2f9d6e;--accent-deep:#8fd6b0;--accent-soft:#173527;--accent-softer:#122920;--warn:#d4b04a;--warn-bg:#332a12;--danger:#e07070;--danger-bg:#3a1d1d;--info:#7aa5cc;--info-bg:#1a2a3a;--shadow:0 1px 2px rgba(0,0,0,.35),0 6px 18px rgba(0,0,0,.32);--shadow-lift:0 2px 6px rgba(0,0,0,.4),0 12px 28px rgba(0,0,0,.38)}
body{background:radial-gradient(1100px 400px at 16% -8%,rgba(47,157,110,.07),transparent 60%),var(--bg)}
section[id^="board-"]:hover{border-color:#31473b}
th{background:linear-gradient(180deg,#1c2f26,#182820);color:#a9d8bf}
tbody tr:nth-child(even){background:var(--panel-soft)}
.grade{background:var(--info-bg);border-color:#2c4560;color:var(--info)}
svg{background:#fff;border-radius:10px}}
@media print{:root{color-scheme:light;--ink:#18222c;--muted:#5f6f7d;--line:#dfe6ec;--bg:#f2f6f4;--panel:#ffffff;--panel-soft:#f8fbf9;--accent:#1e6b49;--accent-deep:#14503a;--accent-soft:#e9f3ed;--warn:#8a6d1d;--warn-bg:#faf3dd;--danger:#a53333;--info:#34597a;--info-bg:#e8eff6;--shadow:none;--shadow-lift:none}
body{background:#fff;padding:0;font-size:11pt;line-height:1.4}
h2{page-break-after:avoid}tr,figure{page-break-inside:avoid}thead{display:table-header-group}
table,.card{box-shadow:none}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
@media (max-width:768px){body{padding:18px 12px 40px}.card{min-width:220px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def build_material_dashboard_html(result):
    """再生材料主线仪表盘（单文件离线，无任何外部资源）。"""
    demand = result["demand"]
    title = f"再生{demand.target_material_code}绿色决策仪表盘"
    parts = [
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
        f"<link rel='icon' href='{FAVICON}'>",
        "<meta name='color-scheme' content='light'>",
        f"<title>{escape(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{escape(title)}</h1>",
        f"<div class='meta'>生成日期：{date.today():%Y-%m-%d}　　{escape(result.get('data_note', ''))}</div>",
        "<div class='note'>当前使用内置虚构演示数据：企业、批次、价格与因子均为演示值，不得用于真实决策。</div>",
        _overview(result),
        _gates(result),
        _net(result),
        _mix(result),
        _options(result),
        _charts(result),
        _n1(result),
        _passport(result),
        _evidence(result),
        "<footer>本仪表盘用于辅助理解演示供需匹配与情景净减排，不构成交易、合规或投资结论；"
        "净减排为情景核算，不是实测减排；演示数据不得用于真实决策。</footer>",
        "</body></html>",
    ]
    return "\n".join(parts)


def _charts(result):
    """图表节（v15）：Pareto 前沿散点 + N-1 服务率条形 + 净减排区间（因子带区间时）。"""
    options = result.get("options") or []
    baseline = next((o for o in options if o.get("kind") == "baseline"), None)
    pareto = (pareto_scatter_svg(pareto_points(options),
                                 baseline_cost=(baseline or {}).get("total_cost_cny"))
              if options else "")
    n1_bars = n1_service_svg([
        {"label": e["label"], "min_service": e["min_delivery_service_rate"],
         "gap_t": e["worst_case"]["gap_t"]} for e in result.get("n1_stress") or []])
    interval = interval_svg(options)
    if not (pareto or n1_bars or interval):
        return ""
    return ("<section id='board-charts'><h2>图表总览</h2>" + pareto + n1_bars
            + interval + "</section>")


def _overview(result):
    demand = result["demand"]
    rows = []
    for supply, state in zip(result["supplies"], result["match_states"]):
        label = _STATE_LABEL[state["state"]]
        price_text = f"{supply.price_cny_per_t:g}" if supply.price_cny_per_t is not None else "-"
        distance_text = f"{supply.distance_km:g}" if supply.distance_km is not None else "-"
        rows.append(
            f"<tr><td>{escape(supply.supply_id)}</td><td>{escape(supply.supplier_name)}</td>"
            f"<td>{escape(label)}</td><td>{escape(supply.material_name_raw)}</td>"
            f"<td>{price_text}</td><td>{distance_text}</td>"
            f"<td>{f'{supply.available_mass_t:g}' if supply.available_mass_t is not None else '-'}</td></tr>"
        )
    def fopt(value):
        return f"{value:g}" if value is not None else "-"

    return (
        "<section id='board-overview'><h2>1 供需概览</h2>"
        f"<div class='card'><b>需求</b>：{escape(demand.buyer_name)}，{escape(demand.target_material_code)}，"
        f"{escape(demand.application)}；{fopt(demand.required_mass_t)} t；"
        f"再生含量 ≥ {fopt(demand.min_recycled_content_pct)}%；"
        f"熔指 {fopt(demand.mfi_min)}~{fopt(demand.mfi_max)} g/10min；"
        f"基准原生 {fopt(demand.baseline_virgin_price_cny_per_t)} CNY/t；距离上限 {fopt(demand.max_distance_km)} km<br>"
        "功能单位：交付 1 吨满足需方规格的合格颗粒；各方案分配总量必须等于需求量。</div>"
        "<table><thead><tr><th>供给</th><th>供应商</th><th>状态</th><th>物料</th>"
        "<th>价格 CNY/t</th><th>距离 km</th><th>可供 t</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></section>"
    )


def _gates(result):
    sections = []
    for state in result["match_states"]:
        label = _STATE_LABEL[state["state"]]
        rows = "".join(
            f"<tr><td>{escape(g['name'])}</td>"
            f"<td>{escape(_GATE_STATUS_LABEL[g['status']])}</td>"
            f"<td>{escape(g['detail'])}</td></tr>"
            for g in state["gates"]
        )
        sections.append(
            f"<h3>{escape(state['supplier_name'])}（{escape(label)}）</h3>"
            "<table><thead><tr><th>门槛</th><th>判定</th><th>说明</th></tr></thead><tbody>"
            + rows + "</tbody></table>"
        )
    return "<section id='board-gates'><h2>2 替代性硬门槛（三态）</h2>" + "".join(sections) + "</section>"


def _net(result):
    sections = []
    for item in result["singles"]:
        net = item.get("net")
        if not net:
            continue
        supply = item["supply"]
        rows = "".join(
            f"<tr><td>{escape(t['term'])}</td><td>{t['activity_t']:.3f}</td>"
            f"<td>{escape(t['factor'].get('factor_id', ''))}</td>"
            f"<td>{float(t['factor'].get('value', 0)):g}</td><td>{escape(t['factor'].get('unit', ''))}</td>"
            f"<td>{escape(t['factor'].get('boundary', ''))}</td></tr>"
            for t in net["terms"]
        )
        sections.append(
            f"<h3>{escape(supply.supplier_name)}（{supply.distance_km:g} km）</h3>"
            "<table><thead><tr><th>项目</th><th>活动量 t</th><th>因子</th><th>值</th><th>单位</th><th>边界</th></tr></thead><tbody>"
            + rows + "</tbody></table>"
            f"<p class='meta'><b>基准 {net['baseline_kgco2e']:.2f} → 项目 {net['project_kgco2e']:.2f}，"
            f"净减排 {net['net_avoided_kgco2e']:.2f} kgCO2e（{escape(net['net_sign'])}）</b>；"
            f"{escape(net['boundary_note'])}</p>"
        )
    return "<section id='board-net'><h2>3 净减排核算（逐项可手算）</h2>" + "".join(sections) + "</section>"


def _mix(result):
    mix = result.get("mix") or {}
    if mix.get("error"):
        body = f"<p>{escape(mix['error'])}</p>"
    elif mix.get("plans"):
        body = f"<p class='meta'>{escape(mix.get('note', ''))}</p>"
        for plan in mix["plans"][:10]:
            labels = plan.get("allocation_labels") or {}
            alloc = "、".join(f"{escape(labels.get(k, k))} {v:g} t" for k, v in plan["allocation"].items())
            body += (
                f"<div class='card'>方案（{escape(plan.get('option_id', ''))}）：{alloc}<br>"
                f"到厂总成本 {plan['total_cost_cny']:g} CNY；"
                f"净减排 {plan['net_avoided_kgco2e']:.2f} kgCO2e；"
                f"订单最大份额 {plan['order_max_share']:.0%}</div>"
            )
    else:
        body = "<p>无多供方组合方案。</p>"
    return f"<section id='board-mix'><h2>4 组合供料方案（非支配前沿）</h2>{body}</section>"


def _options(result):
    rows = []
    for option in result["options"]:
        net_text = f"{option['net_avoided_kgco2e']:.0f}" if option.get("net_avoided_kgco2e") is not None else "待补证据"
        cost_text = f"{option['total_cost_cny']:g}" if option.get("total_cost_cny") is not None else "缺价"
        delta_text = f"{option['cost_delta_cny']:+.0f}" if option.get("cost_delta_cny") is not None else "—"
        marginal = f"{option['marginal_abatement_cost_cny_per_tco2e']:.0f}" \
            if option.get("marginal_abatement_cost_cny_per_tco2e") is not None else "—"
        rows.append(
            f"<tr><td>{escape(option['label'])}</td><td>{escape(option['kind'])}</td>"
            f"<td>{cost_text}</td><td>{delta_text}</td><td>{net_text}</td>"
            f"<td>{marginal}</td><td>{escape(option['category'])}</td></tr>"
        )
    return (
        "<section id='board-options'><h2>5 决策选项与绿色成本</h2>"
        "<table><thead><tr><th>方案</th><th>类型</th><th>总成本 CNY</th><th>成本差 CNY</th>"
        "<th>净减排 kgCO2e</th><th>边际减排成本 CNY/tCO2e</th><th>分类</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
        "<p class='meta'>边际减排成本只在净减排 &gt; 0 时计算；该指标为当前系统边界下的情景值，"
        "不是完整生命周期减排成本。Pareto 方向：成本越小越好、净减排越大越好、订单最大份额越小越好。</p></section>"
    )


def _n1(result):
    n1 = result.get("n1_stress") or []
    lines = []
    for entry in n1:
        worst = entry["worst_case"]
        lines.append(
            f"<div class='card'><b>{escape(entry['label'])}</b>：最低交付服务率 "
            f"{entry['min_delivery_service_rate']:.0%}（{escape(worst['failed_supplier'])} 不可用时缺口 "
            f"{worst['gap_t']:g} t）；单点故障："
            f"{'、'.join(escape(x) for x in entry['single_point_failures']) or '无'}</div>"
        )
    if not lines:
        lines.append("<p>无代表组合，未做压力测试。</p>")
    sens = result.get("sensitivity")
    sens_html = ""
    if sens:
        sens_html = (
            "<section id='board-sens'><h2>7 结论稳定性核查</h2><p>"
            + "、".join(escape(s["name"]) for s in sens["scenarios"])
            + f"。</p><p class='verdict-line'><b>{escape(sens['verdict'])}</b>"
            + f"（净减排方向翻转 {sens['sign_flips']} 处；入选方案集合变化 {sens['selection_changes']} 个场景；"
            + f"{escape(sens['note'])}）</p></section>"
        )
    return ("<section id='board-n1'><h2>6 N-1 断供压力测试</h2>" + "".join(lines)
            + "</section>" + sens_html)


def _passport(result):
    lines = []
    for option in result.get("representatives") or []:
        cost = f"{option['total_cost_cny']:g}" if option.get("total_cost_cny") is not None else "-"
        delta = f"{option['cost_delta_cny']:+.0f}" if option.get("cost_delta_cny") is not None else "-"
        net = f"{option['net_avoided_kgco2e']:.0f}" if option.get("net_avoided_kgco2e") is not None else "-"
        lines.append(
            f"<div class='card'><b>{escape(option['label'])}</b>：总成本 {cost} CNY"
            f"（较基准 {delta} CNY）；净减排 {net} kgCO2e；分类：{escape(option['category'])}</div>"
        )
    lines.append(
        "<p class='meta'>下一步不是立即交易：对「待核验」供给补送样检测；与最低成本供给议价；"
        "核对基准运输距离假设与各批次实际运输路线。</p>"
    )
    return "<section id='board-passport'><h2>7 绿色匹配决策护照（代表方案）</h2>" + "".join(lines) + "</section>"


def _evidence(result):
    factor_data = result.get("factor_data") or {}
    factors = factor_data.get("factors") or {}
    rows = []
    for factor in factors.values():
        url = factor.get("source_url", "")
        source = f"<a href='{escape(url)}'>{escape(factor.get('source_name', ''))}</a>" if url \
            else escape(factor.get("source_name", ""))
        rows.append(
            f"<tr><td>{escape(factor.get('factor_id', ''))}</td><td>{escape(factor.get('stage', ''))}</td>"
            f"<td>{float(factor.get('value', 0)):g}</td><td>{escape(factor.get('unit', ''))}</td>"
            f"<td>{escape(factor.get('boundary', ''))}</td><td>{escape(factor.get('region', ''))} "
            f"{escape(str(factor.get('year', '')))}</td><td>{source}</td>"
            f"<td><span class='grade'>{escape(factor.get('grade', ''))}</span></td></tr>"
        )
    quality = result.get("quality_stats") or {}
    return (
        "<section id='board-evidence'><h2>8 证据与口径边界</h2>"
        "<h3>因子表（演示假设 / 权威分开标识）</h3>"
        "<table><thead><tr><th>编号</th><th>阶段</th><th>值</th><th>单位</th><th>边界</th>"
        "<th>地区/年份</th><th>来源</th><th>等级</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
        f"<p class='meta'>基准运输距离：{factor_data.get('baseline_transport_distance_km', '-')} km（演示假设）。</p>"
        "<h3>口径边界</h3><ul>"
        "<li>净减排为情景核算（原生颗粒生产与运输、再生颗粒生产与运输），不是完整生命周期碳足迹。</li>"
        "<li>基准情景假设：原生粒料运输距离取配置值（演示假设），改变它只影响基准侧。</li>"
        "<li>材料因子为演示假设因子（虚构值）；运输因子为英国 DEFRA 2024（TTW，非中国口径）。</li>"
        f"<li>{escape(quality.get('emission_uncertainty', ''))}</li>"
        "<li>演示企业、批次、价格全部虚构，不得用于真实决策。</li></ul></section>"
    )
