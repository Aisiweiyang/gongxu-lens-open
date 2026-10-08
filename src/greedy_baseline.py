"""贪心基线对照：按「成本最低优先」贪心填满需求，作为完整枚举的对照基线。

借鉴供应链模拟库 SupplyNetPy「与解析基线互验」的范式：贪心是采购人员的
直觉做法（谁便宜先买谁），完整枚举+非支配筛选是系统做法。对照回答
「为什么需要枚举」——贪心能找到成本最优单供，但看不到非支配前沿与
断供韧性。对照结果写进报告与网站，如实呈现两种策略的差异。

口径说明（）：贪心与枚举的成本比较使用同一到厂成本账本；结论由真实
计算结果生成，不出现 57,000 / 53% 等固定叙事数字。
"""

from .material import supply_state
from .ledger import project_cost_ledger


def greedy_allocate(demand, supplies, factor_data):
    """成本最低优先的贪心分配：返回分配（按 supply_id 键）、总成本与说明（不修改原始数据）。"""
    required = demand.required_mass_t or 0.0
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    priced = [s for s in eligible if s.price_cny_per_t is not None]
    allocation, remaining = {}, required
    for supply in sorted(priced, key=lambda s: (s.price_cny_per_t, s.supply_id)):
        if remaining <= 1e-9:
            break
        take = min(remaining, supply.available_mass_t or 0.0)
        if take <= 1e-9:
            continue
        allocation[supply.supply_id] = round(take, 4)
        remaining = round(remaining - take, 4)
    if remaining > 1e-9:
        return {"computable": False, "allocation": allocation,
                "gap_t": remaining,
                "note": "贪心在可用供给内填不满需求（产能不足）"}
    supplies_by_id = {s.supply_id: s for s in priced}
    ledger = project_cost_ledger(demand, allocation, supplies_by_id,
                                 factor_data.get("cost_assumptions") or {})
    return {"computable": ledger.total is not None, "allocation": allocation,
            "total_cost_cny": round(ledger.total, 2) if ledger.total is not None else None,
            "_total_full": ledger.total,  # 一致性判定用全精度，不用展示舍入值
            "cost_ledger": ledger.to_dict(),
            "n_suppliers": len({s.supplier_name for s in priced if s.supply_id in allocation}),
            "problems": ledger.problems}


def greedy_report(demand, supplies, factor_data, mix=None):
    """贪心 vs 完整枚举的对照结论。

    枚举最优 = 全部方案（基准除外）中的最低总成本；贪心对照它，
    而不是对照「多供方组合」子集——否则会拿贪心的单供去比组合子集。
    """
    from .material import build_options

    greedy = greedy_allocate(demand, supplies, factor_data)
    options = build_options(demand, supplies, factor_data)
    # v13 修正：_cost_full 取值 None 防御（回退组合选项此前缺该键，None 混入 min()
    # 会 TypeError）；统一「全精度优先，缺失退展示值」。
    def option_cost_full(option):
        value = option.get("_cost_full")
        return option.get("total_cost_cny") if value is None else value

    best_mix_cost = min((option_cost_full(o) for o in options
                         if o["kind"] != "baseline" and o.get("total_cost_cny") is not None),
                        default=None)
    if not greedy.get("computable") or greedy.get("total_cost_cny") is None:
        return {"computable": False, "note": greedy.get("note") or "贪心分配不可计算（缺价）"}
    greedy_full = greedy.get("_total_full", greedy["total_cost_cny"])
    cost_match = (best_mix_cost is not None
                  and abs(greedy_full - best_mix_cost) < 1e-6)
    names = {s.supply_id: s.supplier_name for s in supplies}
    alloc_text = "、".join(f"{names.get(k, k)} {v:g}t" for k, v in greedy["allocation"].items())
    if best_mix_cost is None:
        verdict = (f"贪心找到到厂成本 {greedy['total_cost_cny']:,.0f} CNY 的分配（{alloc_text}），"
                   "但完整枚举中没有可比较的组合方案（缺价/缺证据），无法做贪心 vs 枚举对照。")
    elif cost_match and greedy["n_suppliers"] == 1:
        verdict = (f"贪心与枚举在到厂成本上一致（均 {greedy['total_cost_cny']:,.0f} CNY）"
                   f"——但贪心只给出单供方案 {alloc_text}，看不到组合前沿与断供韧性对照；"
                   "这正是完整枚举 + Pareto + N-1 的价值。")
    elif cost_match:
        verdict = (f"贪心与枚举最优到厂成本一致（{greedy['total_cost_cny']:,.0f} CNY），"
                   f"贪心方案 {alloc_text}；枚举还提供其他前沿方案与韧性信息。")
    else:
        verdict = (f"贪心没有找到枚举的最优到厂成本方案（贪心 {greedy['total_cost_cny']:,.0f}"
                   f" vs 枚举最优 {best_mix_cost:,.0f} CNY）——说明按直觉采购会付出组合代价。")
    return {
        "computable": True,
        "total_cost_cny": greedy["total_cost_cny"],
        "allocation": greedy["allocation"],
        "n_suppliers": greedy["n_suppliers"],
        "enumeration_best_cost": best_mix_cost,
        "cost_match": cost_match,
        "verdict": verdict,
    }
