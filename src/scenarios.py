"""场景快照构建（评审网站与试点工作台共用，单一来源）。

同一场景（保守/乐观）的选项、N-1、Pareto、护照来自同一次计算快照，
以 option_id 对齐；切换场景后失效选择须提示并清理，禁止悄悄顶替。
"""

from __future__ import annotations

from .evidence_quality import score_all
from .material import (build_options, n1_compare_evidence, n1_compare_strategies,
                       n1_stress_test, representative_options, supply_state)
from .passport import build_passport


def pareto_points(options):
    """从选项集生成 Pareto 数据点（option_id 身份）。"""
    opts = [o for o in options if o["kind"] != "baseline"
            and o.get("total_cost_cny") is not None and o.get("net_avoided_kgco2e") is not None]
    front = nondominated_by_id(opts)
    front_ids = {o["option_id"] for o in front}
    return [{"option_id": o["option_id"], "label": o["label"],
             "cost": o["total_cost_cny"], "net": o["net_avoided_kgco2e"],
             "on_frontier": o["option_id"] in front_ids,
             "share": o.get("order_max_share"), "category": o.get("category")}
            for o in opts]


def nondominated_by_id(options):
    """展示方案范围内的非支配集（成本↓、净减排↑、订单份额↓），按 option_id 身份比较。

    有内部全精度字段（_cost_full 等）时用全精度比较，不用已展示舍入值；
    支配比较带显式容差（与 material.DOM_TOL_* 一致），避免浮点末位翻转支配关系。
    """
    from .material import DOM_TOL_COST_NET, DOM_TOL_SHARE

    out = []
    for o in options:
        dominated = False
        for other in options:
            if other is o:
                continue
            c1 = o.get("_cost_full", o.get("total_cost_cny"))
            c2 = other.get("_cost_full", other.get("total_cost_cny"))
            n1 = o.get("_net_full", o.get("net_avoided_kgco2e"))
            n2 = other.get("_net_full", other.get("net_avoided_kgco2e"))
            if c1 is None or n1 is None or c2 is None or n2 is None:
                continue
            s1 = o.get("_share_full", o.get("order_max_share"))
            s2 = other.get("_share_full", other.get("order_max_share"))
            s1 = s1 if s1 is not None else 1.0
            s2 = s2 if s2 is not None else 1.0
            if (c2 <= c1 + DOM_TOL_COST_NET and n2 >= n1 - DOM_TOL_COST_NET
                    and s2 <= s1 + DOM_TOL_SHARE
                    and (c2 < c1 - DOM_TOL_COST_NET or n2 > n1 + DOM_TOL_COST_NET
                         or s2 < s1 - DOM_TOL_SHARE)):
                dominated = True
                break
        if not dominated:
            out.append(o)
    return out


def build_scenario_payload(demand, supplies, factor_data, scenario_id,
                           include_passport=True):
    """构建单个场景（保守/乐观）的完整快照：选项、N-1、Pareto、护照。"""
    options = build_options(demand, supplies, factor_data)
    reps = representative_options(options)
    n1 = n1_stress_test(demand, supplies, factor_data, options=reps)
    states = [supply_state(demand, s) for s in supplies]
    result_like = {
        "supplies": supplies, "match_states": states, "options": options,
        "representatives": reps, "n1_stress": n1,
        "n1_compare_evidence": n1_compare_evidence(demand, supplies, factor_data),
        "n1_compare_strategies": n1_compare_strategies(demand, supplies, factor_data),
        "factor_data": factor_data, "quality": score_all(supplies, demand),
        "resolved_mode": scenario_id, "requested_mode": scenario_id,
        "fallback_reason": "", "demand": demand,
    }
    payload = {
        "options": [
            {"option_id": o["option_id"], "label": o["label"], "kind": o["kind"],
             "total_cost_cny": o.get("total_cost_cny"),
             "net_avoided_kgco2e": o.get("net_avoided_kgco2e"),
             "order_max_share": o.get("order_max_share"), "category": o["category"],
             "allocation": o.get("allocation") or {},
             "allocation_labels": o.get("allocation_labels") or {},
             "cost_ledger": o.get("cost_ledger"),
             "recycled_mass_t": o.get("recycled_mass_t"),
             "problems": o.get("problems") or []}
            for o in options
        ],
        "n1": [
            {"option_id": e.get("option_id"), "label": e["label"],
             "min_service": e["min_delivery_service_rate"],
             "worst": f"{e['worst_case']['failed_supplier']}（缺口 {e['worst_case']['gap_t']:g} t）",
             "spof": "、".join(e["single_point_failures"]) or "无"}
            for e in n1
        ],
        "pareto": pareto_points(options),
        "representatives": [
            {"option_id": o.get("option_id"), "label": o["label"],
             "total_cost_cny": o.get("total_cost_cny"),
             "net_avoided_kgco2e": o.get("net_avoided_kgco2e"),
             "category": o["category"]}
            for o in reps
        ],
    }
    if include_passport:
        payload["passport"] = build_passport(result_like, scenario_id=scenario_id)
    return payload
