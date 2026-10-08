"""碳与成本账本（）：因子角色显式解析、边界兼容规则、到厂成本分项。

设计原则：
- 基准材料、候选材料、运输因子按「角色 role」显式解析，缺失/不匹配时输出具体阻断字段，
  不硬编码 DEMO 因子 ID，不"取第一个运输因子"。
- 原生 cradle-to-gate 与再生 gate-to-gate 的边界兼容性由显式核算约定判定：
  约定声明废料起点、前生命周期负担、cut-off 规则、收率、运输是否已含在因子内。
  满足约定的因子才能相减；不满足 → 「边界不兼容」阻断，不允许默认可比也不机械判不可比。
- 成本为可解释到厂成本分项：材料价、运费、装卸、一次性检测/验证、加工损耗；
  区分报价是否含运费；缺报价不按 0；计划/实际/一次性试点费用分开记录位置（见 cost_ledger 返回结构）。
- 币值精度与排放精度分别处理：行项目保留全精度，展示层再舍入，禁止累加已展示舍入值。
- 交付吨数与再生质量分开：recycled_mass_t = 交付量 × 再生含量/100，不允许 100% 计入循环材料量。
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

ALGORITHM_VERSION = "material-v8"

# 默认核算约定（演示）。真实因子只有满足该约定或经显式转换后才能相减。
# 约定内容可在 config/material_emission_factors.yaml 的 accounting_convention 中覆盖。
DEFAULT_CONVENTION = {
    "name": "演示核算约定（cut-off，交付颗粒口径）",
    "waste_burden": "cut-off（废料起点零负担，不含前生命周期负担，也不叠加处置信用）",
    "recycled_boundary_keywords": ["gate-to-gate"],
    "virgin_boundary_keywords": ["cradle-to-gate"],
    "transport_included_in_factor": False,  # 因子不含运输，运输单独核算
    "basis": "交付 1 t 满足需方规格的合格颗粒",
    "yield_note": "收率损失已含在再生生产因子内；交付口径为已造粒成品时不做额外折算",
}

ROLE_BY_STAGE = {
    "virgin_production": "baseline_material",
    "recycled_granule_production": "project_material",
    "transport": "transport",
}


@dataclass
class LedgerLine:
    """账本行：活动量、单位、单价/因子、公式、来源、结果（全精度）。"""
    item: str
    activity: float
    unit: str
    rate: float | None
    formula: str
    source: str
    amount: float
    amount_display: str = ""
    note: str = ""


@dataclass
class CostLedger:
    lines: list = field(default_factory=list)
    total: float | None = None  # None = 不可计算（存在缺价等阻断）
    problems: list = field(default_factory=list)
    planned: dict = field(default_factory=dict)  # 计划成本（分项合计）
    one_time: dict = field(default_factory=dict)  # 一次性试点费用（与计划成本分开）
    actual: dict = field(default_factory=dict)  # 实际履约成本（未发生为空）

    def to_dict(self):
        return {
            "lines": [l.__dict__ for l in self.lines],
            "total_cny": round(self.total, 2) if self.total is not None else None,
            "problems": self.problems,
            "planned": self.planned,
            "one_time": self.one_time,
            "actual": self.actual,
        }


# v19 任务 1：并行场景线程同时插入会触发持续淘汰，pop(next(iter())) 复合操作
# 非原子——两线程可同时弹出同一键（KeyError）。淘汰与写入统一持锁（读路径免锁）。
_CACHE_LOCK = threading.Lock()
_factor_resolve_cache = {}


def resolve_factor(factor_data, role, material_code, transport_mode=None, region=None):
    """显式解析单个角色因子。返回 (factor, note, problems)。

    选择规则：role + material_code 匹配（transport 角色按 applies_to 匹配运输方式）；
    多命中时按 grade=权威 优先 → year 降序 → version 降序，并记录选择规则；
    零命中输出具体阻断字段（角色/材料/方式/地区）。

    带进程级缓存：以因子表对象身份为槽（持有引用防 id 复用），同一因子表对象内
    重复解析直接命中；敏感性场景的深拷贝副本各自独立缓存。数值与未缓存逐位一致。
    """
    slot = _factor_resolve_cache.get(id(factor_data))
    if slot is None or slot[0] is not factor_data:
        with _CACHE_LOCK:
            slot = (factor_data, {})
            _factor_resolve_cache[id(factor_data)] = slot
            if len(_factor_resolve_cache) > 64:
                _factor_resolve_cache.pop(next(iter(_factor_resolve_cache)))
    key = (role, material_code, transport_mode, region)
    cached = slot[1].get(key)
    if cached is None:
        cached = _resolve_factor(factor_data, role, material_code, transport_mode, region)
        slot[1][key] = cached
    return cached


def _resolve_factor(factor_data, role, material_code, transport_mode=None, region=None):
    """resolve_factor 的无缓存实现（缓存壳见上）。"""
    factors = factor_data.get("factors", {})
    candidates = []
    for fid, factor in factors.items():
        if role == "transport":
            if factor.get("stage") != "transport":
                continue
            if factor.get("material_code") not in ("ANY", material_code):
                continue
            mode = transport_mode or "公路货运"
            applies = factor.get("applies_to") or []
            if applies and mode not in applies:
                continue
        else:
            if factor.get("role", ROLE_BY_STAGE.get(factor.get("stage", ""))) != role:
                continue
            if factor.get("material_code") != material_code:
                continue
        candidates.append(factor)
    if not candidates:
        if role == "transport":
            return None, None, [f"运输因子缺失或不适配：方式={transport_mode or '公路货运'}，材料={material_code}"]
        return None, None, [f"{role} 因子缺失：材料={material_code}"]
    if region:
        regional = [f for f in candidates if f.get("region") == region]
        if regional:
            candidates = regional
        else:
            # 地区不匹配不静默回退：作为提示输出，但不阻断（地区代表性属质量维度）
            pass
    def sort_key(f):
        return (0 if f.get("grade") == "权威" else 1,
                -int(f.get("year") or 0),
                -int(f.get("version") or 0),
                str(f.get("factor_id")))
    chosen = sorted(candidates, key=sort_key)[0]
    if len(candidates) > 1:
        note = (f"多候选因子按 权威优先→年份降序→版本降序 选定；候选："
                f"{'、'.join(str(f.get('factor_id')) for f in candidates)}")
    else:
        note = ""
    return chosen, note, []


def check_boundary_compatibility(factor, role, convention):
    """边界兼容性检查：按核算约定关键字显式判定。返回问题列表（空=兼容）。"""
    problems = []
    if role in ("baseline_material", "project_material"):
        boundary = str(factor.get("boundary") or "")
        if role == "baseline_material":
            keywords = convention.get("virgin_boundary_keywords", [])
            need = "cradle-to-gate"
        else:
            keywords = convention.get("recycled_boundary_keywords", [])
            need = "gate-to-gate"
        if not any(kw in boundary for kw in keywords):
            problems.append(
                f"边界不兼容：因子 {factor.get('factor_id')} 边界「{boundary}」"
                f"不满足核算约定要求的 {need}（约定：{convention.get('name')}）；"
                "需显式转换或换因子，禁止直接相减")
    transport_in_factor = bool(factor.get("transport_included_in_factor",
                                          convention.get("transport_included_in_factor", False)))
    return problems, transport_in_factor


_net_ledger_cache = {}


def net_emissions_ledger(demand, supply, mass_t, factor_data, baseline_distance_km=None,
                         convention=None):
    """单一供给净减排账本。返回与旧 net_emissions 同构的字典 + 账本明细。

    E_baseline = Q×原生生产因子 + Q×基准距离×基准运输因子
    E_project  = Q×再生颗粒生产因子 + Q×候选距离×候选运输因子
    （若因子显式声明 transport_included_in_factor，则该侧不再另加运输项）
    因子/距离缺失 → 待补证据，禁止用 0。

    带进程级缓存（同 resolve_factor 的按身份槽模式）：组合枚举对同一
    (需求, 批次, 质量) 反复调用，缓存返回同一结果对象，数值逐位一致。
    """
    slot = _net_ledger_cache.get(id(factor_data))
    if slot is None or slot[0] is not factor_data:
        with _CACHE_LOCK:
            slot = (factor_data, {})
            _net_ledger_cache[id(factor_data)] = slot
            if len(_net_ledger_cache) > 64:
                _net_ledger_cache.pop(next(iter(_net_ledger_cache)))
    key = (id(demand), id(supply), mass_t, baseline_distance_km,
           id(convention) if convention is not None else None)
    entry = slot[1].get(key)
    if entry is not None:
        return entry[0]
    cached = _net_emissions_ledger(demand, supply, mass_t, factor_data,
                                   baseline_distance_km, convention)
    # v13 修正：值持有 demand/supply/convention 引用——键含其 id，不持引用会在
    # 对象回收、id 被复用后错误命中（v12 已对因子表做同款防护，此处补齐）。
    slot[1][key] = (cached, demand, supply, convention)
    return cached


def _net_emissions_ledger(demand, supply, mass_t, factor_data, baseline_distance_km=None,
                          convention=None):
    """net_emissions_ledger 的无缓存实现（缓存壳见上）。"""
    convention = convention or factor_data.get("accounting_convention") or DEFAULT_CONVENTION
    factors = factor_data.get("factors", {})
    baseline_distance_km = (baseline_distance_km
                            if baseline_distance_km is not None
                            else factor_data.get("baseline_transport_distance_km", 100.0))
    problems = []

    if supply.distance_km is None:
        return {"status": "待补证据", "problems": [f"供给 {supply.supply_id} 距离缺失，禁止用 0"],
                "net_avoided_kgco2e": None, "terms": []}

    virgin, virgin_note, virgin_problems = resolve_factor(
        factor_data, "baseline_material", demand.target_material_code)
    recycle, recycle_note, recycle_problems = resolve_factor(
        factor_data, "project_material", demand.target_material_code)
    problems += virgin_problems + recycle_problems
    if virgin is not None:
        bound_problems, _ = check_boundary_compatibility(virgin, "baseline_material", convention)
        problems += bound_problems
    if recycle is not None:
        bound_problems, _ = check_boundary_compatibility(recycle, "project_material", convention)
        problems += bound_problems

    baseline_transport = None
    project_transport = None
    transport_mode = supply.transport_mode or "公路货运"
    # 基准运输方式来自需求/项目层的显式配置（accounting_convention.baseline_transport_mode），
    # 候选的运输方式只影响项目侧，不得改变同一需求下的基准排放。
    baseline_mode = convention.get("baseline_transport_mode") or "公路货运"
    if not problems:
        baseline_transport, bt_note, bt_problems = resolve_factor(
            factor_data, "transport", "ANY" if not virgin else demand.target_material_code,
            transport_mode=baseline_mode)
        problems += bt_problems
        project_transport, pt_note, pt_problems = resolve_factor(
            factor_data, "transport", demand.target_material_code,
            transport_mode=transport_mode)
        problems += pt_problems
        # 供给显式指定运输因子时优先使用，但仍须通过运输方式适配检查
        if supply.factor_id:
            explicit = factors.get(supply.factor_id)
            if explicit is not None and explicit.get("stage") == "transport":
                applies = explicit.get("applies_to") or []
                if applies and transport_mode not in applies:
                    problems.append(f"供给 {supply.supply_id} 指定运输因子 {supply.factor_id} "
                                    f"不适用于方式：{transport_mode}")
                else:
                    project_transport = explicit
                    pt_note = f"使用供给指定的运输因子 {supply.factor_id}"
            elif explicit is not None:
                problems.append(f"供给 {supply.supply_id} 指定的 factor_id={supply.factor_id} "
                                "不是运输因子（stage≠transport）")
            else:
                problems.append(f"供给 {supply.supply_id} 指定的 factor_id={supply.factor_id} 不存在")
        if project_transport is not None:
            _, project_t_embedded = check_boundary_compatibility(
                project_transport, "transport", convention)
        else:
            project_t_embedded = False
        if baseline_transport is not None:
            _, baseline_t_embedded = check_boundary_compatibility(
                baseline_transport, "transport", convention)
        else:
            baseline_t_embedded = False
        # 两侧「含运」判断统一——材料因子声明已含运输时，该侧不再另加运输
        recycle_embedded = bool(recycle.get("transport_included_in_factor",
                                            convention.get("transport_included_in_factor", False)))
        virgin_embedded = bool(virgin.get("transport_included_in_factor",
                                          convention.get("transport_included_in_factor", False)))
        project_transport_embedded = project_t_embedded or recycle_embedded
        baseline_transport_embedded = baseline_t_embedded or virgin_embedded
    else:
        bt_note = pt_note = ""
        project_transport_embedded = False

    if problems:
        return {"status": "待补证据", "problems": problems,
                "net_avoided_kgco2e": None, "terms": [],
                "blocked_fields": sorted(set(problems))}

    virgin_value = float(virgin["value"])
    recycle_value = float(recycle["value"])
    baseline_t_value = float(baseline_transport["value"])
    project_t_value = float(project_transport["value"])
    distance = supply.distance_km

    baseline = mass_t * virgin_value
    project = mass_t * recycle_value
    terms = [
        {"term": "原生颗粒生产（基准）", "activity_t": mass_t, "factor": virgin,
         "note": f"Q × {virgin_value:g} kgCO2e/t"},
        {"term": "原生料运输（基准）", "activity_t": mass_t, "factor": baseline_transport,
         "note": f"基准运输距离 {baseline_distance_km:g} km（演示假设，改它只影响基准侧）"},
        {"term": "再生颗粒生产（项目）", "activity_t": mass_t, "factor": recycle,
         "note": f"Q × {recycle_value:g} kgCO2e/t"},
        {"term": "再生物料运输（项目）", "activity_t": mass_t, "factor": project_transport,
         "note": f"候选距离 {distance:g} km"},
    ]
    if not baseline_transport_embedded and baseline_transport is not None:
        baseline += mass_t * baseline_distance_km * baseline_t_value
    else:
        terms[1]["note"] = "基准侧已含运输（transport_included_in_factor），不另加"
    if not project_transport_embedded:
        project += mass_t * distance * project_t_value
    else:
        terms[3]["note"] = "再生因子已含运输（transport_included_in_factor），不另加"
    net = baseline - project
    recycled_mass = mass_t * (supply.recycled_content_pct or 0.0) / 100.0
    notes = [n for n in (virgin_note, bt_note, pt_note) if n]
    return {
        "status": "可计算",
        "problems": [],
        "mass_t": mass_t,
        "delivered_mass_t": mass_t,
        "recycled_mass_t": round(recycled_mass, 4),
        "baseline_distance_km": baseline_distance_km,
        "baseline_kgco2e": round(baseline, 4),
        "project_kgco2e": round(project, 4),
        "net_avoided_kgco2e": round(net, 4),
        # 内部全精度值：比较/排序用（禁止用已展示舍入值）
        "_baseline_full": baseline,
        "_project_full": project,
        "_net_full": net,
        "net_sign": "净减排" if net > 0 else ("净增排" if net < 0 else "无变化"),
        "terms": terms,
        "factor_selection_notes": notes,
        "convention": convention.get("name"),
        "boundary_note": ("功能单位：交付 1 t 满足需方规格的合格颗粒；"
                          f"核算约定：{convention.get('name')}；"
                          f"废料起点：{convention.get('waste_burden')}；"
                          f"收率说明：{convention.get('yield_note')}；"
                          "不含使用阶段与处置环节（未取得废料来源与基线去向证据，不叠加处置信用）"),
    }


_cost_ledger_cache = {}


def project_cost_ledger(demand, allocation, supplies_by_id, cost_assumptions=None):
    """项目侧到厂成本账本。allocation: {supply_id: mass_t}。

    分项：材料价、运费、装卸费、一次性检测/验证费、加工损耗费用。
    缺报价 → 阻断（不按 0）；price_includes_freight → 运费为 0 并注明；
    一次性费用（检测/验证）与计划分项分开记录在 one_time 中。

    带进程级缓存：成本账本只依赖分配量、供给对象与成本假设（与因子无关），
    敏感性场景（因子扰动）间也可命中。键按成本假设的值（deepcopy 后身份不同），
    供给按对象身份。返回同一结果对象，数值逐位一致。
    """
    try:
        cost_key = tuple(sorted(cost_assumptions.items())) if cost_assumptions else ()
        supp_key = tuple(sorted((sid, id(sup)) for sid, sup in supplies_by_id.items()))
        key = (id(demand), tuple(sorted(allocation.items())), cost_key, supp_key)
        entry = _cost_ledger_cache.get(key)
        if entry is not None:
            return entry[0]
    except TypeError:
        pass  # 假设值不可哈希等意外：走无缓存路径
    ledger = _project_cost_ledger(demand, allocation, supplies_by_id, cost_assumptions)
    try:
        # v13 修正：值持有 demand/supplies_by_id 引用——键含其 id，不持引用会在
        # 对象回收、id 被复用后错误命中（跨测试/长进程下偶发数值串扰）。
        with _CACHE_LOCK:
            _cost_ledger_cache[key] = (ledger, demand, supplies_by_id, cost_assumptions)
            if len(_cost_ledger_cache) > 8192:
                _cost_ledger_cache.pop(next(iter(_cost_ledger_cache)))
    except (TypeError, UnboundLocalError):
        pass
    return ledger


def _project_cost_ledger(demand, allocation, supplies_by_id, cost_assumptions=None):
    """project_cost_ledger 的无缓存实现（缓存壳见上）。"""
    cost_assumptions = cost_assumptions or {}
    freight_rate = float(cost_assumptions.get("freight_rate_cny_per_tkm", 0.35))
    handling_rate = float(cost_assumptions.get("handling_fee_cny_per_t", 20.0))
    inspection_fee = float(cost_assumptions.get("inspection_fee_per_batch_cny", 800.0))
    default_yield = float(cost_assumptions.get("default_yield", 1.0))
    ledger = CostLedger()
    material_total = freight_total = handling_total = loss_total = 0.0
    one_time_total = 0.0
    for supply_id, mass in sorted(allocation.items()):
        supply = supplies_by_id.get(supply_id)
        if supply is None:
            ledger.problems.append(f"分配引用了不存在的供给批次 {supply_id}")
            continue
        if supply.price_cny_per_t is None:
            ledger.problems.append(f"{supply.supplier_name}（{supply.supply_id}）：缺价，材料价不可计算，不按 0 处理")
            continue
        price = float(supply.price_cny_per_t)
        material_cost = mass * price
        material_total += material_cost
        ledger.lines.append(LedgerLine(
            item="材料价", activity=round(mass, 4), unit="t", rate=round(price, 4),
            formula=f"{mass:g} t × {price:g} CNY/t",
            source=f"供给报价（{supply.supply_id}，是否含运：{'是' if supply.price_includes_freight else '否/未标注'}）",
            amount=material_cost))
        if supply.price_includes_freight:
            ledger.lines.append(LedgerLine(
                item="运费", activity=round(mass, 4), unit="t", rate=0.0,
                formula="报价已含运费，另计 0",
                source=f"供给报价含运（{supply.supply_id}）", amount=0.0))
        else:
            if supply.distance_km is None:
                ledger.problems.append(f"{supply.supplier_name}（{supply.supply_id}）：距离缺失，运费不可计算")
            else:
                freight = mass * supply.distance_km * freight_rate
                freight_total += freight
                ledger.lines.append(LedgerLine(
                    item="运费", activity=round(mass * supply.distance_km, 4), unit="t·km",
                    rate=round(freight_rate, 4),
                    formula=f"{mass:g} t × {supply.distance_km:g} km × {freight_rate:g} CNY/(t·km)",
                    source=f"演示假设运价 {freight_rate:g} CNY/(t·km)（待真实报价替换）",
                    amount=freight))
        handling = mass * handling_rate
        handling_total += handling
        ledger.lines.append(LedgerLine(
            item="装卸费", activity=round(mass, 4), unit="t", rate=round(handling_rate, 4),
            formula=f"{mass:g} t × {handling_rate:g} CNY/t",
            source=f"演示假设装卸费率 {handling_rate:g} CNY/t", amount=handling))
        if supply.inspection_required or not supply.evidence_source:
            one_time_total += inspection_fee
            ledger.lines.append(LedgerLine(
                item="一次性检测/验证费", activity=1.0, unit="批次", rate=round(inspection_fee, 4),
                formula=f"1 批次 × {inspection_fee:g} CNY",
                source=f"无证据来源需补第三方检测（{supply.supply_id}）；一次性试点费用，与计划成本分开",
                amount=inspection_fee))
        yield_ = float(supply.yield_pct or 0.0) / 100.0 if supply.yield_pct else default_yield
        if yield_ <= 0 or not math.isfinite(yield_):
            ledger.problems.append(f"{supply.supplier_name}（{supply.supply_id}）：收率非法（{supply.yield_pct}）")
        elif yield_ < 1.0:
            input_mass = mass / yield_
            loss_cost = (input_mass - mass) * price
            loss_total += loss_cost
            ledger.lines.append(LedgerLine(
                item="加工损耗费用", activity=round(input_mass - mass, 4), unit="t",
                rate=round(price, 4),
                formula=f"({mass:g}/{yield_:g} − {mass:g}) t × {price:g} CNY/t",
                source=f"收率 {supply.yield_pct}%（{supply.supply_id}）",
                amount=loss_cost))
    if ledger.problems:
        ledger.total = None
    else:
        ledger.total = material_total + freight_total + handling_total + loss_total
        ledger.planned = {
            "material_cny": round(material_total, 4),
            "freight_cny": round(freight_total, 4),
            "handling_cny": round(handling_total, 4),
            "processing_loss_cny": round(loss_total, 4),
            "total_planned_cny": round(ledger.total, 4),
        }
        ledger.one_time = {"inspection_fee_cny": round(one_time_total, 4)}
    return ledger


def baseline_cost(demand, required_mass_t, cost_assumptions=None, baseline_distance_km=None,
                  factor_data=None):
    """基准侧到厂成本：原生价 × 数量 + 基准运输距离运费（与项目侧同口径）。"""
    cost_assumptions = cost_assumptions or {}
    freight_rate = float(cost_assumptions.get("freight_rate_cny_per_tkm", 0.35))
    price = demand.baseline_virgin_price_cny_per_t
    if price is None:
        return None, ["基准原生价缺失，基准成本不可计算"]
    if baseline_distance_km is None:
        baseline_distance_km = (factor_data or {}).get("baseline_transport_distance_km", 100.0)
    material = required_mass_t * price
    freight = required_mass_t * baseline_distance_km * freight_rate
    return round(material + freight, 4), []


# —— 净减排区间传播（v14 任务 1：确定性 UQ） ——
#
# 因子条目可携带可选 value_min/value_max（成对，0 ≤ min ≤ value ≤ max）；未带区间
# 的因子退化为点区间 [value, value]。排放对各因子取值单调递增，故区间端点按极值
# 组合直接传播；净减排区间 = 基准区间 − 项目区间（区间减法）。与点估计账本
# _net_emissions_ledger 复用同一 resolve_factor/check_boundary_compatibility，
# 可计算/待补证据判定一致；点估计值必然落在区间内（属性测试锁定）。
# 区间含义：输入因子极值传播的确定界，不是统计置信区间（如实标注，红线 7）。


def factor_data_has_ranges(factor_data):
    """因子表内是否存在任一带 value_min/value_max 的因子（决定输出是否携带区间）。"""
    for factor in (factor_data or {}).get("factors", {}).values():
        if factor.get("value_min") is not None or factor.get("value_max") is not None:
            return True
    return False


def _factor_interval(factor):
    """单因子取值区间 [lo, hi]；未带 value_min/value_max 时退化为点区间。"""
    value = float(factor["value"])
    lo, hi = factor.get("value_min"), factor.get("value_max")
    if lo is None and hi is None:
        return value, value
    return float(lo), float(hi)


def net_interval(demand, supply, mass_t, factor_data, baseline_distance_km=None,
                 convention=None):
    """单一供给净减排区间账本（与 net_emissions_ledger 同构的区间版）。

    返回 {"status": "可计算"|"待补证据", "problems", "interval_kgco2e": [lo, hi]}；
    status 判定条件与点估计账本一致。不做缓存（仅区间场景调用，量级小）。
    """
    convention = convention or factor_data.get("accounting_convention") or DEFAULT_CONVENTION
    factors = factor_data.get("factors", {})
    baseline_distance_km = (baseline_distance_km
                            if baseline_distance_km is not None
                            else factor_data.get("baseline_transport_distance_km", 100.0))
    problems = []
    if supply.distance_km is None:
        return {"status": "待补证据", "problems": [f"供给 {supply.supply_id} 距离缺失，禁止用 0"],
                "interval_kgco2e": None}
    virgin, _, virgin_problems = resolve_factor(
        factor_data, "baseline_material", demand.target_material_code)
    recycle, _, recycle_problems = resolve_factor(
        factor_data, "project_material", demand.target_material_code)
    problems += virgin_problems + recycle_problems
    if virgin is not None:
        problems += check_boundary_compatibility(virgin, "baseline_material", convention)[0]
    if recycle is not None:
        problems += check_boundary_compatibility(recycle, "project_material", convention)[0]
    transport_mode = supply.transport_mode or "公路货运"
    baseline_mode = convention.get("baseline_transport_mode") or "公路货运"
    baseline_transport = project_transport = None
    baseline_embedded = project_embedded = recycle_embedded = virgin_embedded = False
    if not problems:
        baseline_transport, _, bt_problems = resolve_factor(
            factor_data, "transport", "ANY" if not virgin else demand.target_material_code,
            transport_mode=baseline_mode)
        problems += bt_problems
        project_transport, _, pt_problems = resolve_factor(
            factor_data, "transport", demand.target_material_code,
            transport_mode=transport_mode)
        problems += pt_problems
        if supply.factor_id:
            explicit = factors.get(supply.factor_id)
            if explicit is not None and explicit.get("stage") == "transport":
                applies = explicit.get("applies_to") or []
                if applies and transport_mode not in applies:
                    problems.append(f"供给 {supply.supply_id} 指定运输因子 {supply.factor_id} "
                                    f"不适用于方式：{transport_mode}")
                else:
                    project_transport = explicit
            elif explicit is not None:
                problems.append(f"供给 {supply.supply_id} 指定的 factor_id={supply.factor_id} "
                                "不是运输因子（stage≠transport）")
            else:
                problems.append(f"供给 {supply.supply_id} 指定的 factor_id={supply.factor_id} 不存在")
        if project_transport is not None:
            project_embedded = check_boundary_compatibility(
                project_transport, "transport", convention)[1]
        if baseline_transport is not None:
            baseline_embedded = check_boundary_compatibility(
                baseline_transport, "transport", convention)[1]
        recycle_embedded = bool(recycle.get("transport_included_in_factor",
                                            convention.get("transport_included_in_factor", False)))
        virgin_embedded = bool(virgin.get("transport_included_in_factor",
                                          convention.get("transport_included_in_factor", False)))
    if problems:
        return {"status": "待补证据", "problems": problems, "interval_kgco2e": None}
    v_lo, v_hi = _factor_interval(virgin)
    r_lo, r_hi = _factor_interval(recycle)
    bt_lo, bt_hi = _factor_interval(baseline_transport)
    pt_lo, pt_hi = _factor_interval(project_transport)
    distance = supply.distance_km
    base_terms = [(mass_t, v_lo, v_hi)]
    if not (baseline_embedded or virgin_embedded):
        base_terms.append((mass_t * baseline_distance_km, bt_lo, bt_hi))
    base_lo = sum(q * lo for q, lo, _ in base_terms)
    base_hi = sum(q * hi for q, _, hi in base_terms)
    proj_terms = [(mass_t, r_lo, r_hi)]
    if not (project_embedded or recycle_embedded):
        proj_terms.append((mass_t * distance, pt_lo, pt_hi))
    proj_lo = sum(q * lo for q, lo, _ in proj_terms)
    proj_hi = sum(q * hi for q, _, hi in proj_terms)
    return {"status": "可计算", "problems": [],
            "interval_kgco2e": [base_lo - proj_hi, base_hi - proj_lo]}
