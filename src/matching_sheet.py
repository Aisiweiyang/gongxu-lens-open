"""撮合建议单渲染（v17 任务 2）：预览响应 → 独立 HTML，打印友好、零依赖。

与决策护照同一渲染模式：确定性字符串拼装、全部动态文本经 html.escape、
无外部资源（无 CDN/无图片/无脚本），可直接打印或存档转发。

口径与红线：
- 输入是 POST /api/matching/preview 的响应字典（透传，不做任何二次计算）——
  建议单与页面所见同源同值；「只读不落库」口径不变：服务端不存储本文件，
  是否存档由用户在前端下载决定；
- generated_at 由调用方显式传入；除该时间戳外，同输入产出同字节（可复算）；
- 标注齐全：撮合建议（非交易/非预留）、供给侧偏好为演示毛利模型、生成时间与
  入参快照；建议单为只读试算产物，不构成交易、预留或任何库存占用。
"""

from __future__ import annotations

from html import escape


def _esc(value):
    return escape(str(value if value is not None else ""), quote=True)


def _mass(value):
    """吨数展示：与预览 reasons 的 :g 口径一致（15.0 → "15"，41.5775 → "41.5775"）。"""
    return f"{value:g}" if isinstance(value, (int, float)) else _esc(value)


def _cost(value):
    """金额展示：与预览 reasons 的 :,.2f 口径一致（59820.0 → "59,820.00"）。"""
    return f"{value:,.2f}" if isinstance(value, (int, float)) else _esc(value)


_CSS = (
    "body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;color:#16231c;"
    "margin:24px auto;max-width:960px;padding:0 16px;}"
    "h1{font-size:20px;margin:0 0 2px;}h2{font-size:15px;margin:22px 0 6px;"
    "border-bottom:1px solid #9db3a6;padding-bottom:4px;}"
    ".meta{color:#5b6f65;font-size:12.5px;line-height:1.7;}"
    ".banner{border:1px solid #8a6d1d;background:#faf6e8;border-radius:6px;"
    "padding:8px 12px;font-size:12.5px;line-height:1.7;margin:12px 0;}"
    "table{border-collapse:collapse;width:100%;font-size:12.5px;}"
    "th,td{border:1px solid #9db3a6;padding:5px 8px;text-align:left;"
    "vertical-align:top;}"
    "th{background:#e8f1ec;white-space:nowrap;}td.num,th.num{text-align:right;}"
    ".note{color:#5b6f65;font-size:11.5px;line-height:1.6;}"
    "footer{margin-top:24px;border-top:1px solid #9db3a6;padding-top:8px;"
    "color:#5b6f65;font-size:11.5px;line-height:1.8;}"
    ".svg-wrap{overflow-x:auto;}"
    "@media print{body{margin:0;max-width:none;}.banner{background:#faf6e8;}}"
)


def render_matching_sheet(payload: dict, generated_at: str) -> str:
    """渲染撮合建议单 HTML 文档（UTF-8）。

    payload：撮合预览 API 的完整响应（ok/preview/reasons/demands/batches/svg/
    elapsed_ms/disclaimers）；generated_at：生成时间（调用方传入的本地时间字符串）。
    """
    preview = payload.get("preview") or {}
    matches = preview.get("matches") or []
    unmatched = preview.get("unmatched") or []
    leftovers = preview.get("leftover_capacity_t") or {}
    stability = preview.get("stability") or {}
    reasons = payload.get("reasons") or {}
    demands = payload.get("demands") or []
    batches = payload.get("batches") or []
    disclaimers = payload.get("disclaimers") or []
    demand_by_id = {d.get("task_id"): d for d in demands}

    # —— 头部：生成时间与入参快照 ——
    task_ids = [d.get("task_id") for d in demands]
    snapshot = (
        f"勾选任务 {'、'.join(_esc(t) for t in task_ids) if task_ids else '（无）'}"
        f"　·　参与需求 {len(demands)}　·　匹配 {len(matches)}　·　未满足 {len(unmatched)}"
        f"　·　批次 {len(batches)}"
        + (f"　·　试算耗时 {payload.get('elapsed_ms')} ms"
           if payload.get("elapsed_ms") is not None else ""))

    parts = [
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<meta name=\"color-scheme\" content=\"light\">",
        "<title>撮合建议单（演示口径，非交易/非预留）</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>供需透镜 · 撮合建议单</h1>",
        "<p class='meta'>生成时间：" + _esc(generated_at)
        + "　·　试算口径：只读预览（即算即返、不落库、不产生预留或占用）</p>",
        f"<p class='meta'>入参快照：{snapshot}</p>",
        "<div class='banner'>" + "；".join(_esc(x) for x in disclaimers)
        + "；本建议单为只读试算产物，打印或转发请连同本标注一并保留。</div>",
    ]

    # —— 一、匹配明细 ——
    parts.append("<h2>一、匹配明细</h2>")
    if matches:
        rows = []
        for m in matches:
            dm = demand_by_id.get(m.get("demand_id")) or {}
            alloc = "；".join(
                f"{_esc(bid)} × {_mass(mass)}"
                for bid, mass in sorted((m.get("allocations") or {}).items()))
            rows.append(
                "<tr><td>" + _esc(m.get("demand_id"))
                + (f"（{_esc(dm.get('buyer_name'))}）" if dm.get("buyer_name") else "")
                + f"</td><td class='num'>{_mass(dm.get('required_mass_t'))}</td>"
                f"<td class='num'>{_mass(m.get('satisfied_t'))}</td>"
                f"<td class='num'>{_mass(m.get('remaining_t'))}</td>"
                f"<td>{alloc or '—'}</td>"
                f"<td class='num'>{_cost(m.get('proposed_cost_cny'))}</td>"
                f"<td>{_esc(reasons.get(m.get('demand_id')) or '')}</td></tr>")
        parts.append(
            "<table><thead><tr><th>需求任务</th><th class='num'>需求量 t</th>"
            "<th class='num'>满足 t</th><th class='num'>未满足 t</th>"
            "<th>分配明细（批次 × t）</th><th class='num'>建议到厂成本 CNY</th>"
            "<th>撮合理由</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
        parts.append(
            "<p class='note'>建议成本为演示模型核算口径（材料+运费+装卸+检测+损耗），"
            "非报价；与预览接口数值同源。</p>")
    else:
        parts.append("<p class='note'>本轮无匹配（见下节原因）。</p>")

    # —— 二、未满足需求与原因 ——
    parts.append("<h2>二、未满足需求与原因</h2>")
    if unmatched:
        rows = "".join(
            f"<tr><td>{_esc(u.get('demand_id'))}</td>"
            f"<td class='num'>{_mass(u.get('remaining_t'))}</td>"
            f"<td>{_esc(u.get('reason'))}</td></tr>"
            for u in unmatched)
        parts.append(
            "<table><thead><tr><th>需求任务</th><th class='num'>未满足量 t</th>"
            f"<th>原因</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        parts.append("<p class='note'>本轮所有参与需求均已获得完整分配。</p>")

    # —— 三、批次剩余容量 ——
    parts.append("<h2>三、批次剩余容量（本轮撮合后，未占用）</h2>")
    if leftovers:
        rows = "".join(
            f"<tr><td>{_esc(bid)}</td><td class='num'>{_mass(leftovers[bid])}</td></tr>"
            for bid in sorted(leftovers))
        parts.append(
            "<table><thead><tr><th>批次</th><th class='num'>剩余容量 t</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
            "<p class='note'>剩余容量为撮合试算的静态余量展示，不构成任何预留或占用。</p>")
    else:
        parts.append("<p class='note'>本轮所有参与批次容量已分满（或无可展示余量）。</p>")

    # —— 四、双边关系视图 ——
    parts.append("<h2>四、双边关系视图</h2><div class='svg-wrap'>"
                 + (payload.get("svg") or "") + "</div>")

    # —— 页脚：算法与稳定性 + 标注 ——
    parts.append(
        "<footer>算法与稳定性：" + _esc(stability.get("algorithm") or "—")
        + f"；稳定性自检阻断对 {_esc(stability.get('blocking_pairs'))}"
        + "（非零即实现缺陷，请勿采用）。"
        + _esc(preview.get("note") or "")
        + "<br>本建议单由供需透镜试点系统按当次输入即时计算生成（只读不落库）；"
        "演示数据一律标注「演示」；净减排与成本为情景核算口径，非实测、非报价；"
        "系统只支持决策，不替代送样检测、交易撮合和固废监管。</footer>")
    parts.append("</body></html>")
    return "".join(parts)
