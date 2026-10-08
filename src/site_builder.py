"""评委网站生成：site/index.html（单文件离线）。

交互层采用单一渲染管线（renderAll），所有动态视图
（方案表 / Pareto / N-1 / 模式标签 / 护照导出）都来自同一场景快照
（conservative 或 optimistic），以 option_id 为行与图点的唯一身份；
稳定容器（#options-body tbody）不再被整体替换；
图点悬停/键盘选择按 option_id 关联表格行，事件委托只绑定一次。
"""

from __future__ import annotations

import json
from datetime import date
from html import escape

from . import config as _config
from .evidence_quality import score_all
from .evidence_value import optimistic_supplies, plan_evidence_actions
from .material import (build_options, load_material_factors, n1_stress_test,
                       representative_options, run_material, supply_state)
from .material_match import run_recall_benchmark
from .passport import build_passport
from .scenarios import build_scenario_payload, nondominated_by_id, pareto_points
from .visuals import interval_svg, n1_service_svg, pareto_scatter_svg

import csv

FAVICON = "data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2032%2032%27%3E%3Cdefs%3E%3ClinearGradient%20id%3D%27g%27%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%271%27%20y2%3D%271%27%3E%3Cstop%20offset%3D%270%27%20stop-color%3D%27%233f9d74%27%2F%3E%3Cstop%20offset%3D%271%27%20stop-color%3D%27%23144d33%27%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Crect%20width%3D%2732%27%20height%3D%2732%27%20rx%3D%278%27%20fill%3D%27url%28%23g%29%27%2F%3E%3Ccircle%20cx%3D%2716%27%20cy%3D%2716%27%20r%3D%276.5%27%20fill%3D%27%23fff%27%2F%3E%3C%2Fsvg%3E"

_CSS = """:root{color-scheme:light;--ink:#18222c;--muted:#5f6f7d;--faint:#8b99a6;--line:#dfe6ec;--line-soft:#e9eef3;--bg:#f2f6f4;--panel:#ffffff;--panel-soft:#f8fbf9;--accent:#1e6b49;--accent-deep:#144d33;--accent-soft:#e9f3ed;--accent-softer:#f2f8f4;--warn:#8a6d1d;--warn-bg:#f8f0d9;--danger:#a53333;--shadow:0 1px 2px rgba(24,34,44,.05),0 6px 18px rgba(24,34,44,.06);--shadow-lift:0 2px 6px rgba(24,34,44,.07),0 12px 28px rgba(24,34,44,.09);--radius:14px;--radius-sm:9px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
::selection{background:var(--accent-soft)}
a{color:var(--accent-deep)}
body{margin:0;background:radial-gradient(1200px 420px at 18% -8%,rgba(30,107,73,.06),transparent 60%),radial-gradient(900px 360px at 90% -6%,rgba(52,89,122,.04),transparent 55%),var(--bg);color:var(--ink);font-family:'Segoe UI','Microsoft YaHei UI','Microsoft YaHei','PingFang SC',system-ui,sans-serif;line-height:1.68;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{max-width:1080px;margin:0 auto;padding:0 22px}
header{padding:30px 0 0}
h1{font-size:27px;margin:0;letter-spacing:.5px}
h2{font-size:20px;margin:0 0 12px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--line);padding-bottom:10px}
h2 .step{background:linear-gradient(135deg,var(--accent) 20%,#3f9d74 100%);color:#fff;border-radius:6px;font-size:13px;padding:2px 9px;letter-spacing:.5px;box-shadow:0 1px 3px rgba(20,80,58,.25)}
h3{font-size:15px;margin:18px 0 6px;color:var(--accent-deep)}
p{margin:8px 0}
section{margin-top:56px}
.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,#eef7f1 0%,#ffffff 52%,#f6fbf8 100%);border:1px solid var(--line);border-radius:18px;padding:38px 40px;margin-top:24px;box-shadow:var(--shadow-lift)}
.hero::before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(560px 220px at 88% -10%,rgba(63,157,116,.14),transparent 65%),radial-gradient(420px 200px at -4% 110%,rgba(30,107,73,.08),transparent 60%)}
.hero::after{content:"";position:absolute;inset:0;pointer-events:none;background-image:radial-gradient(rgba(20,80,58,.055) 1px,transparent 1.5px);background-size:22px 22px;border-radius:inherit;-webkit-mask-image:linear-gradient(180deg,#000 30%,transparent 92%);mask-image:linear-gradient(180deg,#000 30%,transparent 92%)}
.hero .badge{display:inline-flex;align-items:center;gap:7px;background:var(--accent-soft);color:var(--accent-deep);border:1px solid var(--line);border-radius:20px;padding:4px 14px;font-size:12px;margin-bottom:14px;letter-spacing:.4px}
.hero .badge::before{content:"";width:8px;height:8px;border-radius:50%;background:linear-gradient(135deg,var(--accent),#3f9d74);box-shadow:0 0 0 3px rgba(63,157,116,.18)}
.hero h1{font-size:34px;line-height:1.28;letter-spacing:.3px}
@supports ((-webkit-background-clip:text) or (background-clip:text)){.hero h1{background:linear-gradient(115deg,#123d29 0%,#1e6b49 48%,#3f9d74 100%);-webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-fill-color:transparent}}
.hero .sub{color:var(--muted);font-size:15px;max-width:760px;line-height:1.8}
.verdict{position:relative;border-left:6px solid var(--accent);background:var(--accent-soft);border-radius:0 14px 14px 0;padding:18px 24px;margin:22px 0;font-size:16px;box-shadow:var(--shadow)}
.verdict::before{content:"";position:absolute;left:-3px;top:26px;width:9px;height:9px;border-radius:50%;background:linear-gradient(135deg,var(--accent),#3f9d74);box-shadow:0 0 0 3px var(--bg),0 0 0 4.5px var(--accent-soft)}
.verdict b{color:var(--accent-deep)}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}
.kpi{flex:1;min-width:210px;background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:var(--radius);padding:18px 22px;box-shadow:var(--shadow);transition:transform .15s ease,box-shadow .15s ease}
.kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow-lift)}
.kpi .num{font-size:34px;font-weight:700;color:var(--accent-deep);line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .num::after{content:"";display:block;width:34px;height:3px;border-radius:2px;margin-top:7px;background:linear-gradient(90deg,var(--accent),#3f9d74);opacity:.55}
.kpi .cap{font-size:12.5px;color:var(--muted);margin-top:5px}
.big-btn{display:inline-block;background:linear-gradient(180deg,#25805a,var(--accent));color:#fff;text-decoration:none;border-radius:9px;padding:13px 30px;font-size:16px;font-weight:600;margin:8px 0;box-shadow:0 2px 10px rgba(20,80,58,.32);transition:background .15s ease,transform .15s ease,box-shadow .15s ease}
.big-btn:hover{background:linear-gradient(180deg,#2a8f64,var(--accent-deep));transform:translateY(-1px);box-shadow:0 4px 14px rgba(20,80,58,.38)}
.steps{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 4px;position:sticky;top:0;z-index:40;padding:9px 6px;margin-left:-6px;margin-right:-6px;border-radius:12px;background:var(--bg);background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:blur(9px);-webkit-backdrop-filter:blur(9px);box-shadow:0 1px 0 var(--line-soft)}
.steps a{background:var(--panel);border:1px solid var(--line);color:var(--ink);text-decoration:none;border-radius:20px;padding:5px 14px;font-size:12.5px;transition:all .12s ease}
.steps a:hover{border-color:var(--accent);color:var(--accent)}
.steps a.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.row-hl{background:#fdeeb3!important}
.speaker-note{display:none;background:var(--warn-bg);border:1px dashed var(--warn);border-radius:8px;padding:8px 14px;margin:8px 0;font-size:13px}
.demo-mode .speaker-note{display:block}
#demo-progress{position:fixed;top:0;left:0;height:3px;background:var(--accent);width:0;z-index:99;display:none}
.demo-mode #demo-progress{display:block}
#demo-bar{position:fixed;bottom:14px;right:14px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 12px;font-size:12px;color:var(--muted);box-shadow:var(--shadow);display:none;z-index:98}
.demo-mode #demo-bar{display:block}
table{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0;background:var(--panel);font-variant-numeric:tabular-nums}
.table-scroll{overflow-x:auto}
tbody tr{transition:background .1s ease}
tbody tr:nth-child(even){background:var(--panel-soft)}
tbody tr:hover{background:var(--accent-soft)}
th,td{border:1px solid var(--line-soft);padding:8px 12px;text-align:left}
th{background:linear-gradient(180deg,#f0f7f2,#e9f2ec);color:var(--accent-deep);font-weight:600;font-size:12px;letter-spacing:.4px}
.verdict-line{font-size:12.5px;color:var(--muted)}
.verdict-line b{color:var(--ink)}
.pos{color:var(--accent);font-weight:700}.neg{color:var(--danger);font-weight:700}
.bar{background:#e6efe9;border-radius:6px;height:13px;overflow:hidden;margin:4px 0}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-deep));transition:width .35s ease}
.bar.warn i{background:linear-gradient(90deg,#c9a23e,var(--warn))}
.btn{border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:6px;padding:6px 14px;margin:0 6px 6px 0;cursor:pointer;font-size:13px;transition:all .12s ease}
.btn:hover{background:var(--accent-soft)}
.btn[aria-pressed='true']{background:var(--accent);color:#fff}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
details{margin:8px 0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 16px}
details:hover{border-color:var(--accent)}
summary{cursor:pointer;font-weight:600}
.search{display:flex;gap:10px;margin:12px 0}
.search input{flex:1;max-width:420px;border:1px solid var(--line);border-radius:8px;padding:9px 14px;font-size:14px}
.search input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px 20px;margin:12px 0;box-shadow:var(--shadow);transition:box-shadow .18s ease,border-color .18s ease,transform .18s ease}
.card:hover{box-shadow:var(--shadow-lift);transform:translateY(-1px)}
.card h3{margin:0 0 8px;font-size:16px}
.tag{display:inline-block;border-radius:5px;padding:1px 9px;font-size:11.5px;margin-right:6px;border:1px solid transparent}
.tag.ok{background:#e2f0e6;color:var(--accent-deep);border-color:#cfe6d7}
.tag.wait{background:var(--warn-bg);color:var(--warn);border-color:#eee0b4}
.tag.no{background:#f7e5e5;color:var(--danger);border-color:#eed0d0}
footer{max-width:1080px;margin:0 auto;padding:16px 22px 46px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);margin-top:52px;line-height:1.8}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
.dot.ok{background:var(--accent)}.dot.wait{background:var(--warn)}.dot.no{background:var(--danger)}
.pareto-wrap{overflow-x:auto}
svg polyline{pointer-events:none}
svg circle[data-option-id]{cursor:pointer}
svg circle[data-option-id]:focus{outline:2px solid var(--warn);outline-offset:2px}
@media (max-width:640px){.hero{padding:26px 22px}.hero h1{font-size:26px}.kpi .num{font-size:28px}.verdict{padding:14px 18px;font-size:15px}}
@media (prefers-color-scheme: dark){:root{color-scheme:dark;--ink:#e4ede8;--muted:#9caf9f;--faint:#82968a;--line:#26352d;--line-soft:#1f2b24;--bg:#0e1512;--panel:#151f1a;--panel-soft:#1a2620;--accent:#2f9d6e;--accent-deep:#8fd6b0;--accent-soft:#173527;--accent-softer:#122920;--warn:#d4b04a;--warn-bg:#332a12;--danger:#e07070;--shadow:0 1px 2px rgba(0,0,0,.35),0 6px 18px rgba(0,0,0,.32);--shadow-lift:0 2px 6px rgba(0,0,0,.4),0 12px 28px rgba(0,0,0,.38)}
body{background:radial-gradient(1200px 420px at 18% -8%,rgba(47,157,110,.08),transparent 60%),radial-gradient(900px 360px at 90% -6%,rgba(52,89,122,.05),transparent 55%),var(--bg)}
.hero{background:linear-gradient(135deg,#12241b 0%,#101815 52%,#122019 100%);border-color:#25352c}
@supports ((-webkit-background-clip:text) or (background-clip:text)){.hero h1{background:linear-gradient(115deg,#c9ead7 0%,#8fd6b0 48%,#5fc493 100%);-webkit-background-clip:text;background-clip:text}}
.hero::after{background-image:radial-gradient(rgba(255,255,255,.05) 1px,transparent 1.5px)}
.steps{background:var(--bg);background:color-mix(in srgb,var(--bg) 86%,transparent);box-shadow:0 1px 0 var(--line)}
.verdict::before{box-shadow:0 0 0 3px var(--bg),0 0 0 4.5px var(--accent-soft)}
.hero::before{background:radial-gradient(560px 220px at 88% -10%,rgba(63,157,116,.16),transparent 65%),radial-gradient(420px 200px at -4% 110%,rgba(30,107,73,.12),transparent 60%)}
.hero .badge{background:var(--accent-soft);border-color:#2a4436}
.verdict{background:var(--accent-soft)}
table{background:var(--panel)}
th{background:linear-gradient(180deg,#1c2f26,#182820);color:#a9d8bf}
tbody tr:nth-child(even){background:var(--panel-soft)}
.card,.kpi,.steps a,#demo-bar{background:var(--panel)}
.bar{background:#223028}
.tag.ok{background:#173527;color:#a9d8bf;border-color:#24503a}
.tag.wait{background:#332a12;border-color:#524417}
.tag.no{background:#3a1d1d;border-color:#5c2e2e}
.row-hl{background:#3a3012!important}
.search input,.btn,details{background:var(--panel);color:var(--ink)}
svg{background:#fff;border-radius:10px}
.print-only{color:var(--muted)}}
@media (prefers-reduced-motion: reduce){html{scroll-behavior:auto}.bar i{transition:none}.kpi,.big-btn{transition:none}}
@media print{:root{color-scheme:light;--ink:#18222c;--muted:#5f6f7d;--line:#dfe6ec;--bg:#f2f6f4;--panel:#ffffff;--panel-soft:#f8fbf9;--accent:#1e6b49;--accent-deep:#144d33;--accent-soft:#e9f3ed;--warn:#8a6d1d;--warn-bg:#f8f0d9;--danger:#a53333;--shadow:none;--shadow-lift:none}
.hero h1{background:none;color:var(--ink);-webkit-text-fill-color:currentColor}
.hero::after{display:none}
.steps,.big-btn,button,a[href^='#']{display:none}
.print-only{display:block}
section{page-break-inside:avoid}
tr,figure{page-break-inside:avoid}
thead{display:table-header-group}
body{font-size:11pt;line-height:1.35;background:#fff}
svg{background:#fff;border-radius:0}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.print-only{display:none;font-size:12px;color:#666;margin:6px 0}
@media print{.steps,.search,.btn,.big-btn,.speaker-note,#demo-bar,#demo-progress{display:none}}
"""

_JS = r"""
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("data-json").textContent);
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function q(id) { return document.getElementById(id); }

  // —— 单一状态：场景快照 + 筛选 ——
  var state = { optimistic: false, filter: "all" };

  function scenario() {
    return state.optimistic ? DATA.scenarios.optimistic : DATA.scenarios.conservative;
  }

  // —— 视图 1：供给表筛选 ——
  document.querySelectorAll(".state-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.filter = btn.getAttribute("data-state");
      document.querySelectorAll(".state-btn").forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
      btn.setAttribute("aria-pressed", "true");
      renderSupplies();
    });
  });
  function renderSupplies() {
    DATA.supplies.forEach(function (s) {
      var row = q("supply-" + s.supply_id);
      if (!row) { return; }
      row.hidden = !(state.filter === "all" || s.state === state.filter);
    });
  }

  // —— 视图 2：方案表（稳定容器 #options-body，只换 tbody 内容） ——
  function optionsSnapshot() {
    var seen = {};
    return scenario().options.filter(function (o) {
      if (seen[o.option_id]) { return false; }
      seen[o.option_id] = true;
      return true;
    });
  }
  function renderOptions() {
    var body = q("options-body");
    if (!body) { return; }
    var source = optionsSnapshot();
    var baseline = source.find(function (o) { return o.kind === "baseline"; });
    var baseCost = baseline && baseline.total_cost_cny != null ? baseline.total_cost_cny : null;
    var kindName = { baseline: "基准", single: "单供", combo: "组合" };
    body.innerHTML = source.map(function (o) {
      var savingRatio = (baseCost != null && o.total_cost_cny != null && o.kind !== "baseline")
        ? Math.max(0, Math.min(1, (baseCost - o.total_cost_cny) / baseCost)) : 0;
      var barHtml = o.kind === "baseline" ? "基准" :
        "<div class='bar' style='width:110px'><i style='width:" + Math.round(savingRatio * 100)
        + "%'></i></div><span class='verdict-line'>省 " + Math.round(savingRatio * 100) + "%</span>";
      return "<tr data-option-id='" + esc(o.option_id) + "'><td>" + esc(o.label) + "</td><td>"
        + (kindName[o.kind] || esc(o.kind)) + "</td><td>"
        + (o.total_cost_cny == null ? "缺价" : o.total_cost_cny.toLocaleString()) + "</td><td>"
        + (o.net_avoided_kgco2e == null ? "待补证据" : o.net_avoided_kgco2e.toLocaleString()) + "</td><td>"
        + (o.order_max_share == null ? "-" : (o.order_max_share * 100).toFixed(0) + "%") + "</td><td>"
        + esc(o.category) + "</td><td>" + barHtml + "</td></tr>";
    }).join("");
  }
  function highlightRow(optionId, on) {
    var row = document.querySelector("#options-body tr[data-option-id='" + CSS.escape(optionId) + "']");
    if (row) { row.classList.toggle("row-hl", on); }
    return row;
  }

  // —— 视图 3：Pareto 图（按 option_id 关联，事件委托只绑定一次） ——
  function renderPareto() {
    var svg = q("pareto-svg");
    var wrap = q("pareto-wrap");
    if (!svg || !wrap) { return; }
    var pts = scenario().pareto;
    if (pts.length < 2) {
      wrap.innerHTML = "<p class='verdict-line'>当前场景可绘制的方案不足两个（缺价/缺证据方案不进图）。</p>";
      return;
    }
    var costs = pts.map(function (p) { return p.cost; });
    var nets = pts.map(function (p) { return p.net; });
    var minc = Math.min.apply(null, costs), maxc = Math.max.apply(null, costs);
    var minn = Math.min.apply(null, nets), maxn = Math.max.apply(null, nets);
    var spanC = (maxc - minc) || 1, spanN = (maxn - minn) || 1;
    var pad = 0.06, W = 660, H = 340, L = 76, R = 18, T = 18, B = 46;
    function px(c) { return L + (c - minc) / spanC * (1 - 2 * pad) * (W - L - R) + pad * (W - L - R); }
    function py(n) { return T + (maxn - n) / spanN * (1 - 2 * pad) * (H - T - B) + pad * (H - T - B); }
    var parts = ["<line x1='" + L + "' y1='" + (H - B) + "' x2='" + (W - R) + "' y2='" + (H - B) + "' stroke='#9db3a6'/>",
      "<line x1='" + L + "' y1='" + T + "' x2='" + L + "' y2='" + (H - B) + "' stroke='#9db3a6'/>"];
    for (var i = 0; i < 5; i++) {
      var cx = minc + spanC * i / 4, ny = maxn - spanN * i / 4;
      parts.push("<line x1='" + px(cx).toFixed(1) + "' y1='" + (H - B) + "' x2='" + px(cx).toFixed(1) + "' y2='" + (H - B + 4) + "' stroke='#9db3a6'/>"
        + "<text x='" + px(cx).toFixed(1) + "' y='" + (H - B + 18) + "' font-size='11' fill='#5b6f65' text-anchor='middle'>" + cx.toLocaleString(undefined, {maximumFractionDigits: 0}) + "</text>");
      parts.push("<line x1='" + (L - 4) + "' y1='" + py(ny).toFixed(1) + "' x2='" + L + "' y2='" + py(ny).toFixed(1) + "' stroke='#9db3a6'/>"
        + "<text x='" + (L - 8) + "' y='" + (py(ny) + 4).toFixed(1) + "' font-size='11' fill='#5b6f65' text-anchor='end'>" + ny.toLocaleString(undefined, {maximumFractionDigits: 0}) + "</text>");
    }
    parts.push("<text x='14' y='16' font-size='11' fill='#5b6f65'>净减排 kgCO2e</text>");
    parts.push("<text x='" + (W - R) + "' y='" + (H - 8) + "' font-size='11' fill='#5b6f65' text-anchor='end'>总成本 CNY</text>");
    var frontSorted = pts.filter(function (p) { return p.on_frontier; })
      .sort(function (a, b) { return a.cost - b.cost; });
    if (frontSorted.length > 1) {
      var poly = frontSorted.map(function (p) {
        return px(p.cost).toFixed(1) + "," + py(p.net).toFixed(1);
      }).join(" ");
      parts.push("<polyline points='" + poly + "' fill='none' stroke='var(--accent)' stroke-width='2.5' stroke-dasharray='5,4'/>");
    }
    pts.forEach(function (p) {
      var front = p.on_frontier;
      var color = front ? "var(--accent)" : "#b9ccc2";
      var radius = front ? 7 : 4.5;
      parts.push("<circle cx='" + px(p.cost).toFixed(1) + "' cy='" + py(p.net).toFixed(1) + "' r='" + radius
        + "' fill='" + color + "' data-option-id='" + esc(p.option_id) + "' tabindex='0' role='button' aria-label='"
        + esc(p.label) + "：成本 " + p.cost.toLocaleString() + "，净减排 " + p.net.toLocaleString() + "'>"
        + "<title>" + esc(p.label) + "：成本 " + p.cost.toLocaleString() + "，净减排 " + p.net.toLocaleString() + "</title></circle>");
    });
    wrap.innerHTML = "<svg id='pareto-svg' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Pareto 前沿图：总成本与净减排'>"
      + parts.join("") + "</svg>";
    bindPareto();
  }
  function bindPareto() {
    // 每个渲染批次创建全新 circle 节点：直接绑定，节点随重渲染销毁，不重复堆积监听
    var svg = q("pareto-svg");
    if (!svg) { return; }
    svg.querySelectorAll("circle[data-option-id]").forEach(function (circle) {
      circle.addEventListener("mouseenter", function () {
        highlightRow(circle.getAttribute("data-option-id"), true);
      });
      circle.addEventListener("mouseleave", function () {
        highlightRow(circle.getAttribute("data-option-id"), false);
      });
      circle.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          highlightRow(circle.getAttribute("data-option-id"), true);
          var row = highlightRow(circle.getAttribute("data-option-id"), true);
          if (row) { row.scrollIntoView({ behavior: "smooth", block: "center" }); }
        }
      });
    });
  }
  function onParetoEnter(ev) {
    var circle = ev.target && ev.target.closest ? ev.target.closest("circle[data-option-id]") : null;
    if (!circle) { return; }
    highlightRow(circle.getAttribute("data-option-id"), true);
  }
  function onParetoLeave() {
    document.querySelectorAll("#options-body tr.row-hl").forEach(function (tr) {
      tr.classList.remove("row-hl");
    });
  }

  // —— 视图 4：N-1 ——
  function renderN1() {
    var body = q("n1-body");
    if (!body) { return; }
    var rows = scenario().n1;
    body.innerHTML = rows.map(function (e) {
      return "<tr data-option-id='" + esc(e.option_id) + "'><td>" + esc(e.label) + "</td><td>"
        + (e.min_service * 100).toFixed(1) + "%</td><td>" + esc(e.worst) + "</td><td>"
        + esc(e.spof) + "</td></tr>";
    }).join("");
  }

  // —— 保守/乐观切换：一次点击只走一条渲染管线 ——
  q("toggle-mode").addEventListener("click", function () {
    state.optimistic = !state.optimistic;
    q("toggle-mode").setAttribute("aria-pressed", String(state.optimistic));
    q("mode-label").textContent = state.optimistic ? "乐观（假定待核验证据通过）" : "保守（现状证据）";
    q("mode-label").className = "tag " + (state.optimistic ? "wait" : "ok");
    renderAll();
  });

  function renderAll() {
    renderOptions();
    renderPareto();
    renderN1();
  }

  // —— 护照下载：与当前场景快照一致 ——
  q("download-passport").addEventListener("click", function () {
    var doc = state.optimistic ? DATA.passport_optimistic : DATA.passport_conservative;
    var blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "material_evidence_passport_" + (state.optimistic ? "optimistic" : "conservative") + ".json";
    a.click();
  });
  q("print-passport").addEventListener("click", function () { window.print(); });

  // —— 演示模式：←/→ 翻板块；空格自动播放；F 全屏；输入框内不拦截 ——
  var demoSections = Array.prototype.slice.call(document.querySelectorAll("section[id]"));
  var demoIdx = 0;
  var autoplay = null;
  var prog = q("demo-progress");
  var bar = q("demo-bar");
  var NOTES = {
    "s1": "痛点：不是找不到材料，是不敢替代。五道关卡一句话带过，直接看结论条。",
    "s2": "三态门槛：缺证据 ≠ 不合规，不被误杀也不默认通过。点一下「待核验」筛选。",
    "s3": "证据价值排序：补哪份证据最可能改变结论？先给邻区批次补检测报告。",
    "s4": "Pareto：三个方向显式配置；点「保守/乐观口径」按钮看整表切换（同一渲染管线）。",
    "s5": "N-1 两张分离对照：补证影响与采购策略影响分开归因，数值来自真实计算结果。",
    "s6": "可手算复核：每个数字=活动量×因子；扰动与翻转阈值如实报告。",
    "s7": "决策护照：Schema v2 可校验，下一步是补证据与议价，不是立即交易。",
    "s8": "真实企业市场背景：与虚构演示数据严格分开，来源以官方公告为准。",
    "s9": "试点闭环：一个园区、一个用途、2-4 周、八项指标，全部待启动未测——如实。",
    "s10": "商业闭环与试点入口：商业模式是待验证假设，试点从最小闭环起步，不写客户付费承诺。"
  };
  Object.keys(NOTES).forEach(function (id) {
    var sec = q(id);
    if (!sec) { return; }
    var note = document.createElement("div");
    note.className = "speaker-note";
    note.textContent = "演讲者注：" + NOTES[id];
    sec.appendChild(note);
  });
  function goTo(idx) {
    demoIdx = Math.max(0, Math.min(demoSections.length - 1, idx));
    demoSections[demoIdx].scrollIntoView({ behavior: "smooth", block: "start" });
  }
  document.addEventListener("keydown", function (e) {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA"
        || e.target.isContentEditable)) { return; }
    if (e.key === "ArrowRight") { e.preventDefault(); goTo(demoIdx + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); goTo(demoIdx - 1); }
    else if (e.key === " ") { e.preventDefault(); if (autoplay) { stopAutoplay(); } else { startAutoplay(); } }
    else if (e.key.toLowerCase() === "f") {
      try { if (!document.fullscreenElement) { document.documentElement.requestFullscreen(); } }
      catch (err) { /* 演示环境不允许全屏时静默 */ }
    }
  });
  function startAutoplay() {
    autoplay = setInterval(function () {
      goTo(demoIdx + 1);
      if (demoIdx >= demoSections.length - 1) { stopAutoplay(); }
    }, 25000);
  }
  function stopAutoplay() { clearInterval(autoplay); autoplay = null; }
  document.addEventListener("scroll", function () {
    var max = document.documentElement.scrollHeight - window.innerHeight;
    if (max > 0) { prog.style.width = (window.scrollY / max * 100) + "%"; }
  });
  var demoToggle = q("demo-toggle");
  if (demoToggle) {
    demoToggle.addEventListener("click", function () {
      var on = document.body.classList.toggle("demo-mode");
      demoToggle.setAttribute("aria-pressed", String(on));
    });
  }
  // 滚动时高亮当前步骤
  var steps = Array.prototype.slice.call(document.querySelectorAll(".steps a"));
  var sections = steps.map(function (a) { return document.querySelector(a.getAttribute("href")); });
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        steps.forEach(function (a) { a.classList.remove("active"); });
        var link = document.querySelector('.steps a[href="#' + entry.target.id + '"]');
        if (link) { link.classList.add("active"); }
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    sections.forEach(function (s) { if (s) { observer.observe(s); } });
  }
  // 企业检索
  q("company-search").addEventListener("input", function () {
    var query = (q("company-search").value || "").trim().toLowerCase();
    var hits = DATA.companies.filter(function (c) {
      return !query || c.name.toLowerCase().indexOf(query) >= 0
        || c.id.toLowerCase().indexOf(query) >= 0
        || c.material.toLowerCase().indexOf(query) >= 0;
    });
    q("company-results").innerHTML = hits.length
      ? hits.map(renderCompany).join("")
      : "<p class='verdict-line'>未找到匹配企业（可搜企业名 / 批次编号 / 物料词，如「外省」「SUP-02」「PP」）</p>";
  });
  function renderCompany(c) {
    var tag = c.state === "eligible" ? "<span class='tag ok'>可比较</span>"
      : c.state === "pending_evidence" ? "<span class='tag wait'>待核验</span>"
      : "<span class='tag no'>不符合</span>";
    var gates = (c.gates || []).map(function (g) {
      var s = g.status === "pass" ? "通过" : g.status === "fail" ? "不通过" : "未知";
      return esc(g.name) + "：" + esc(s);
    }).join("、");
    var options = (c.in_options || []).map(esc).join("；") || "未进入当前方案";
    var n1 = c.n1_role ? "<p class='verdict-line'><b>N-1 角色</b>：" + esc(c.n1_role) + "</p>" : "";
    var evidence = c.evidence ? "<p class='verdict-line'><b>证据</b>：" + esc(c.evidence) + "</p>"
      : "<p class='verdict-line'><b>证据</b>：缺失，进入待核验</p>";
    return "<div class='card'><h3>" + esc(c.name) + " <span class='verdict-line'>（" + esc(c.id) + "）</span></h3>"
      + "<p>" + tag + " " + esc(c.material) + " · 可供 " + c.mass + " t · 价格 "
      + (c.price == null ? "缺价" : c.price + " CNY/t") + " · 距离 " + (c.distance == null ? "-" : c.distance + " km") + "</p>"
      + "<p class='verdict-line'><b>门槛</b>：" + gates + "</p>"
      + evidence
      + "<p class='verdict-line'><b>参与方案</b>：" + options + "</p>"
      + n1 + "</div>";
  }

  renderSupplies();
  renderAll();
})();
"""


def _fmt(value, spec):
    return format(value, spec) if value is not None else "-"


def _load_industry_players():
    """真实市场背景库（公开报道，与演示数据严格分开）。"""
    path = _config.BASE_DIR / "benchmarks" / "industry_players.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f)]


def _nondominated_by_id(options):
    """展示方案范围内的非支配集（成本↓、净减排↑、订单份额↓），按 option_id 身份比较。

    实现已统一到 src/scenarios.nondominated_by_id（评审网站与试点工作台共用）。
    """
    return nondominated_by_id(options)


def _quality_html(result):
    """证据质量多维评分表（pedigree 思路，规则拆分为独立维度），带门槛状态与日期标记。"""
    rows = result.get("quality") or []
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{escape(r['supply_id'])}</td><td>{escape(r['supplier_name'])}</td>"
        f"<td>{r['score']:.2f}</td><td>{escape(r['grade'])}</td>"
        f"<td>{escape(r['weakest'] or '无短板')}</td>"
        f"<td>{escape('、'.join(r.get('flags') or []) or '-')}</td>"
        f"<td>{escape('、'.join(r.get('critical_missing') or []) or '-')}</td>"
        f"<td>{escape(r['gate_label'])}</td></tr>"
        for r in rows
    )
    return ("<h3>证据质量多维评分（pedigree 思路）</h3>"
            "<p class='verdict-line'>来源存在/来源类型/独立核验/附件可追溯/字段覆盖/时效/"
            "地理代表性/技术相关性各 1-5 分（1 最优），几何平均合成（启发式，不是统计置信度）；"
            "未来日期、无效日期与过期状态分开标记；关键缺证据单独列出，不被其他维度高分抵消；"
            "质量分只描述证据强弱，不替代硬门槛判定。演示来源不自动获得真实证据最高评级。</p>"
            "<div class='table-scroll'><table><thead><tr><th>批次</th><th>供应商</th><th>质量分</th>"
            "<th>等级</th><th>最短板</th><th>日期标记</th><th>关键缺证据</th><th>门槛状态</th>"
            "</tr></thead><tbody>"
            + body + "</tbody></table></div>")


def _greedy_html(result):
    """贪心基线 vs 完整枚举对照卡（结论由真实计算结果生成，）。"""
    greedy = result.get("greedy_baseline") or {}
    if not greedy.get("computable"):
        return ""
    names = {s.supply_id: s.supplier_name for s in result["supplies"]}
    alloc = " + ".join(f"{names.get(k, k)} {v:g}t" for k, v in greedy["allocation"].items())
    enum_text = (f"{greedy['enumeration_best_cost']:,.0f}"
                 if greedy.get("enumeration_best_cost") is not None else "无可比较方案")
    return (
        "<h3>贪心基线对照：按「谁便宜先买谁」采购会怎样</h3>"
        f"<div class='card'><p>贪心分配：{escape(alloc)}；到厂总成本 {greedy['total_cost_cny']:,.0f} CNY"
        f"（枚举最优 {enum_text} CNY，{'一致' if greedy['cost_match'] else '不一致'}）。</p>"
        f"<p class='verdict-line'>{escape(greedy['verdict'])}</p></div>"
    )


def _pareto_points(options):
    """从选项集生成 Pareto 数据点（option_id 身份，用于 JS 渲染与静态兜底）。"""
    return pareto_points(options)


def _static_pareto_svg(points, baseline_cost=None):
    """无 JS 兜底/初始渲染的静态 Pareto SVG（v15 升级：气泡=份额、颜色=分类、基准线）。"""
    return pareto_scatter_svg(points, baseline_cost=baseline_cost)


def _tornado_svg(result):
    """敏感性 tornado 图（内联 SVG）：最优成本方案的净减排对扰动场景的摆动值。"""
    sens = result.get("sensitivity")
    if not sens or not sens.get("anchor_label") or sens.get("anchor_base_net") is None:
        return ""
    base = sens["anchor_base_net"]
    rows = [(s["name"], s["anchor_swing"]) for s in sens["scenarios"]
            if s.get("anchor_swing") is not None]
    if not rows:
        return ""
    rows.sort(key=lambda r: -abs(r[1]))
    max_abs = max(abs(swing) for _, swing in rows) or 1.0
    lo, hi = base - max_abs * 1.25, base + max_abs * 1.25
    W, L, R, row_h, top = 660, 200, 90, 26, 34
    H = top + row_h * len(rows) + 26

    def px(val):
        return L + (val - lo) / (hi - lo) * (W - L - R)

    parts = [f"<svg id='tornado-svg' viewBox='0 0 {W} {H}' role='img' aria-label='敏感性 tornado 图'>"]
    bx = px(base)
    parts.append(f"<line x1='{bx:.1f}' y1='{top - 10}' x2='{bx:.1f}' y2='{H - 24}' "
                 "stroke='#8a6d1d' stroke-width='1.5' stroke-dasharray='4,3'/>")
    parts.append(f"<text x='{bx:.1f}' y='{top - 14}' font-size='11' fill='#8a6d1d' "
                 f"text-anchor='middle'>基准 {base:,.2f}</text>")
    for i, (name, swing) in enumerate(rows):
        y = top + i * row_h
        x1, x2 = px(base), px(base + swing)
        color = "#1e6b49" if swing >= 0 else "#c9a23e"
        parts.append(f"<text x='{L - 10}' y='{y + 12}' font-size='11.5' fill='#16231c' "
                     f"text-anchor='end'>{escape(name)}</text>")
        parts.append(f"<rect x='{min(x1, x2):.1f}' y='{y}' width='{abs(x2 - x1):.1f}' height='15' "
                     f"rx='3' fill='{color}'/>")
        val_x = x2 + (6 if swing >= 0 else -6)
        parts.append(f"<text x='{val_x:.1f}' y='{y + 12}' font-size='11' fill='#5b6f65' "
                     f"text-anchor='{('start' if swing >= 0 else 'end')}'>{swing:+,.0f}</text>")
    parts.append("</svg>")
    return (f"<h3>敏感性 tornado：最优成本方案「{escape(sens['anchor_label'])}」的净减排摆动</h3>"
            "<p class='verdict-line'>虚线=基准净减排；绿条=扰动后增加、黄条=减少；按摆动幅度降序。"
            "OAT 单因子扫描假设线性、忽略交互，适合筛查不适合定论。</p>"
            + "".join(parts))


def _hero_bars(baseline, cheapest, saving, n1_single, opt_min_service):
    """首屏对比条：全部数值来自结果对象，不做硬编码。"""
    base_cost, best_cost = baseline["total_cost_cny"], cheapest["total_cost_cny"]
    cons = n1_single["min_delivery_service_rate"]
    cost_pct = round(best_cost / base_cost * 100) if base_cost else 0
    return (
        f"<p class='verdict-line'>到厂总成本（较基准 {base_cost:,.0f} CNY）</p>"
        "<div class='bar'><i style='width:100%'></i></div>"
        f"<div class='bar'><i style='width:{cost_pct}%'></i></div>"
        f"<p class='verdict-line'>上：全部原生 {base_cost:,.0f}；下：最优方案 {best_cost:,.0f}"
        f"（省 {saving:,.0f}）</p>"
        "<p class='verdict-line'>断供交付服务率（保守证据信息集）</p>"
        f"<div class='bar warn'><i style='width:{round(cons * 100)}%'></i></div>"
        f"<div class='bar'><i style='width:{round(opt_min_service * 100)}%'></i></div>"
        f"<p class='verdict-line'>上：现状代表方案 {cons:.1%}；下：补证后 {opt_min_service:.1%}"
        "（两张对照分开归因，见第 5 步）</p>"
    )


def _sensitivity_html(result):
    sens = result.get("sensitivity")
    if not sens:
        return ""
    rows = "".join(
        f"<tr><td>{escape(s['name'])}</td>"
        f"<td>{'是' if s['sign_flips'] else '否'}</td>"
        f"<td>{'变化' if s['selection_changed'] else '不变'}</td></tr>"
        for s in sens["scenarios"]
    )
    threshold_rows = "".join(
        f"<tr><td>{escape(t['param'])}</td><td>{t['base_value']:g}</td>"
        f"<td>{t['flip_multiplier'] if t['flip_multiplier'] is not None else '区间内未翻转'}</td>"
        f"<td>{t['flip_value'] if t['flip_value'] is not None else '-'}</td>"
        f"<td>{escape(t['note'])}</td></tr>"
        for t in sens.get("thresholds") or []
    )
    joint_rows = ""
    for j in sens.get("joint") or []:
        joint_rows += (f"<tr><td>{escape(' × '.join(j['params']))}</td>"
                       f"<td>{j['cells_selection_changed']}/{len(j['grid'])} 格代表集合变化</td>"
                       f"<td>{j['total_sign_flips']} 处净减排符号翻转（含演示边界）</td></tr>")
    return (
        "<h3>结论稳定性核查（确定性扰动，非随机）</h3>"
        "<div class='table-scroll'><table><thead><tr><th>扰动场景</th><th>净减排方向翻转</th>"
        "<th>入选方案集合</th></tr></thead><tbody>" + rows + "</tbody></table></div>"
        "<h3>关键参数翻转阈值（OAT）</h3>"
        "<div class='table-scroll'><table><thead><tr><th>参数</th><th>基准值</th>"
        "<th>翻转倍数</th><th>翻转值</th><th>说明</th></tr></thead><tbody>"
        + threshold_rows + "</tbody></table></div>"
        "<h3>两参数联合情景（演示范围，非统计置信区间）</h3>"
        "<div class='table-scroll'><table><thead><tr><th>参数对</th><th>代表集合变化</th>"
        "<th>符号翻转</th></tr></thead><tbody>" + joint_rows + "</tbody></table></div>"
        f"<p class='verdict-line'><b>{escape(sens['verdict'])}</b>：{sens['sign_flips']} 处翻转、"
        f"{sens['selection_changes']} 个场景集合变化。{escape(sens['note'])}"
        "真实数据下若结论会翻转，如实报告翻转点，不加工。</p>"
    )


def _scenario_payload(demand, supplies, factor_data, scenario_id):
    """构建单个场景（保守/乐观）的完整快照：选项、N-1、Pareto、护照。

    实现已统一到 src/scenarios.build_scenario_payload（单一来源）。
    """
    return build_scenario_payload(demand, supplies, factor_data, scenario_id,
                                  include_passport=True)


def build_site_html(result=None):
    """生成 site/index.html。result 为 None 时用演示数据。"""
    result = result or run_material("demo")
    demand = result["demand"]
    factor_data = load_material_factors()
    evidence_plans = plan_evidence_actions(demand, result["supplies"], factor_data)
    benchmark = run_recall_benchmark(
        demand, result["supplies"],
        cases_path=str(_config.BASE_DIR / "benchmarks" / "material_matching_cases_v2.csv"),
    )
    optimistic = optimistic_supplies(demand, result["supplies"])
    conservative_scen = _scenario_payload(demand, result["supplies"], factor_data, "conservative")
    optimistic_scen = _scenario_payload(demand, optimistic, factor_data, "optimistic")
    opt_min_service = min((e["min_service"] for e in optimistic_scen["n1"]), default=1.0)
    players = _load_industry_players()
    player_rows = "".join(
        f"<tr><td>{escape(p['company_name'])}</td><td>{escape(p['stock_code'] or '-')}</td>"
        f"<td>{escape(p['business_recycled'])}</td><td>{escape(p['key_fact'])}</td>"
        f"<td>{escape(p['source_date'])}</td></tr>"
        for p in players
    )
    reps = result["representatives"]
    cheapest = next((o for o in reps if o.get("total_cost_cny") is not None
                     and o["kind"] != "baseline"), None)
    best_net = max((o for o in result["options"] if o.get("net_avoided_kgco2e") is not None
                    and o["kind"] != "baseline"),
                   key=lambda o: o["net_avoided_kgco2e"], default=None)
    baseline = next((o for o in reps if o["kind"] == "baseline"), None)
    n1_single = next((e for e in result["n1_stress"] if e["kind"] == "single"), None)
    saving = (baseline["total_cost_cny"] - cheapest["total_cost_cny"]
              if baseline and cheapest and baseline.get("total_cost_cny") is not None
              and cheapest.get("total_cost_cny") is not None else None)

    # 一句话结论（全部来自真实计算结果）
    verdict_parts = []
    if cheapest is not None and saving is not None:
        verdict_parts.append(f"最低成本方案「{cheapest['label']}」到厂成本 {cheapest['total_cost_cny']:,.0f} CNY"
                             f"（较全部原生省 {saving:,.0f} CNY）")
    if best_net is not None:
        verdict_parts.append(f"保守净减排 {best_net['net_avoided_kgco2e']:,.0f} kgCO2e / {demand.required_mass_t:g} t")
    if n1_single is not None:
        verdict_parts.append(f"现状代表方案断供服务率 {n1_single['min_delivery_service_rate']:.0%}"
                             f"；补证后 {opt_min_service:.0%}（两张对照分开归因）")
    verdict = "；".join(verdict_parts) + "。"

    supply_rows = []
    for supply, state in zip(result["supplies"], result["match_states"]):
        label = {"eligible": "可比较", "pending_evidence": "待核验", "ineligible": "不符合"}[state["state"]]
        price = _fmt(supply.price_cny_per_t, "g")
        distance = _fmt(supply.distance_km, "g")
        dot = {"可比较": "ok", "待核验": "wait", "不符合": "no"}[label]
        supply_rows.append(
            f"<tr id='supply-{escape(supply.supply_id)}'><td>{escape(supply.supply_id)}</td>"
            f"<td>{escape(supply.supplier_name)}</td>"
            f"<td><span class='dot {dot}'></span>{escape(label)}</td>"
            f"<td>{escape(supply.material_name_raw)}</td><td>{price}</td><td>{distance}</td>"
            f"<td>{supply.available_mass_t:g}</td></tr>"
        )
    mix_plans = (result.get("mix") or {}).get("plans") or []
    mix_rows = "".join(
        f"<tr><td>{escape(' + '.join(f'{p["allocation_labels"].get(k, k)} {v:g}t' for k, v in p['allocation'].items()))}</td>"
        f"<td>{escape(p.get('option_id', ''))}</td>"
        f"<td>{p['total_cost_cny']:,.0f}</td><td>{p['net_avoided_kgco2e']:,.0f}</td>"
        f"<td>{p['order_max_share']:.0%}</td></tr>"
        for p in mix_plans[:10]
    )
    # 选项一句话结论行（静态兜底；JS 渲染同源快照）
    option_rows = []
    for option in result["options"]:
        verdict_line = []
        if option.get("total_cost_cny") is not None and baseline and baseline.get("total_cost_cny") is not None:
            delta = option["total_cost_cny"] - baseline["total_cost_cny"]
            verdict_line.append(f"较基准 {'省' if delta < 0 else '多花'} {abs(delta):,.0f} CNY")
        if option.get("net_avoided_kgco2e") is not None:
            verdict_line.append(f"净减排 {option['net_avoided_kgco2e']:,.0f} kgCO2e")
        option_rows.append(
            f"<tr data-option-id='{escape(option['option_id'])}'><td>{escape(option['label'])}</td>"
            f"<td>{_fmt(option.get('total_cost_cny'), ',.0f')}</td>"
            f"<td>{_fmt(option.get('net_avoided_kgco2e'), ',.0f')}</td>"
            f"<td>{escape(option['category'])}</td>"
            f"<td class='verdict-line'>{escape('；'.join(verdict_line))}</td></tr>"
        )
    # 企业报告数据
    recall = {s["supply_id"]: s for s in __import__("src.material_match", fromlist=["material_match"])
              .recall_candidates(demand, result["supplies"])}
    companies = []
    for supply, state in zip(result["supplies"], result["match_states"]):
        in_options = [o["label"] for o in result["options"]
                      if o.get("allocation") and supply.supply_id in o["allocation"]]
        spof = [e["label"] for e in result["n1_stress"]
                if supply.supplier_name in e["single_point_failures"]]
        companies.append({
            "id": supply.supply_id,
            "name": supply.supplier_name,
            "state": state["state"],
            "material": supply.material_name_raw,
            "mass": supply.available_mass_t,
            "price": supply.price_cny_per_t,
            "distance": supply.distance_km,
            "gates": state["gates"],
            "evidence": supply.evidence_source,
            "in_options": in_options,
            "n1_role": "单点故障（断供即产生缺口）" if spof else
                       ("参与组合供给" if in_options else None),
        })
    payload = {
        "supplies": [{"supply_id": s.supply_id, "state": st["state"]} for s, st in
                     zip(result["supplies"], result["match_states"])],
        "scenarios": {
            "conservative": {"options": conservative_scen["options"],
                             "n1": conservative_scen["n1"],
                             "pareto": conservative_scen["pareto"]},
            "optimistic": {"options": optimistic_scen["options"],
                           "n1": optimistic_scen["n1"],
                           "pareto": optimistic_scen["pareto"]},
        },
        "companies": companies,
        "passport_conservative": conservative_scen["passport"],
        "passport_optimistic": optimistic_scen["passport"],
    }
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("<", "\\u003c")
    steps = ["供需", "分阶段匹配", "证据价值", "Pareto", "N-1", "碳对照", "护照", "企业检索", "市场背景", "试点"]
    steps_html = "".join(
        f"<a href='#s{i + 1}'>{i + 1}. {escape(s)}</a>" for i, s in enumerate(steps)
    )
    reps_rows = "".join(
        f"<tr><td>{escape(o['label'])}</td><td>{escape(o.get('option_id', ''))}</td>"
        f"<td>{_fmt(o.get('total_cost_cny'), ',.0f')}</td>"
        f"<td>{_fmt(o.get('net_avoided_kgco2e'), ',.0f')}</td><td>{escape(o['category'])}</td></tr>"
        for o in reps
    )
    evidence_rows = "".join(
        f"<tr><td>{escape(p['action_name'])}</td><td>{escape(p['supplier_name'])}</td>"
        f"<td>{escape('、'.join(p['blocked_fields']))}</td>"
        f"<td>{p['value_interval']['extra_supply_t']:g} t</td>"
        f"<td>{p['value_interval']['cost_saving_max_cny']:,.0f}</td>"
        f"<td>{p['value_interval']['net_avoided_gain_kgco2e']:,.0f}</td>"
        f"<td>{escape(str(p['fee_cny']))}</td>"
        f"<td>{escape(str(p['probability']))}</td></tr>"
        for p in evidence_plans[:8]
    )
    net_details = "".join(
        (f"<details><summary>{escape(item['supply'].supplier_name)}（{item['supply'].distance_km:g} km）"
         f"：净减排 {item['net']['net_avoided_kgco2e']:,.0f} kgCO2e</summary>"
         "<table><thead><tr><th>项目</th><th>活动量 t</th><th>因子</th><th>值</th><th>单位</th></tr></thead><tbody>"
         + "".join(
             f"<tr><td>{escape(t['term'])}</td><td>{t['activity_t']:.2f}</td>"
             f"<td>{escape(t['factor'].get('factor_id', ''))}</td>"
             f"<td>{float(t['factor'].get('value', 0)):g}</td><td>{escape(t['factor'].get('unit', ''))}</td></tr>"
             for t in item["net"]["terms"]
         ) + "</tbody></table></details>")
        for item in result["singles"] if item.get("net")
    )
    recall_dev = benchmark.get("recall", {}).get("dev", {})
    recall_holdout = benchmark.get("recall", {}).get("holdout", {})
    gate_labels = benchmark.get("gate", {}).get("by_label", {})
    gate_summary = "；".join(
        f"{label}：误放行 {stats['false_pass']}/{stats['n']}"
        for label, stats in sorted(gate_labels.items())
    )
    n1_compare_note = ""
    strategies = result.get("n1_compare_strategies") or {}
    if strategies.get("results"):
        rates = sorted({round(e["min_delivery_service_rate"], 4) for e in strategies["results"]})
        n1_compare_note = ("保守信息集下各代表策略的断供服务率为 "
                           + "、".join(f"{r:.1%}" for r in rates)
                           + "：差异来自分配策略本身。补证带来的变化另计："
                           + (f"固定方案补证前后服务率变化 "
                              + f"{result['n1_compare_evidence']['delta_service_rate']:+.1%}。"
                              if result.get("n1_compare_evidence", {}).get("delta_service_rate") is not None
                              else "补证对照未计算。"))
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=100%,initial-scale=1'>"
        f"<link rel='icon' href='{FAVICON}'>"
        "<meta name='color-scheme' content='light dark'>"
        "<meta name='description' content='面向工业园区与制造企业的再生材料供需匹配与净减排决策系统：完整组合前沿、逐项可手算的净减排账本、一页决策护照。离线确定性，数据不出本机。'>"
        "<meta property='og:type' content='website'>"
        "<meta property='og:title' content='供需透镜：再生材料供需匹配与净减排决策'>"
        "<meta property='og:description' content='面向工业园区与制造企业的再生材料供需匹配与净减排决策系统：完整组合前沿、逐项可手算的净减排账本、一页决策护照。离线确定性，数据不出本机。'>"
        f"<title>供需透镜：再生材料供需匹配与净减排决策</title><style>{_CSS}</style></head><body>"
        "<div id='demo-progress'></div>"
        "<div id='demo-bar'>演示模式：←/→ 翻页 · 空格 自动播放 · F 全屏</div>"
        "<header class='wrap'><h1>供需透镜</h1>"
        "<p class='verdict-line'>面向工业园区与制造企业的再生材料供需匹配和净减排决策系统</p></header>"
        "<main class='wrap'>"
        "<section class='hero'>"
        "<span class='badge'>演示数据</span>"
        "<h1>不是找不到材料，是不敢替代。</h1>"
        "<p class='sub'>名称相似的再生批次，规格、证据与供货能力可能完全不同；验证成本前置，"
        "还不知道先检测谁最可能改变采购结论。供需透镜把「能不能替、怎么组合、净减排是否成立、"
        "还缺什么证据、先补哪个最值」放进同一条可审计链路。</p>"
        f"<div class='print-only'>生成日期：{date.today():%Y-%m-%d}；数据模式：{result.get('data_mode', 'demo')}；"
        "净减排为情景核算，非实测或认证数据，不用于合规或外部披露。因子版本与来源见碳对照节。</div>"
        f"<div class='verdict'><b>本次决策结论</b>：{escape(verdict)}</div>"
        "<div class='kpis'>"
        f"<div class='kpi'><div class='num'>{demand.required_mass_t:g} t</div>"
        "<div class='cap'>本次替代需求：再生 PP 替代原生 PP（非食品接触周转箱）</div></div>"
        # v13：超限路径（求解器/贪心回退）feasible_count 为 None，如实渲染「—」，
        # 口径说明随 is_complete 切换（演示/常规案例输出与此前逐字节一致）
        f"<div class='kpi'><div class='num'>"
        f"{'—' if result['mix'].get('feasible_count') is None else result['mix']['feasible_count']}</div>"
        "<div class='cap'>个可行组合方案（"
        f"{'当前离散网格内完整枚举' if result['mix'].get('is_complete') else '组合超限：求解器/回退路径，不提供完整枚举计数'}）</div></div>"
        f"<div class='kpi'><div class='num'>{len(result['supplies'])}</div>"
        f"<div class='cap'>个供给批次："
        f"{sum(1 for st in result['match_states'] if st['state'] == 'pending_evidence')} 待核验 / "
        f"{sum(1 for st in result['match_states'] if st['state'] == 'ineligible')} 硬门槛淘汰 / "
        f"{sum(1 for st in result['match_states'] if st['state'] == 'eligible')} 可比较</div></div>"
        "</div>"
        "<h3>关键取舍一眼看懂</h3>"
        "<div style='max-width:560px'>"
        + (_hero_bars(baseline, cheapest, saving, n1_single, opt_min_service)
           if baseline and cheapest and baseline.get("total_cost_cny") is not None
           and cheapest.get("total_cost_cny") is not None and n1_single is not None
           else "<p class='verdict-line'>当前数据不足以生成对比条（缺价格或未做压力测试）。</p>")
        + "</div>"
        "<a class='big-btn' href='#s1'>开始演示</a>"
        + "</section>"
        f"<div class='steps'>{steps_html}</div>"
        + "<div style='margin:6px 0'><button id='demo-toggle' class='btn' aria-pressed='false'>"
        "演示模式（演讲者注释）</button></div>"
        f"<section id='s1'><h2><span class='step'>1</span>一条需求 × 四个供给批次</h2>"
        "<p>功能单位：交付 1 吨满足需方规格的合格颗粒。需求："
        f"{escape(demand.buyer_name)}，{escape(demand.target_material_code)}，{escape(demand.application)}；"
        f"再生含量 ≥ {demand.min_recycled_content_pct:g}%；熔指 {demand.mfi_min:g}~{demand.mfi_max:g} g/10min；"
        f"基准原生 {demand.baseline_virgin_price_cny_per_t:g} CNY/t。</p>"
        "<div><button class='btn state-btn' data-state='all' aria-pressed='true'>全部</button>"
        "<button class='btn state-btn' data-state='eligible' aria-pressed='false'>可比较</button>"
        "<button class='btn state-btn' data-state='pending_evidence' aria-pressed='false'>待核验</button>"
        "<button class='btn state-btn' data-state='ineligible' aria-pressed='false'>不符合</button></div>"
        "<div class='table-scroll'><table><thead><tr><th>批次</th><th>供应商</th><th>状态</th><th>物料</th>"
        "<th>价格 CNY/t</th><th>距离 km</th><th>可供 t</th></tr></thead><tbody>"
        + "".join(supply_rows) + "</tbody></table></div></section>"
        f"<section id='s2'><h2><span class='step'>2</span>分阶段匹配：召回 → 硬规格 → 证据</h2>"
        "<p>文本相似只负责扩大召回（同义词/字符 n-gram），准入由硬规格决定："
        "熔指超范围 → 淘汰；数值无检测报告支撑 → 待核验。召回基准与硬门槛误放行分开评测。</p>"
        f"<p class='verdict'><b>召回基准（固定候选池，Top-k 排名）</b>：开发集 Recall@1 = "
        f"{recall_dev.get('recall_at_1')}（{recall_dev.get('n_cases')} 例），保留集 Recall@1 = "
        f"{recall_holdout.get('recall_at_1')}（{recall_holdout.get('n_cases')} 例）；"
        f"硬门槛误放行按标签统计：{escape(gate_summary or '无门滥用例')}；"
        f"稳定性 {'通过' if benchmark.get('stable') else '未通过'}。"
        f"{escape(benchmark.get('benchmark', ''))}。</p></section>"
        f"<section id='s3'><h2><span class='step'>3</span>证据价值排序：下一笔检测先花在哪里</h2>"
        "<p>每个动作只补齐它覆盖的阻断字段；保守/有利双情景反事实；"
        "通过概率未知时不输出 EVSI/精确 ROI，只给情景值上限与条件。</p>"
        "<div class='table-scroll'><table><thead><tr><th>动作</th><th>批次</th><th>阻断字段</th>"
        "<th>可释放供给 t</th><th>成本节约上限 CNY</th><th>净减排增量 kgCO2e</th><th>费用</th>"
        "<th>概率口径</th></tr></thead><tbody>"
        + evidence_rows + "</tbody></table></div>"
        + _quality_html(result)
        + (f"<p class='verdict-line'>排序结论：<b>先给 {escape(evidence_plans[0]['supplier_name'])}"
           f" 补「{escape(evidence_plans[0]['action_name'])}」</b>——"
           f"{escape(evidence_plans[0]['ranking_reason'])}。</p>"
           if evidence_plans
           else "<p class='verdict-line'>排序结论：当前无待补证批次，无证据动作可排。</p>")
        + "</section>"
        f"<section id='s4'><h2><span class='step'>4</span>成本 × 保守净减排 × 供应集中度</h2>"
        "<p>Pareto 方向：到厂成本越小越好、净减排越大越好、订单最大份额越小越好；缺价不进成本维度。"
        "表与图以 option_id 对齐（同名称不同分配是不同方案）。</p>"
        "<div class='table-scroll'><table id='options-table'><thead><tr><th>方案</th><th>类型</th>"
        "<th>到厂总成本 CNY</th><th>净减排 kgCO2e</th><th>订单最大份额</th><th>分类</th>"
        "<th>较基准</th></tr></thead><tbody id='options-body'>"
        + "".join(option_rows) + "</tbody></table></div>"
        "<div id='pareto-wrap' class='pareto-wrap'>"
        + _static_pareto_svg(conservative_scen["pareto"],
                             baseline_cost=(baseline or {}).get("total_cost_cny"))
        + "</div>"
        + interval_svg(result.get("options") or [])
        + "<p class='verdict-line'>灰点=全部可比较方案；绿点连线=非支配前沿"
        "（成本↓、净减排↑、订单份额↓）；图点悬停或键盘聚焦高亮对应表格行；二维投影不代表三维完整前沿。</p>"
        + _greedy_html(result)
        + "<div style='margin:6px 0'><button id='toggle-mode' class='btn' aria-pressed='false'>"
        "保守/乐观口径</button><span id='mode-label' class='tag ok'>保守（现状证据）</span></div></section>"
        f"<section id='s5'><h2><span class='step'>5</span>代表组合与 N-1 断供压力测试（两张分离对照）</h2>"
        "<h3>非支配组合方案（前 10，完整前沿以 option_id 对齐）</h3>"
        "<div class='table-scroll'><table><thead><tr><th>方案</th><th>方案ID</th><th>到厂总成本 CNY</th>"
        "<th>净减排 kgCO2e</th><th>订单最大份额</th></tr></thead>"
        f"<tbody>{mix_rows}</tbody></table></div>"
        "<h3>N-1 压力测试（失效实体=供应商，名下批次同时失效）</h3>"
        "<div class='table-scroll'><table><thead><tr><th>方案</th><th>最低交付服务率</th><th>最坏情况</th>"
        "<th>单点故障</th></tr></thead>"
        "<tbody id='n1-body'></tbody></table></div>"
        + n1_service_svg([{"label": e["label"],
                           "min_service": e["min_delivery_service_rate"],
                           "gap_t": e["worst_case"]["gap_t"]}
                          for e in result.get("n1_stress") or []])
        + f"<p class='verdict-line'><b>归因结论</b>：{escape(n1_compare_note)}"
        "N-1 模型约束未知项（切换时间/切换费用）按产能上限口径标注，不冒充交付保证；"
        "只测单家供应商失效，未启用共同失效模型。</p></section>"
        f"<section id='s6'><h2><span class='step'>6</span>可展开、可手算的碳对照</h2>"
        "<p>E_baseline = Q × 原生PP生产因子 + Q × 基准运输距离 × 基准运输因子；"
        "E_project = Q × 再生PP颗粒生产因子 + Σ(分配量 × 各候选距离 × 各候选运输因子)。"
        "因子按角色显式解析；原生与再生边界兼容性按核算约定判定（cut-off，交付颗粒口径）。</p>"
        + net_details + _sensitivity_html(result) + _tornado_svg(result) + "</section>"
        f"<section id='s7'><h2><span class='step'>7</span>决策护照与下一步</h2>"
        "<div class='table-scroll'><table><thead><tr><th>代表方案</th><th>方案ID</th><th>到厂总成本 CNY</th>"
        "<th>净减排 kgCO2e</th><th>分类</th></tr></thead>"
        f"<tbody>{reps_rows}</tbody></table></div>"
        "<p class='verdict-line'>下一步不是立即交易：对「待核验」批次补送样检测；与最低成本供给议价；"
        "核对基准运输距离假设与各批次实际运输路线。下载护照与当前口径（保守/乐观）一致，"
        "Schema v2 可校验。</p>"
        "<div><button id='download-passport' class='btn'>下载 JSON 证据护照（当前口径）</button>"
        "<button id='print-passport' class='btn'>打印当前决策护照</button></div></section>"
        f"<section id='s8'><h2><span class='step'>8</span>企业报告检索</h2>"
        "<p>搜索企业名 / 批次编号 / 物料词，查看该企业的匹配状态、门槛判定、参与方案与 N-1 角色。</p>"
        "<div class='search'><input id='company-search' type='text' placeholder='例如：外省 / SUP-02 / PP' "
        "aria-label='企业检索'></div>"
        "<div id='company-results'></div></section>"
        f"<section id='s9'><h2><span class='step'>9</span>公开市场背景（真实企业，与演示数据分开）</h2>"
        "<p>以下为公开报道中的行业参与者，仅作市场背景与候选线索，<b>不是</b>本演示的供给批次；"
        "演示批次全部为虚构。来源以官方公告为准，未逐一核实。</p>"
        "<table><thead><tr><th>企业</th><th>代码</th><th>再生材料业务</th><th>关键事实</th><th>来源日期</th></tr></thead><tbody>"
        + player_rows + "</tbody></table></section>"
        f"<section id='s10'><h2><span class='step'>10</span>商业闭环与 2–4 周试点入口</h2>"
        "<p>一个园区、一个具体用途、一个用料企业、3–10 个候选批次起步：上传供需 → 找到候选 → "
        "识别阻断证据 → 决定先检测/询价谁 → 形成组合 → 输出决策护照 → 记录送样/履约反馈。</p>"
        "<p>商业模式为待验证假设（园区年度服务费 + 试点实施服务），未经验证不写客户付费承诺。</p></section>"
        "</main><footer class='wrap'>本页面为演示：企业、批次、价格与因子均为演示值，不得用于真实决策；"
        "净减排为情景核算，不是实测减排；不构成交易、合规或投资结论。"
        f"生成日期：{date.today():%Y-%m-%d}。</footer>"
        f"<script type='application/json' id='data-json'>{embedded}</script><script>{_JS}</script>"
        "</body></html>"
    )
