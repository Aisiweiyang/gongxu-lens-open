"""证据价值排序：对「待核验」批次的每个可验证动作，构造保守/有利双情景反事实。

行为约束：
- 单个证据动作只补齐**该动作覆盖的阻断字段**；有利情景其余字段保持原缺失，
  不得一次动作补齐所有未知字段（旧实现把其他动作的贡献计入同一动作）。
- 多个阻断项需要组合动作时显式计算动作包（action bundle）与依赖。
- 分别展示：可比较供给增加、满足需求增量、成本变化、净减排变化、
  服务率变化、费用与耗时；供给新增量 ≠ 实际采用量。
- 未知通过概率时输出情景结果与条件（保守/有利两情景），只有具备概率依据时
  才称期望信息价值/EVSI；费用未知时不输出精确 ROI。
- 有利情景取值来自动作配置的明确测试值（演示假设），不造极端最优值。
"""

from __future__ import annotations

import copy

import yaml

from . import config
from .material import (
    build_options,
    n1_stress_test,
    representative_options,
    supply_state,
)

# 有利情景合法测试值：默认取需求允许区间内的明确值（演示假设，标注）。
# 各动作配置可覆盖（见 config/evidence_actions.yaml 的 optimistic_values）。
_DEFAULT_OPTIMISTIC_VALUES = {
    "mfi_g_10min": 12.0,
    "moisture_pct": 0.3,
    "ash_pct": 3.0,
    "recycled_content_pct": 90.0,
}


def load_evidence_actions(path=None):
    if path is None:
        path = config.CONFIG_DIR / "evidence_actions.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("actions", [])


def plan_evidence_actions(demand, supplies, factor_data, actions=None):
    """为每个待核验批次生成证据动作与保守/有利反事实价值区间，按价值排序。

    单个动作只补其覆盖字段；组合动作包（bundles）单独计算。
    不修改原始数据：反事实在副本上运行。
    """
    actions = actions or load_evidence_actions()
    plans = []
    for supply in supplies:
        state = supply_state(demand, supply)
        if state["state"] != "pending_evidence":
            continue
        for action in actions:
            plan = _evaluate_action(demand, supplies, supply, action, factor_data)
            if plan is not None:
                plans.append(plan)
        bundle = _evaluate_bundle(demand, supplies, supply, actions, factor_data)
        if bundle is not None:
            plans.append(bundle)
    # 排序：可释放净减排增量降序 → 可行供给量增量降序 → 批次 ID 升序（稳定）
    plans.sort(key=lambda p: (
        -(p["value_interval"]["net_avoided_gain_kgco2e"] or 0),
        -(p["value_interval"]["extra_supply_t"] or 0),
        p["supply_id"],
    ))
    return plans


def _blocked_fields_of(supply):
    """该批次当前被阻断（缺失/无证据）的字段清单。"""
    field_map = {
        "再生含量": supply.recycled_content_pct is None or not supply.evidence_source,
        "水分": supply.moisture_pct is None,
        "灰分": supply.ash_pct is None,
        "熔指": supply.mfi_g_10min is None,
        "价格": supply.price_cny_per_t is None,
        "距离": supply.distance_km is None,
        "数量": supply.available_mass_t is None,
    }
    return [name for name, blocked in field_map.items() if blocked]


def _apply_action_fields(supply, action, related_fields, demand):
    """仅补齐动作覆盖的阻断字段。

    有利情景取值顺序：动作配置的 optimistic_values → 默认演示测试值。
    价格取需求基准价 90%（演示假设）；距离取需求上限；其余字段保持缺失。
    """
    optimistic_values = dict(_DEFAULT_OPTIMISTIC_VALUES)
    optimistic_values.update(action.get("optimistic_values") or {})
    if "价格" in related_fields and supply.price_cny_per_t is None:
        supply.price_cny_per_t = _demand_floor_price(demand)
    for field in related_fields:
        value = optimistic_values.get(field)
        if value is None:
            continue
        if getattr(supply, _field_attr(field), None) is None:
            setattr(supply, _field_attr(field), value)
    if "距离" in related_fields and supply.distance_km is None:
        supply.distance_km = demand.max_distance_km or 100.0
    # 检测类动作（覆盖规格字段）视为同时补上证据来源；询价/路线类动作不补证据来源
    if any(f in related_fields for f in ("再生含量", "水分", "灰分", "熔指")):
        supply.evidence_source = supply.evidence_source or "补证（有利情景，演示假设）"
        supply.evidence_date = supply.evidence_date or "待确认"


def _field_attr(field):
    return {"再生含量": "recycled_content_pct", "水分": "moisture_pct",
            "灰分": "ash_pct", "熔指": "mfi_g_10min", "价格": "price_cny_per_t",
            "距离": "distance_km", "数量": "available_mass_t"}[field]


def _evaluate_action(demand, supplies, supply, action, factor_data):
    """单个动作的双情景反事实。返回 None 表示该动作与该批次阻断字段无关。

    有利情景只补该动作覆盖字段（）；其余未知字段保持缺失，
    由缺字段导致的不满足如实保留。
    """
    blocks = action.get("blocks") or []
    related = [name for name in blocks if name in _blocked_fields_of(supply)]
    if not related:
        return None
    baseline_options = build_options(demand, supplies, factor_data)
    baseline_n1 = n1_stress_test(demand, supplies, factor_data,
                                 options=representative_options(baseline_options))
    baseline_min_service = min((e["min_delivery_service_rate"] for e in baseline_n1), default=1.0)
    baseline_best_cost = min((o["total_cost_cny"] for o in baseline_options
                              if o.get("total_cost_cny") is not None), default=None)
    baseline_best_net = max((o["net_avoided_kgco2e"] for o in baseline_options
                             if o.get("net_avoided_kgco2e") is not None), default=0.0)
    baseline_eligible_ids = {s.supply_id for s in supplies
                             if supply_state(demand, s)["state"] == "eligible"}

    optimistic_supplies = [copy.copy(s) for s in supplies]
    optimistic_supply = next(s for s in optimistic_supplies if s.supply_id == supply.supply_id)
    _apply_action_fields(optimistic_supply, action, related, demand)

    optimistic_state = supply_state(demand, optimistic_supply)
    if optimistic_state["state"] != "eligible":
        return {
            "action_id": action.get("action_id"), "action_name": action.get("name"),
            "kind": "single_action",
            "supply_id": supply.supply_id, "supplier_name": supply.supplier_name,
            "blocked_fields": related,
            "fields_not_covered_by_action": [f for f in _blocked_fields_of(supply)
                                             if f not in related],
            "current_state": "pending_evidence",
            "optimistic_state": optimistic_state["state"],
            "value_interval": {"extra_supply_t": 0.0, "cost_saving_max_cny": 0.0,
                               "net_avoided_gain_kgco2e": 0.0, "resilience_improvement": 0.0,
                               "eligible_supply_increase": 0},
            "fee_cny": action.get("fee_cny", "待询"), "lead_days": action.get("lead_days", "待询"),
            "probability": "未知（无通过概率依据，输出保守/有利两情景，非 EVSI）",
            "ranking_reason": "仅此动作补证后仍不满足硬门槛（其余阻断字段未由本动作覆盖）",
            "released_options": [],
        }

    optimistic_options = build_options(demand, optimistic_supplies, factor_data)
    optimistic_n1 = n1_stress_test(demand, optimistic_supplies, factor_data,
                                   options=representative_options(optimistic_options))
    optimistic_min_service = min((e["min_delivery_service_rate"] for e in optimistic_n1),
                                 default=1.0)
    optimistic_best_cost = min((o["total_cost_cny"] for o in optimistic_options
                                if o.get("total_cost_cny") is not None), default=baseline_best_cost)
    optimistic_best_net = max((o["net_avoided_kgco2e"] for o in optimistic_options
                               if o.get("net_avoided_kgco2e") is not None), default=0.0)
    optimistic_eligible_ids = {s.supply_id for s in optimistic_supplies
                               if supply_state(demand, s)["state"] == "eligible"}

    extra_supply = (optimistic_supply.available_mass_t or 0.0)
    eligible_increase = len(optimistic_eligible_ids - baseline_eligible_ids)
    cost_saving = (baseline_best_cost - optimistic_best_cost
                   if baseline_best_cost is not None and optimistic_best_cost is not None else 0.0)
    net_gain = optimistic_best_net - baseline_best_net
    resilience = optimistic_min_service - baseline_min_service
    fee = action.get("fee_cny", "待询")
    lead = action.get("lead_days", "待询")
    net_value = None
    if isinstance(fee, (int, float)) and fee >= 0:
        # 费用已知但通过概率未知：只给「有利情景下的净值上限」，不是 EVSI
        if cost_saving > 0:
            net_value = round(cost_saving - fee, 2)
    return {
        "action_id": action.get("action_id"), "action_name": action.get("name"),
        "kind": "single_action",
        "supply_id": supply.supply_id, "supplier_name": supply.supplier_name,
        "blocked_fields": related,
        "fields_not_covered_by_action": [f for f in _blocked_fields_of(supply)
                                         if f not in related],
        "current_state": "pending_evidence",
        "optimistic_state": "eligible",
        "value_interval": {
            "extra_supply_t": round(extra_supply, 4),
            "eligible_supply_increase": eligible_increase,
            "cost_saving_max_cny": round(max(0.0, cost_saving), 2),
            "net_avoided_gain_kgco2e": round(max(0.0, net_gain), 2),
            "resilience_improvement": round(max(0.0, resilience), 4),
        },
        "fee_cny": fee, "lead_days": lead,
        "net_value_upper_bound_cny": net_value,
        "probability": "未知（无通过概率依据；输出为有利情景值上限，非 EVSI）",
        "ranking_reason": _ranking_reason(extra_supply, cost_saving, net_gain, resilience),
        "released_options": [o["option_id"] for o in optimistic_options
                             if o["kind"] != "baseline"
                             and o.get("total_cost_cny") is not None][:5],
    }


def _evaluate_bundle(demand, supplies, supply, actions, factor_data):
    """组合动作包：显式计算「补全该批次全部阻断字段」所需动作集合与依赖（）。"""
    blocked = _blocked_fields_of(supply)
    if not blocked:
        return None
    covered, needed = set(), []
    for action in actions:
        for field in (action.get("blocks") or []):
            if field in blocked and field not in covered:
                needed.append(action)
                covered.update(a for a in (action.get("blocks") or []) if a in blocked)
    if not needed or covered != set(blocked):
        return None  # 动作包无法覆盖全部阻断字段：如实不生成「全套补证」包
    # 任一成员费用未知时，包费用显示「待询」并注明已计部分，未知不伪装成已知
    known_fees = [a.get("fee_cny") for a in needed
                  if isinstance(a.get("fee_cny"), (int, float))]
    if len(known_fees) == len(needed):
        bundle_fee = sum(known_fees)
    else:
        bundle_fee = f"待询（已知部分 {sum(known_fees):g} CNY）"
    known_leads = [a.get("lead_days") for a in needed
                   if isinstance(a.get("lead_days"), (int, float))]
    bundle_lead = max(known_leads) if known_leads else "待询"
    combined = {"action_id": "BUNDLE-" + "-".join(a.get("action_id", "") for a in needed),
                "name": "组合补证包：" + " + ".join(a.get("name", "") for a in needed),
                "blocks": blocked,
                "fee_cny": bundle_fee,
                "lead_days": bundle_lead,
                "optimistic_values": {}}
    plan = _evaluate_action(demand, supplies, supply, combined, factor_data)
    if plan is None:
        return None
    plan["kind"] = "action_bundle"
    plan["bundle_dependencies"] = [a.get("action_id") for a in needed]
    return plan


def optimistic_supplies(demand, supplies):
    """乐观情景（全套补证，用于「补证前后」N-1 对照二与页面乐观口径）：
    对每个待核验批次补全缺失字段与证据（副本上操作，不改原始数据）。

    值取需求允许区间内的明确测试值（演示假设），不造极端最优值。
    该函数只用于显式标注的乐观情景展示；单个动作的反事实见 _apply_action_fields。
    """
    optimistic = [copy.copy(s) for s in supplies]
    for supply in optimistic:
        if supply_state(demand, supply)["state"] != "pending_evidence":
            continue
        for field, value in _DEFAULT_OPTIMISTIC_VALUES.items():
            if getattr(supply, field) is None:
                setattr(supply, field, value)
        if supply.price_cny_per_t is None:
            supply.price_cny_per_t = _demand_floor_price(demand)
        if supply.distance_km is None:
            supply.distance_km = demand.max_distance_km or 100.0
        supply.evidence_source = supply.evidence_source or "补证（有利情景，演示假设）"
        supply.evidence_date = supply.evidence_date or "待确认"
    return optimistic


def _demand_floor_price(demand):
    """有利情景价格取需求基准价的 90%（演示假设，不是极端最优值）。"""
    base = demand.baseline_virgin_price_cny_per_t if demand is not None else None
    if base is None:
        return 4000.0
    return round(base * 0.9, 2)


def _ranking_reason(extra_supply, cost_saving, net_gain, resilience):
    parts = []
    if extra_supply > 0:
        parts.append(f"可释放供给 {extra_supply:g} t（新增量≠实际采用量）")
    if cost_saving > 0:
        parts.append(f"成本节约上限 {cost_saving:.0f} CNY")
    if net_gain > 0:
        parts.append(f"净减排增量 {net_gain:.0f} kgCO2e")
    if resilience > 0:
        parts.append(f"断供服务率 +{resilience:.0%}")
    return "；".join(parts) if parts else "对当前决策无明显影响"
