"""零依赖内联 SVG 可视化（v15 任务 4）：站点与仪表盘共用。

- 全部为纯函数：输入统一结果对象/方案列表，输出确定性 SVG 字符串（同输入同字节）；
- 无外部资源、无图表库（与「无 CDN、自研无构建」口径一致）；
- 每张图伴随等价数据表格（可访问性 + 打印 + 图表被禁用时不丢信息）；
- 数值一律来自既有计算结果的展示层舍入值，图表层不做二次舍入；
- 冻结口径：demo 数值的格式化输出逐字符不变（图为新增元素，不改既有文本）。
"""

from __future__ import annotations

from html import escape

# 方案四类分类的固定配色（打印友好、色弱可辨的明度差）
CATEGORY_COLORS = {
    "成本与碳双降": "#1e6b49",
    "绿色溢价": "#8a6d1d",
    "省钱但增排": "#a05a2c",
    "成本与碳双升": "#8b2f2f",
}
_GREY = "#b9ccc2"


def _fmt(value):
    return f"{value:,.0f}"


def _est_text_width(text, font_size=11.5):
    """估算 SVG 文本渲染宽度：CJK/全角按 1.0em、其余按 0.62em（无测量 API 的确定性估计）。"""
    wide = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return (wide + (len(text) - wide) * 0.62) * font_size


def _fit_label(label, slot_px, font_size=11.5):
    """标签超槽位时截断加省略号，返回（显示文本, 完整文本）；完整文本供 <title> 悬停。"""
    if _est_text_width(label, font_size) <= slot_px:
        return label, label
    shown = label
    while shown and _est_text_width(shown + "…", font_size) > slot_px:
        shown = shown[:-1]
    return shown + "…", label


def pareto_scatter_svg(points, baseline_cost=None):
    """Pareto 前沿散点图：x=到厂总成本，y=保守净减排，
    气泡半径=订单最大份额，颜色=方案四类分类；虚线折线=非支配前沿；
    可选基准成本参考线。points 由 scenarios.pareto_points 生成（含 share/category）。"""
    if len(points) < 2:
        return "<p class='verdict-line'>可绘制方案不足两个（缺价/缺证据方案不进图）。</p>"
    costs = [p["cost"] for p in points]
    if baseline_cost is not None:
        costs.append(baseline_cost)
    nets = [p["net"] for p in points]
    minc, maxc = min(costs), max(costs)
    minn, maxn = min(nets), max(nets)
    span_c = maxc - minc or 1.0
    span_n = maxn - minn or 1.0
    pad = 0.06
    W, H, L, R, T, B = 660, 340, 76, 18, 18, 46

    def px(cost):
        return L + (cost - minc) / span_c * (1 - 2 * pad) * (W - L - R) + pad * (W - L - R)

    def py(net):
        return T + (maxn - net) / span_n * (1 - 2 * pad) * (H - T - B) + pad * (H - T - B)

    parts = [f"<svg id='pareto-svg' viewBox='0 0 {W} {H}' role='img' "
             "aria-label='Pareto 前沿图：总成本与保守净减排，气泡大小=订单最大份额'>"]
    parts.append(f"<line x1='{L}' y1='{H - B}' x2='{W - R}' y2='{H - B}' stroke='#9db3a6'/>")
    parts.append(f"<line x1='{L}' y1='{T}' x2='{L}' y2='{H - B}' stroke='#9db3a6'/>")
    for i in range(5):
        cx = minc + span_c * i / 4
        ny = maxn - span_n * i / 4
        parts.append(f"<line x1='{px(cx):.1f}' y1='{H - B}' x2='{px(cx):.1f}' y2='{H - B + 4}' stroke='#9db3a6'/>"
                     f"<text x='{px(cx):.1f}' y='{H - B + 18}' font-size='11' fill='#5b6f65' "
                     f"text-anchor='middle'>{cx:,.0f}</text>")
        parts.append(f"<line x1='{L - 4}' y1='{py(ny):.1f}' x2='{L}' y2='{py(ny):.1f}' stroke='#9db3a6'/>"
                     f"<text x='{L - 8}' y='{py(ny) + 4:.1f}' font-size='11' fill='#5b6f65' "
                     f"text-anchor='end'>{ny:,.0f}</text>")
    parts.append("<text x='14' y='16' font-size='11' fill='#5b6f65'>净减排 kgCO2e</text>")
    parts.append(f"<text x='{W - R}' y='{H - 8}' font-size='11' fill='#5b6f65' text-anchor='end'>总成本 CNY</text>")
    if baseline_cost is not None and minc <= baseline_cost <= maxc:
        bx = px(baseline_cost)
        parts.append(f"<line x1='{bx:.1f}' y1='{T}' x2='{bx:.1f}' y2='{H - B}' "
                     "stroke='#8a6d1d' stroke-width='1.5' stroke-dasharray='4,3'/>")
        parts.append(f"<text x='{bx:.1f}' y='{T + 10}' font-size='10.5' fill='#8a6d1d' "
                     f"text-anchor='middle'>基准 {_fmt(baseline_cost)}</text>")
    front_sorted = sorted([p for p in points if p["on_frontier"]], key=lambda p: p["cost"])
    if len(front_sorted) > 1:
        poly = " ".join(f"{px(p['cost']):.1f},{py(p['net']):.1f}" for p in front_sorted)
        parts.append(f"<polyline points='{poly}' fill='none' stroke='var(--accent)' "
                     "stroke-width='2.5' stroke-dasharray='5,4'/>")
    for p in points:
        share = p.get("share")
        radius = (4 + max(0.0, min(1.0, share or 0.0)) * 5) if share is not None else 6.0
        color = CATEGORY_COLORS.get(p.get("category"), "var(--accent)")
        if not p["on_frontier"]:
            parts.append(f"<circle cx='{px(p['cost']):.1f}' cy='{py(p['net']):.1f}' r='{radius:.1f}' "
                         f"fill='{_GREY}' fill-opacity='0.55' data-option-id='{escape(p['option_id'])}' "
                         f"data-category='{escape(p.get('category') or '')}'>"
                         f"<title>{escape(p['label'])}：成本 {_fmt(p['cost'])}，"
                         f"净减排 {_fmt(p['net'])}"
                         + (f"，份额 {p['share']:.0%}" if p.get('share') is not None else "")
                         + "</title></circle>")
            continue
        parts.append(f"<circle cx='{px(p['cost']):.1f}' cy='{py(p['net']):.1f}' r='{radius:.1f}' "
                     f"fill='{color}' stroke='#16231c' stroke-width='0.6' "
                     f"data-option-id='{escape(p['option_id'])}' "
                     f"data-category='{escape(p.get('category') or '')}'>"
                     f"<title>{escape(p['label'])}：成本 {_fmt(p['cost'])}，"
                     f"净减排 {_fmt(p['net'])}"
                     + (f"，份额 {p['share']:.0%}" if p.get('share') is not None else "")
                     + "</title></circle>")
    parts.append("</svg>")
    legend = "".join(
        f"<span class='tag' style='border-color:{color}'><span style='color:{color}'>●</span> "
        f"{escape(name)}</span>" for name, color in CATEGORY_COLORS.items())
    note = ("<p class='verdict-line'>气泡大小=订单最大份额；颜色=方案分类；"
            "灰点=非支配前沿之外（填充半透明）；虚线折线=非支配前沿；"
            "悬停或键盘聚焦查看方案明细；二维投影不代表三维完整前沿。</p>")
    return (f"<div class='pareto-legend'>{legend}</div>" + "".join(parts) + note)


def n1_service_svg(n1_rows):
    """N-1 断供压力测试：各代表方案最差交付服务率的水平条形图。

    n1_rows: [{"label", "min_service"(0~1), "gap_t"}]（来自 result["n1_stress"]）。"""
    rows = [r for r in n1_rows if r.get("min_service") is not None]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda r: r["min_service"])
    # 标签槽按最长标签动态加宽（L 与 viewBox 同步扩，条形区宽度不变）；
    # 超出上限时截断加省略号，<title> 保留全名。
    est = max(_est_text_width(str(r["label"])) for r in rows)
    extra = max(0, min(240, est + 26 - 230))
    W, L, R, row_h, top = 660 + extra, 230 + extra, 70, 26, 30
    H = top + row_h * len(rows) + 20

    def px(rate):
        return L + max(0.0, min(1.0, rate)) * (W - L - R)

    parts = [f"<svg id='n1-service-svg' viewBox='0 0 {W:g} {H}' role='img' "
             "aria-label='N-1 断供压力测试：各方案最差交付服务率'>"]
    for rate, cap_x in ((0.25, 0.25), (0.5, 0.5), (0.75, 0.75), (1.0, 1.0)):
        parts.append(f"<line x1='{px(rate):.1f}' y1='{top - 8}' x2='{px(rate):.1f}' "
                     f"y2='{H - 16}' stroke='#9db3a6' stroke-dasharray='2,4'/>"
                     f"<text x='{px(rate):.1f}' y='{top - 12}' font-size='10.5' fill='#5b6f65' "
                     f"text-anchor='middle'>{cap_x:.0%}</text>")
    for i, row in enumerate(rows):
        rate = max(0.0, min(1.0, row["min_service"]))
        y = top + i * row_h
        color = "#1e6b49" if rate >= 0.999 else ("#8a6d1d" if rate >= 0.9 else "#8b2f2f")
        shown, full = _fit_label(str(row["label"]), L - 12)
        tip = f"<title>{escape(full)}</title>" if shown != full else ""
        parts.append(f"<text x='{L - 10}' y='{y + 13}' font-size='11.5' fill='#16231c' "
                     f"text-anchor='end'>{escape(shown)}{tip}</text>")
        parts.append(f"<rect x='{L}' y='{y}' width='{px(rate) - L:.1f}' height='15' rx='3' "
                     f"fill='{color}'/>")
        gap = row.get("gap_t")
        suffix = (f"（缺口 {gap:g} t）" if gap else "")
        parts.append(f"<text x='{px(rate) + 6:.1f}' y='{y + 13}' font-size='11' fill='#5b6f65' "
                     f">{rate:.1%}{suffix}</text>")
    parts.append("</svg>")
    note = ("<p class='verdict-line'>条长=单家供应商失效后的最差交付服务率；"
            "绿=100% 满交付，黄=≥90%，红=存在缺口；数值与上方 N-1 表格逐项一致。</p>")
    return "".join(parts) + note


def interval_svg(options):
    """净减排区间条（v14 数据就绪时渲染）：区间线 + 点估计圆点。

    options 中不带 net_interval_kgco2e 的方案跳过；全部不带则返回 ""（demo 口径）。"""
    rows = [{"label": o["label"], "lo": o["net_interval_kgco2e"][0],
             "hi": o["net_interval_kgco2e"][1],
             "point": o.get("net_avoided_kgco2e"),
             "oid": o["option_id"]}
            for o in options if o.get("net_interval_kgco2e")]
    if not rows:
        return ""
    los = [r["lo"] for r in rows]
    his = [r["hi"] for r in rows]
    minv, maxv = min(los), max(his)
    span = maxv - minv or 1.0
    # 标签槽按最长标签动态加宽；超上限时截断加省略号，<title> 保留全名。
    est = max(_est_text_width(str(r["label"])) for r in rows)
    extra = max(0, min(240, est + 26 - 250))
    W, L, R, row_h, top = 660 + extra, 250 + extra, 90, 28, 30
    H = top + row_h * len(rows) + 20

    def px(val):
        return L + (val - minv) / span * (W - L - R)

    parts = [f"<svg id='interval-svg' viewBox='0 0 {W:g} {H}' role='img' "
             "aria-label='净减排区间：输入因子极值传播'>"]
    for i, row in enumerate(rows):
        y = top + i * row_h
        x_lo, x_hi = px(row["lo"]), px(row["hi"])
        shown, full = _fit_label(str(row["label"]), L - 12)
        tip = f"<title>{escape(full)}</title>" if shown != full else ""
        parts.append(f"<text x='{L - 10}' y='{y + 13}' font-size='11.5' fill='#16231c' "
                     f"text-anchor='end'>{escape(shown)}{tip}</text>")
        parts.append(f"<line x1='{x_lo:.1f}' y1='{y + 8}' x2='{x_hi:.1f}' y2='{y + 8}' "
                     "stroke='#1e6b49' stroke-width='4' stroke-linecap='round' "
                     "stroke-opacity='0.55'/>")
        point = row["point"]
        if point is not None and row["lo"] - 1e-6 <= point <= row["hi"] + 1e-6:
            parts.append(f"<circle cx='{px(point):.1f}' cy='{y + 8}' r='4' fill='#16231c'>"
                         f"<title>点估计 {_fmt(point)}</title></circle>")
        parts.append(f"<text x='{x_lo:.1f}' y='{y + 24}' font-size='10.5' fill='#5b6f65' "
                     f"text-anchor='middle'>{_fmt(row['lo'])}</text>")
        parts.append(f"<text x='{x_hi:.1f}' y='{y + 24}' font-size='10.5' fill='#5b6f65' "
                     f"text-anchor='middle'>{_fmt(row['hi'])}</text>")
    parts.append("</svg>")
    table_rows = "".join(
        f"<tr><td>{escape(r['label'])}</td><td>{_fmt(r['lo'])}</td>"
        f"<td>{_fmt(r['point']) if r['point'] is not None else '-'}</td>"
        f"<td>{_fmt(r['hi'])}</td></tr>" for r in rows)
    table = ("<div class='table-scroll'><table><thead><tr><th>方案</th><th>区间下界 kgCO2e</th>"
             "<th>点估计 kgCO2e</th><th>区间上界 kgCO2e</th></tr></thead>"
             f"<tbody>{table_rows}</tbody></table></div>")
    note = ("<p class='verdict-line'>区间为输入因子极值（value_min/value_max）的确定性传播，"
            "不是统计置信区间；黑点=点估计，与区间表逐项一致。</p>")
    return (f"<h3>净减排区间（因子极值传播）</h3>" + "".join(parts) + table + note)


def matching_svg(demands, batches, allocations):
    """多需求-批次双边关系图（v16 任务 3）：左=需求，右=批次，连线宽度=分配量。

    借鉴 OpenSupplyChains / Manifest 的「双边关系视图」设计思想（仅借思想，
    不使用其前端代码；SPEC-v16 第四节核验记录）。纯函数、确定性、无外部资源，
    与本模块其他图同一口径：
    - demands: [{"id", "title", "sub"}]（展示顺序，已按服务端确定性排序）；
    - batches: [{"id", "title", "sub"}]（内部按 id 升序稳定排列）；
    - allocations: {demand_id: {batch_id: mass_t}}（来自撮合引擎输出）。
    所有动态文本经 html.escape；无分配批次以灰色呈现（含剩余容量）。"""
    batches = sorted(batches, key=lambda b: b["id"])
    if not demands or not batches:
        return ""
    row_h, top = 52, 16
    W, L, R, node_w, node_h = 760, 14, 14, 240, 40
    H = top + max(len(demands), len(batches)) * row_h + 8
    left_x, right_x = L, W - R - node_w
    masses = [m for alloc in allocations.values() for m in alloc.values() if m > 0]
    max_mass = max(masses) if masses else 0.0

    def mass_width(m):
        return 1.2 + 8.8 * (m / max_mass if max_mass > 0 else 0.0)

    def node(x, y, title, sub, accent):
        fill = "#e8f1ec" if accent else "#eef0ee"
        stroke = "#1e6b49" if accent else "#9db3a6"
        return (f"<rect x='{x}' y='{y}' width='{node_w}' height='{node_h}' rx='6' "
                f"fill='{fill}' stroke='{stroke}'/>"
                f"<text x='{x + 10}' y='{y + 16}' font-size='11.5' fill='#16231c' "
                f"font-weight='bold'>{escape(title)}</text>"
                f"<text x='{x + 10}' y='{y + 31}' font-size='10.5' fill='#5b6f65' "
                f">{escape(sub)}</text>")

    parts = [f"<svg id='matching-svg' viewBox='0 0 {W} {H}' role='img' "
             "aria-label='多需求-批次双边关系图：左为需求，右为批次，连线宽度=分配量'>"]
    parts.append(f"<text x='{left_x}' y='{top - 2}' font-size='11' fill='#5b6f65'>需求（勾选任务）</text>")
    parts.append(f"<text x='{right_x + node_w}' y='{top - 2}' font-size='11' fill='#5b6f65' "
                 "text-anchor='end'>批次（全局池 · 实时可用量）</text>")
    left_pos = {d["id"]: top + i * row_h for i, d in enumerate(demands)}
    right_pos = {b["id"]: top + i * row_h for i, b in enumerate(batches)}
    allocated_batches = {bid for alloc in allocations.values() for bid in alloc}
    for did, alloc in allocations.items():
        if did not in left_pos:
            continue
        for bid, mass in alloc.items():
            if bid not in right_pos or mass <= 0:
                continue
            y1, y2 = left_pos[did] + node_h / 2, right_pos[bid] + node_h / 2
            x1, x2 = left_x + node_w, right_x
            mx = (x1 + x2) / 2
            parts.append(f"<path d='M {x1} {y1:.1f} C {mx:.1f} {y1:.1f}, {mx:.1f} {y2:.1f}, "
                         f"{x2} {y2:.1f}' fill='none' stroke='#1e6b49' "
                         f"stroke-width='{mass_width(mass):.1f}' stroke-opacity='0.45' "
                         f"stroke-linecap='round'>"
                         f"<title>{escape(did)} ← {escape(bid)}：{mass:g} t</title></path>")
    for d in demands:
        parts.append(node(left_x, left_pos[d["id"]], d["title"], d["sub"], True))
    for b in batches:
        parts.append(node(right_x, right_pos[b["id"]], b["title"], b["sub"],
                          b["id"] in allocated_batches))
    parts.append("</svg>")
    note = ("<p class='verdict-line'>连线宽度=分配量（悬停看数值）；绿色=有分配的批次/需求，"
            "灰色=本轮无分配批次；图为撮合建议（非交易/非预留），不改变批次库存真相。</p>")
    return "".join(parts) + note
