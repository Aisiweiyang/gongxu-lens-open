"""多需求-多批次双边撮合引擎 v0（v14 任务 3，Gale-Shapley 容量变体）。

借鉴：Gale & Shapley (1962) *College Admissions and the Stability of Marriage*
及其 hospital-residents 容量变体（公开经典算法，自行实现；未使用任何撮合库代码）。
需求侧（求婚方）按自身偏好逐批次申请；批次侧（接收方）按自身偏好与剩余容量决定
接收或替换现有最差被分配方（全额释放，被替换方保留未满足需求继续申请）。

偏好全部确定性、可解释：
- 需求侧排序键 =（到厂单价 CNY/t 升序，净减排强度降序，距离升序，批次 ID 升序）；
  净减排不可计算的批次排在其后（强度记为 -1，展示时注明）。
- 供给侧排序键 =（需求基准单价 − 批次到厂单价的毛利空间降序，需求量/批次产能升序，
  需求 ID 升序）。毛利空间为演示偏好模型，非真实报价行为（如实标注）。

口径与红线：
- 可行集直接复用 src.material.match_gates 硬门槛（数量门槛按「可组合」放行），
  不另造宽松规则；
- 输出是「撮合建议」，不产生预留、不构成交易；演示数据须标注「演示」；
- 稳定性自检：输出后扫描「阻断对」（blocking pair）必须为零，非零视为实现缺陷。
"""

from .ledger import net_emissions_ledger, project_cost_ledger
from .material import match_gates

_TOL = 1e-9


def landed_unit_cost(supply, cost_assumptions):
    """批次到厂单价（CNY/t）：材料价 + 运费（报价含运则为 0）+ 装卸；缺价返回 None。"""
    if supply.price_cny_per_t is None:
        return None
    cost = float(supply.price_cny_per_t)
    cost_assumptions = cost_assumptions or {}
    if not supply.price_includes_freight:
        if supply.distance_km is None:
            return None
        rate = float(cost_assumptions.get("freight_rate_cny_per_tkm", 0.35))
        cost += float(supply.distance_km) * rate
    handling = cost_assumptions.get("handling_fee_cny_per_t")
    if handling is not None:
        cost += float(handling)
    return cost


def net_avoided_intensity(demand, supply, factor_data):
    """批次对该需求的净减排强度（kgCO2e/t 交付量）；不可计算返回 None。"""
    result = net_emissions_ledger(demand, supply, 1.0, factor_data)
    if result.get("status") != "可计算" or result.get("_net_full") is None:
        return None
    return result["_net_full"]


def demand_batch_preference(demand, supply, factor_data, cost_assumptions=None):
    """需求侧对单个批次的偏好序键（越小越优）；不可定价批次返回 None（不进入偏好表）。"""
    cost = landed_unit_cost(supply, cost_assumptions)
    if cost is None:
        return None
    intensity = net_avoided_intensity(demand, supply, factor_data)
    return (round(cost, 6),
            0 if intensity is not None else 1,
            -(intensity if intensity is not None else 0.0),
            float(supply.distance_km or 0.0),
            supply.supply_id)


def supply_demand_preference(supply, demand, cost_assumptions=None,
                             preference_model="margin"):
    """供给侧（批次）对单个需求的偏好序键（越小越优）。

    preference_model="margin"：毛利空间 = 需求基准单价 − 批次到厂单价（降序）；
    需求缺基准价记为 -1（排在有价需求之后）。第二键：需求量/批次产能升序
    （优先小单，减少产能碎片化）。演示偏好模型，非真实报价行为。"""
    if preference_model != "margin":
        raise ValueError(f"未知偏好模型：{preference_model}")
    unit_cost = landed_unit_cost(supply, cost_assumptions)
    if unit_cost is None or demand.baseline_virgin_price_cny_per_t is None:
        margin = -1.0
    else:
        margin = float(demand.baseline_virgin_price_cny_per_t) - unit_cost
    capacity = supply.available_mass_t or 0.0
    size_ratio = (demand.required_mass_t or 0.0) / capacity if capacity > _TOL else 0.0
    return (-round(margin, 6), round(size_ratio, 9), demand.demand_id)


def eligible_batches(demand, supplies):
    """需求可行批次集（match_gates 全过或仅数量待组合；硬淘汰/待补证据不含）。"""
    result = []
    for supply in supplies:
        gates, elimination, _pending = match_gates(demand, supply)
        if elimination:
            continue
        result.append(supply)
    return result


def match_demands(demands, supplies, factor_data, preference_model="margin"):
    """多需求-多批次稳定撮合（Gale-Shapley 容量变体）。

    返回 {"matches", "unmatched", "stability", "note"}。输出为撮合建议，
    不产生预留、不构成交易。"""
    cost_assumptions = factor_data.get("cost_assumptions") or {}
    # 预计算每个需求的可行批次与需求侧偏好表
    prefs = {}
    for demand in demands:
        ranked = []
        for supply in eligible_batches(demand, supplies):
            key = demand_batch_preference(demand, supply, factor_data, cost_assumptions)
            if key is not None:
                ranked.append((key, supply))
        ranked.sort(key=lambda item: item[0])
        prefs[demand.demand_id] = [supply for _key, supply in ranked]
    supply_prefs = {
        supply.supply_id: sorted(
            demands, key=lambda d: supply_demand_preference(supply, d, cost_assumptions,
                                                             preference_model))
        for supply in supplies
    }
    supply_by_id = {s.supply_id: s for s in supplies}
    demand_by_id = {d.demand_id: d for d in demands}

    capacity = {s.supply_id: float(s.available_mass_t or 0.0) for s in supplies}
    alloc = {s.supply_id: {} for s in supplies}      # batch_id -> {demand_id: mass}
    served = {d.demand_id: {} for d in demands}      # demand_id -> {batch_id: mass}
    remaining = {d.demand_id: float(d.required_mass_t or 0.0) for d in demands}
    next_index = {d.demand_id: 0 for d in demands}
    rank_of = {sid: {d.demand_id: i for i, d in enumerate(prefs_list)}
               for sid, prefs_list in supply_prefs.items()}

    def worst_admit(sid):
        """批次当前最差被分配方（按批次偏好排名最大者）；空则 None。"""
        if not alloc[sid]:
            return None
        return max(alloc[sid], key=lambda did: rank_of[sid].get(did, len(supply_prefs[sid])))

    applying = sorted(demand_by_id)  # 确定性扫描顺序
    progressed = True
    while progressed:
        progressed = False
        for did in applying:
            if remaining[did] <= _TOL:
                continue
            while remaining[did] > _TOL and next_index[did] < len(prefs[did]):
                sid = prefs[did][next_index[did]].supply_id
                if sid in served[did] and served[did][sid] > _TOL:
                    next_index[did] += 1
                    continue  # 已从该批次获得分配，不重复申请
                free = capacity[sid] - sum(alloc[sid].values())
                worst = worst_admit(sid)
                batch_rank_of_did = rank_of[sid].get(did)
                can_take_free = free > _TOL
                can_displace = (worst is not None and batch_rank_of_did is not None
                                and batch_rank_of_did < rank_of[sid].get(worst, 10 ** 9))
                if not can_take_free and not can_displace:
                    next_index[did] += 1
                    continue
                if can_take_free:
                    amount = min(remaining[did], free)
                else:
                    worst_mass = alloc[sid].get(worst, 0.0)
                    amount = min(remaining[did], worst_mass)
                    # 全额替换语义：从最差被分配方释放 amount（可能部分释放）
                    alloc[sid][worst] -= amount
                    served[worst][sid] -= amount
                    if alloc[sid][worst] <= _TOL:
                        del alloc[sid][worst]
                    if served[worst][sid] <= _TOL:
                        del served[worst][sid]
                    remaining[worst] = round(remaining[worst] + amount, 9)
                alloc[sid][did] = alloc[sid].get(did, 0.0) + amount
                served[did][sid] = served[did].get(sid, 0.0) + amount
                remaining[did] = round(remaining[did] - amount, 9)
                progressed = True
                if remaining[did] <= _TOL:
                    break

    matches = []
    for demand in demands:
        did = demand.demand_id
        if not served[did]:
            continue
        allocations = {sid: round(mass, 4) for sid, mass in sorted(served[did].items())
                       if mass > _TOL}
        supplies_by_id = {s.supply_id: s for s in supplies}
        ledger = project_cost_ledger(demand, allocations, supplies_by_id, cost_assumptions)
        matches.append({
            "demand_id": did,
            "allocations": allocations,
            "satisfied_t": round(sum(allocations.values()), 4),
            "remaining_t": round(remaining[did], 4),
            "proposed_cost_cny": round(ledger.total, 2) if ledger.total is not None else None,
            "cost_problems": list(ledger.problems),
            "note": "撮合建议（非交易/非预留）",
        })
    unmatched = []
    for demand in demands:
        did = demand.demand_id
        if remaining[did] > _TOL:
            if not prefs[did]:
                reason = "无可行批次（硬门槛淘汰/待补证据/缺价）"
            elif next_index[did] >= len(prefs[did]):
                reason = "偏好表批次均拒绝（产能被更高偏好需求占据或容量不足）"
            else:
                reason = "需求未完全满足（容量不足）"
            unmatched.append({"demand_id": did, "remaining_t": round(remaining[did], 4),
                              "reason": reason})
    # —— 稳定性自检：阻断对必须为零 ——
    blocking = _blocking_pairs(demands, supplies, factor_data, cost_assumptions,
                               prefs, rank_of, alloc, served, remaining, capacity)
    leftovers = {sid: round(capacity[sid] - sum(alloc[sid].values()), 4)
                 for sid in sorted(capacity)
                 if capacity[sid] - sum(alloc[sid].values()) > _TOL}
    return {
        "matches": matches,
        "unmatched": unmatched,
        "leftover_capacity_t": leftovers,
        "stability": {"blocking_pairs": len(blocking), "checked_pairs": blocking,
                      "algorithm": "Gale-Shapley 容量变体（hospital-residents）"},
        "note": ("撮合建议（非交易/非预留）；供给侧偏好为演示毛利模型；"
                 "稳定性自检阻断对必须为零，非零即实现缺陷"),
    }


def _blocking_pairs(demands, supplies, factor_data, cost_assumptions,
                    prefs, rank_of, alloc, served, remaining, capacity):
    """阻断对扫描（hospital-residents 稳定性定义的连续容量版）。

    (d, b) 构成阻断当且仅当 d 尚有未满足需求，且：
    - d 已从 b 获得部分分配但 b 仍有剩余容量（容量应已给到 d）；或
    - d 未从 b 获得分配、b 优于 d 当前持有的最差批次（或 d 尚无持有），
      且 b 有剩余容量或 b 的最差被分配方劣于 d。"""
    pairs = []
    for demand in demands:
        did = demand.demand_id
        if remaining[did] <= _TOL:
            continue
        held = {sid: mass for sid, mass in served[did].items() if mass > _TOL}
        worst_held = (max(held, key=lambda sid: rank_of[sid].get(did, 10 ** 9))
                      if held else None)
        for supply in prefs.get(did, []):
            sid = supply.supply_id
            free = capacity[sid] - sum(alloc[sid].values())
            already = held.get(sid, 0.0) > _TOL
            if already:
                if free > _TOL:
                    pairs.append({"demand_id": did, "batch_id": sid,
                                  "reason": "已持有部分分配但批次仍有剩余容量"})
                continue
            d_prefers = (worst_held is None
                         or rank_of[sid].get(did) is not None
                         and rank_of[sid].get(did) < rank_of[sid].get(worst_held, 10 ** 9))
            if not d_prefers:
                continue
            worst_admit = None
            if alloc[sid]:
                worst_admit = max(alloc[sid],
                                  key=lambda x: rank_of[sid].get(x, 10 ** 9))
            b_prefers = free > _TOL or (
                worst_admit is not None
                and rank_of[sid].get(did) is not None
                and rank_of[sid].get(did) < rank_of[sid].get(worst_admit, 10 ** 9))
            if b_prefers:
                pairs.append({"demand_id": did, "batch_id": sid,
                              "reason": "需求与批次互相偏好但未达成分配"})
    return pairs
