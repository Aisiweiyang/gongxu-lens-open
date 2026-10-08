"""再生材料供需匹配与净减排决策模块（首个场景：再生 PP 颗粒替代原生 PP）。

统一单位：质量 t、单价 CNY/t、总成本 CNY、距离 km、
运输因子 kgCO2e/(t·km)、材料因子 kgCO2e/t。

功能单位：交付 1 吨满足需方规格的合格颗粒。供给为已造粒成品时，
交付质量与分配质量一致；收率折算只在收率<100% 的批次上按成本账本处理（）。

净减排（v8 口径，SPEC v10 ）：
    因子按「角色」显式解析（baseline_material / project_material / transport），
    边界兼容性按核算约定判定（原生 cradle-to-gate、再生 gate-to-gate、cut-off 废料起点），
    满足约定才能相减；缺失/不匹配输出具体阻断字段，禁止用 0。
    成本为到厂成本分项账本（材料/运费/装卸/检测/损耗），缺价不按 0。

方案身份：
    所有方案（基准/单供/组合）携带稳定 option_id（场景身份+批次 ID+规范化分配量+
    因子配置版本不进入 ID、另记于护照 versions.factor_config_sha256），显示名称不是主键；同供应商多个批次独立编号，
    产能按批次核算、N-1 按供应商实体失效（）。

组合求解：
    小规模按 0.5t 步长完整枚举 + 非支配前沿（可解释基准）；组合数超过 MAX_COMBINATIONS 时
    优先 OR-Tools CP-SAT 求三锚点字典序全局最优（solver_status=optimal），未安装或未证实
    最优回退贪心可行解（solver_status=feasible），如实标注、不冒充。与穷举的互验见
    tests/test_v13_solver.py。
"""

from __future__ import annotations

import copy
import csv
import os
import hashlib
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from . import config
from .ledger import (ALGORITHM_VERSION, baseline_cost, factor_data_has_ranges,
                     net_emissions_ledger, net_interval, project_cost_ledger)
from .imports import (DEMAND_REQUIRED, SUPPLY_REQUIRED, format_issues,
                      preflight_demand_rows, preflight_supply_rows, split_issues)

TOL = 1e-9
# 支配比较容差：成本/净减排在 1e-6（绝对）内视为相等，超过容差的改进才算严格支配，
# 避免浮点末位差异翻转支配关系（D1 独立核验发现）。
DOM_TOL_COST_NET = 1e-6
DOM_TOL_SHARE = 1e-9
MAX_COMBINATIONS = 20000

CONFIG_VERSION_KEY = "algorithm_version"


@dataclass
class MaterialSupply:
    supply_id: str = ""
    supplier_id: str = ""
    supplier_name: str = ""
    material_code: str = ""
    material_name_raw: str = ""
    polymer_type: str = ""
    form: str = ""
    color: str = ""
    grade: str = ""
    mfi_g_10min: float | None = None
    moisture_pct: float | None = None
    ash_pct: float | None = None
    recycled_content_pct: float | None = None
    available_mass_t: float | None = None
    available_from: str = ""
    available_to: str = ""
    origin: str = ""
    price_cny_per_t: float | None = None
    price_includes_freight: bool = False
    yield_pct: float | None = None
    inspection_required: bool = False
    preprocessing: str = ""
    transport_mode: str = ""
    factor_id: str = ""
    distance_km: float | None = None
    evidence_source: str = ""
    evidence_date: str = ""
    evidence_attachment: str = ""
    source: str = ""
    external_id: str = ""  # 可选外部权威编号（如统一社会信用代码/权威批次编码，v13 数据模型）


@dataclass
class MaterialDemand:
    demand_id: str = ""
    buyer_name: str = ""
    target_material_code: str = ""
    application: str = ""
    required_mass_t: float | None = None
    min_recycled_content_pct: float | None = None
    mfi_min: float | None = None
    mfi_max: float | None = None
    max_moisture_pct: float | None = None
    max_ash_pct: float | None = None
    accepted_form: str = ""
    accepted_color: str = ""
    required_from: str = ""
    due_date: str = ""
    destination: str = ""
    baseline_virgin_price_cny_per_t: float | None = None
    max_distance_km: float | None = None
    evidence_source: str = ""
    source: str = ""


# —— 加载器（含导入前体检，） ——


def _parse_bool(value):
    text = _clean(value)
    if not text:
        return False
    return text.lower() in ("1", "true", "yes", "y", "是", "含", "含运费")


def load_supplies_csv(path, strict=True):
    """读取物料供给 CSV。结构错误（缺列/ID 缺失重复/非法数值）在 strict 下整体拒绝
    并附行级问题清单；业务待补证（缺报告/缺价）原样保留缺失，不静默修正。"""
    required = SUPPLY_REQUIRED
    stats = {"read": 0, "accepted": 0, "rejected": 0, "warnings": [], "issues": []}
    supplies, seen, rows = [], set(), []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError("物料供给 CSV 缺少字段：" + "、".join(sorted(missing)))
        for line_no, row in enumerate(reader, 2):
            stats["read"] += 1
            rows.append({"row_no": line_no, "data": row})
    issues = preflight_supply_rows(rows, Path(path).name)
    structural, business = split_issues(issues)
    stats["issues"] = issues
    stats["business_issues"] = business
    if strict and structural:
        raise ValueError("供给 CSV 存在结构错误，拒绝导入：\n" + format_issues(structural)
                         + f"\n共 {len(structural)} 项（业务待补证 {len(business)} 项未在此列出）")
    for item in rows:
        line_no, row = item["row_no"], item["data"]
        supply_id = _clean(row.get("supply_id"))
        if not supply_id or supply_id in seen:
            continue  # 结构错误已在体检中报告；strict 下根本走不到这里
        seen.add(supply_id)
        numeric = _parse_numerics(row, ("mfi_g_10min", "moisture_pct", "ash_pct", "recycled_content_pct",
                                        "available_mass_t", "price_cny_per_t", "distance_km", "yield_pct"),
                                  line_no, stats)
        if numeric is None:
            stats["rejected"] += 1
            continue
        supplies.append(MaterialSupply(
            supply_id=supply_id,
            supplier_id=_clean(row.get("supplier_id")),
            supplier_name=_clean(row.get("supplier_name")),
            material_code=_clean(row.get("material_code")),
            material_name_raw=_clean(row.get("material_name_raw")),
            polymer_type=_clean(row.get("polymer_type")),
            form=_clean(row.get("form")),
            color=_clean(row.get("color")),
            grade=_clean(row.get("grade")),
            mfi_g_10min=numeric["mfi_g_10min"],
            moisture_pct=numeric["moisture_pct"],
            ash_pct=numeric["ash_pct"],
            recycled_content_pct=numeric["recycled_content_pct"],
            available_mass_t=numeric["available_mass_t"],
            available_from=_clean(row.get("available_from")),
            available_to=_clean(row.get("available_to")),
            origin=_clean(row.get("origin")),
            price_cny_per_t=numeric["price_cny_per_t"],
            price_includes_freight=_parse_bool(row.get("price_includes_freight")),
            yield_pct=numeric["yield_pct"],
            inspection_required=_parse_bool(row.get("inspection_required")),
            preprocessing=_clean(row.get("preprocessing")),
            transport_mode=_clean(row.get("transport_mode")),
            factor_id=_clean(row.get("factor_id")),
            distance_km=numeric["distance_km"],
            evidence_source=_clean(row.get("evidence_source")),
            evidence_date=_clean(row.get("evidence_date")),
            evidence_attachment=_clean(row.get("evidence_attachment")),
            source=_clean(row.get("source")),
            external_id=_clean(row.get("external_id")),
        ))
        stats["accepted"] += 1
    return supplies, stats


def load_demands_csv(path, strict=True):
    """读取物料需求 CSV。多条需求必须由调用方显式选择（run_material 的 demand_id 参数）。"""
    required = DEMAND_REQUIRED
    stats = {"read": 0, "accepted": 0, "rejected": 0, "warnings": [], "issues": []}
    demands, seen, rows = [], set(), []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError("物料需求 CSV 缺少字段：" + "、".join(sorted(missing)))
        for line_no, row in enumerate(reader, 2):
            stats["read"] += 1
            rows.append({"row_no": line_no, "data": row})
    issues = preflight_demand_rows(rows, Path(path).name)
    structural, business = split_issues(issues)
    stats["issues"] = issues
    stats["business_issues"] = business
    if strict and structural:
        raise ValueError("需求 CSV 存在结构错误，拒绝导入：\n" + format_issues(structural)
                         + f"\n共 {len(structural)} 项（业务待补证 {len(business)} 项未在此列出）")
    for item in rows:
        line_no, row = item["row_no"], item["data"]
        demand_id = _clean(row.get("demand_id"))
        if not demand_id or demand_id in seen:
            continue
        seen.add(demand_id)
        numeric = _parse_numerics(
            row, ("required_mass_t", "min_recycled_content_pct", "mfi_min", "mfi_max",
                  "max_moisture_pct", "max_ash_pct", "baseline_virgin_price_cny_per_t",
                  "max_distance_km"),
            line_no, stats)
        if numeric is None:
            stats["rejected"] += 1
            continue
        demands.append(MaterialDemand(
            demand_id=demand_id,
            buyer_name=_clean(row.get("buyer_name")),
            target_material_code=_clean(row.get("target_material_code")),
            application=_clean(row.get("application")),
            required_mass_t=numeric["required_mass_t"],
            min_recycled_content_pct=numeric["min_recycled_content_pct"],
            mfi_min=numeric["mfi_min"],
            mfi_max=numeric["mfi_max"],
            max_moisture_pct=numeric["max_moisture_pct"],
            max_ash_pct=numeric["max_ash_pct"],
            accepted_form=_clean(row.get("accepted_form")),
            accepted_color=_clean(row.get("accepted_color")),
            required_from=_clean(row.get("required_from")),
            due_date=_clean(row.get("due_date")),
            destination=_clean(row.get("destination")),
            baseline_virgin_price_cny_per_t=numeric["baseline_virgin_price_cny_per_t"],
            max_distance_km=numeric["max_distance_km"],
            evidence_source=_clean(row.get("evidence_source")),
            source=_clean(row.get("source")),
        ))
        stats["accepted"] += 1
    return demands, stats


def load_material_factors(path=None):
    """加载材料因子表并校验。返回 {"factors": {id: dict}, "baseline_transport_distance_km": float,
    "accounting_convention": {...}, "cost_assumptions": {...}, "sensitivity": {...}}。"""
    if path is None:
        path = config.CONFIG_DIR / "material_emission_factors.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    problems = validate_material_factors(data)
    if problems:
        raise ValueError("材料因子配置无效：" + "；".join(problems))
    factors = {}
    for item in data.get("factors", []):
        factors[str(item["factor_id"])] = item
    baseline_distance = float(data.get("baseline_transport_distance_km", 100.0))
    sensitivity = data.get("sensitivity") or {}
    convention = data.get("accounting_convention") or {}
    cost_assumptions = data.get("cost_assumptions") or {}
    return {"factors": factors, "baseline_transport_distance_km": baseline_distance,
            "sensitivity": sensitivity, "accounting_convention": convention,
            "cost_assumptions": cost_assumptions}


def validate_material_factors(data):
    """因子治理校验：ID 唯一、字段完整、数值有限非负、角色/阶段/边界/运输方式兼容。"""
    from .ledger import ROLE_BY_STAGE
    problems = []
    seen = set()
    factors = data.get("factors") or []
    if not factors:
        problems.append("factors 为空")
    for item in factors:
        factor_id = str(item.get("factor_id", ""))
        if not factor_id:
            problems.append("存在缺少 factor_id 的因子")
            continue
        if factor_id in seen:
            problems.append(f"factor_id 重复：{factor_id}")
        seen.add(factor_id)
        for field in ("stage", "material_code", "value", "unit", "boundary", "region",
                      "year", "source_name", "version"):
            if item.get(field) in (None, ""):
                problems.append(f"因子 {factor_id} 缺少字段 {field}")
        value = item.get("value")
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            problems.append(f"因子 {factor_id} 的 value 必须是非负有限数值")
        if item.get("grade") not in ("演示假设", "权威"):
            problems.append(f"因子 {factor_id} 的 grade 必须是「演示假设」或「权威」")
        # v14 任务 1：可选不确定性区间（成对出现，0 ≤ value_min ≤ value ≤ value_max）
        value_min, value_max = item.get("value_min"), item.get("value_max")
        if (value_min is None) != (value_max is None):
            problems.append(f"因子 {factor_id} 的 value_min/value_max 必须成对出现")
        elif value_min is not None:
            for name, bound in (("value_min", value_min), ("value_max", value_max)):
                if not isinstance(bound, (int, float)) or not math.isfinite(bound) or bound < 0:
                    problems.append(f"因子 {factor_id} 的 {name} 必须是非负有限数值")
            if (isinstance(value_min, (int, float)) and math.isfinite(value_min)
                    and isinstance(value_max, (int, float)) and math.isfinite(value_max)
                    and isinstance(value, (int, float)) and math.isfinite(value)
                    and not (value_min <= value <= value_max)):
                problems.append(f"因子 {factor_id} 的 value 必须落在 [value_min, value_max] 内")
        stage = item.get("stage")
        role = item.get("role") or ROLE_BY_STAGE.get(stage or "")
        if not role:
            problems.append(f"因子 {factor_id} 的 stage={stage} 无法推导角色 role")
        if role not in ("baseline_material", "project_material", "transport"):
            problems.append(f"因子 {factor_id} 的 role={role} 非法")
        if stage == "transport":
            applies = item.get("applies_to") or []
            if not applies:
                problems.append(f"运输因子 {factor_id} 缺少 applies_to")
    base_distance = data.get("baseline_transport_distance_km")
    if base_distance is None or not isinstance(base_distance, (int, float)) \
            or not math.isfinite(base_distance) or base_distance < 0:
        problems.append("baseline_transport_distance_km 必须是非负有限数值")
    cost = data.get("cost_assumptions") or {}
    for key in ("freight_rate_cny_per_tkm", "handling_fee_cny_per_t",
                "inspection_fee_per_batch_cny", "default_yield"):
        if key in cost:
            v = cost[key]
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                problems.append(f"cost_assumptions.{key} 必须是非负有限数值")
    return problems


def _parse_numerics(row, fields, line_no, stats):
    numeric = {}
    for name in fields:
        text = _clean(row.get(name))
        if not text:
            numeric[name] = None
            continue
        try:
            numeric[name] = float(text.replace(",", ""))
        except ValueError:
            stats["warnings"].append(f"第 {line_no} 行 {name} 不是数字，已剔除")
            return None
        if not math.isfinite(numeric[name]):
            stats["warnings"].append(f"第 {line_no} 行 {name} 非有限数值，已剔除")
            return None
    return numeric


def _clean(value):
    return str(value or "").strip()


# —— 替代性硬门槛（三态） ——


def match_gates(demand, supply):
    """供给 vs 需求的替代性硬门槛。返回 (gates, elimination_reasons, pending_reasons)。

    数量门槛按「单供资格」判定：不足需求的批次不能单供，但注明可进入组合。
    """
    gates, elimination, pending = [], [], []

    def unknown_gate(name, detail):
        gates.append({"name": name, "status": "unknown", "detail": detail})
        pending.append(f"{name}：{detail}")

    # 材料类别
    if not demand.target_material_code:
        unknown_gate("材料类别", "需求方未提供目标材料代码")
    elif not supply.material_code and not supply.polymer_type:
        unknown_gate("材料类别", "供给未提供材料代码")
    elif demand.target_material_code not in (supply.material_code or "", supply.polymer_type or ""):
        gates.append({"name": "材料类别", "status": "fail",
                      "detail": f"供给材料 {supply.material_code or supply.polymer_type} 与需求 {demand.target_material_code} 不符"})
        elimination.append("材料类别不符")
    else:
        gates.append({"name": "材料类别", "status": "pass",
                      "detail": f"材料类别匹配（{demand.target_material_code}）"})

    # 形态
    forms = [x for x in (demand.accepted_form or "").replace(";", "、").split("、") if x]
    if not forms:
        unknown_gate("形态", "需求方未提供形态要求")
    elif not supply.form:
        unknown_gate("形态", "供给未提供形态")
    elif supply.form not in forms:
        gates.append({"name": "形态", "status": "fail",
                      "detail": f"供给形态 {supply.form} 不在 {demand.accepted_form} 内"})
        elimination.append("形态不符")
    else:
        gates.append({"name": "形态", "status": "pass", "detail": f"形态匹配（{supply.form}）"})

    # 颜色
    colors = [x for x in (demand.accepted_color or "").replace(";", "、").split("、") if x]
    if not colors:
        unknown_gate("颜色", "需求方未提供颜色要求")
    elif not supply.color:
        unknown_gate("颜色", "供给未提供颜色")
    elif supply.color not in colors:
        gates.append({"name": "颜色", "status": "fail",
                      "detail": f"供给颜色 {supply.color} 不在 {demand.accepted_color} 内"})
        elimination.append("颜色不符")
    else:
        gates.append({"name": "颜色", "status": "pass", "detail": f"颜色匹配（{supply.color}）"})

    # 熔指范围
    if demand.mfi_min is None or demand.mfi_max is None:
        unknown_gate("熔指", "需求方未提供熔指范围")
    elif supply.mfi_g_10min is None:
        unknown_gate("熔指", "供给未提供熔指")
    elif not (demand.mfi_min <= supply.mfi_g_10min <= demand.mfi_max):
        gates.append({"name": "熔指", "status": "fail",
                      "detail": f"供给熔指 {supply.mfi_g_10min:g} g/10min 超出 {demand.mfi_min:g}~{demand.mfi_max:g}"})
        elimination.append("熔指不满足")
    else:
        gates.append({"name": "熔指", "status": "pass", "detail": f"熔指 {supply.mfi_g_10min:g} g/10min 在范围内"})

    # 水分
    if demand.max_moisture_pct is None:
        unknown_gate("水分", "需求方未提供水分上限")
    elif supply.moisture_pct is None:
        unknown_gate("水分", "供给未提供水分")
    elif supply.moisture_pct > demand.max_moisture_pct:
        gates.append({"name": "水分", "status": "fail",
                      "detail": f"供给水分 {supply.moisture_pct:g}% 超过上限 {demand.max_moisture_pct:g}%"})
        elimination.append("水分超标")
    else:
        gates.append({"name": "水分", "status": "pass", "detail": f"水分 {supply.moisture_pct:g}% ≤ {demand.max_moisture_pct:g}%"})

    # 灰分
    if demand.max_ash_pct is None:
        unknown_gate("灰分", "需求方未提供灰分上限")
    elif supply.ash_pct is None:
        unknown_gate("灰分", "供给未提供灰分")
    elif supply.ash_pct > demand.max_ash_pct:
        gates.append({"name": "灰分", "status": "fail",
                      "detail": f"供给灰分 {supply.ash_pct:g}% 超过上限 {demand.max_ash_pct:g}%"})
        elimination.append("灰分超标")
    else:
        gates.append({"name": "灰分", "status": "pass", "detail": f"灰分 {supply.ash_pct:g}% ≤ {demand.max_ash_pct:g}%"})

    # 再生含量
    if demand.min_recycled_content_pct is None:
        unknown_gate("再生含量", "需求方未提供再生含量下限")
    elif supply.recycled_content_pct is None:
        unknown_gate("再生含量", "供给未提供再生含量")
    elif not supply.evidence_source:
        unknown_gate("再生含量", "数值无检测报告支撑，需补送样检测")
    elif supply.recycled_content_pct < demand.min_recycled_content_pct:
        gates.append({"name": "再生含量", "status": "fail",
                      "detail": f"供给再生含量 {supply.recycled_content_pct:g}% 低于需求下限 {demand.min_recycled_content_pct:g}%"})
        elimination.append("再生含量不达标")
    else:
        gates.append({"name": "再生含量", "status": "pass",
                      "detail": f"再生含量 {supply.recycled_content_pct:g}% ≥ {demand.min_recycled_content_pct:g}%"})

    # 数量：不足需求的批次不被硬淘汰（可与其他批次组合），但无单供资格；
    # 单供资格在方案构建层强制（build_options 只收一家具备全部产能的供给）
    if demand.required_mass_t is None or supply.available_mass_t is None:
        unknown_gate("数量", "数量数据缺失")
    elif supply.available_mass_t < demand.required_mass_t:
        gates.append({"name": "数量", "status": "pass",
                      "detail": f"可供 {supply.available_mass_t:g} t 小于需求 {demand.required_mass_t:g} t"
                                f"（单供不足，可与其他批次组合）"})
    else:
        gates.append({"name": "数量", "status": "pass",
                      "detail": f"可供 {supply.available_mass_t:g} t ≥ 需求 {demand.required_mass_t:g} t"})

    # 时窗
    if not (demand.required_from or demand.due_date):
        unknown_gate("时窗", "需求方未提供时窗")
    elif not (supply.available_from or supply.available_to):
        unknown_gate("时窗", "供给未提供可用时窗")
    else:
        overlap = (not supply.available_to or not demand.required_from
                   or supply.available_to >= demand.required_from) and \
                  (not supply.available_from or not demand.due_date
                   or supply.available_from <= demand.due_date)
        if overlap:
            gates.append({"name": "时窗", "status": "pass", "detail": "可用时窗与需求时窗有重叠"})
        else:
            gates.append({"name": "时窗", "status": "fail", "detail": "可用时窗与需求时窗无重叠"})
            elimination.append("时窗不符")

    # 距离
    if demand.max_distance_km is None:
        unknown_gate("距离", "需求方未提供距离上限")
    elif supply.distance_km is None:
        unknown_gate("距离", "供给未提供距离")
    elif supply.distance_km > demand.max_distance_km:
        gates.append({"name": "距离", "status": "fail",
                      "detail": f"供给距离 {supply.distance_km:g} km 超过上限 {demand.max_distance_km:g} km"})
        elimination.append("距离超限")
    else:
        gates.append({"name": "距离", "status": "pass",
                      "detail": f"距离 {supply.distance_km:g} km ≤ {demand.max_distance_km:g} km"})

    return gates, elimination, pending


def supply_state(demand, supply):
    """三态：fail → ineligible；unknown → pending_evidence；全过 → eligible。

    单供资格（数量不足）单独处理：其余门槛全过的不足批次标记 single_eligible=False。
    """
    gates, elimination, pending = match_gates(demand, supply)
    if elimination:
        state = "ineligible"
    elif pending:
        state = "pending_evidence"
    else:
        state = "eligible"
    return {"supply_id": supply.supply_id, "supplier_name": supply.supplier_name,
            "state": state, "gates": gates,
            "elimination_reasons": elimination, "pending_reasons": pending}


# —— 方案身份（） ——


# option_id 缓存的淘汰复合操作（pop(next(iter()))）非原子：
# 并行场景下写入与淘汰必须持锁（读路径免锁）。
_OPTION_ID_CACHE_LOCK = threading.Lock()
_option_id_cache = {}

def option_id(kind, demand, allocation=None):
    """稳定 option_id：场景身份（需求 ID+规格指纹）+ 类型 + 规范化分配量 + 算法版本。

    显示名称不是主键；同标签不同分配产生不同 ID；同一分配在不同需求场景下也不同。
    纯函数：按 (kind, 需求对象身份, 分配) 进程级缓存，数值逐位一致。
    v13 修正：缓存值持有 demand 引用（键含 id(demand)，不持引用会在对象回收、
    id 被新对象复用后错误命中），命中值与未缓存计算逐位一致。
    """
    key = (kind, id(demand),
           tuple(sorted((allocation or {}).items())))
    cached = _option_id_cache.get(key)
    if cached is not None:
        return cached[0]
    result = _option_id(kind, demand, allocation)
    with _OPTION_ID_CACHE_LOCK:
        _option_id_cache[key] = (result, demand)  # 持有 demand：防 id 复用误命中
        if len(_option_id_cache) > 32768:
            _option_id_cache.pop(next(iter(_option_id_cache)))
    return result


def _option_id(kind, demand, allocation=None):
    """option_id 的无缓存实现（缓存壳见上）。"""
    demand_fingerprint = hashlib.sha256(json.dumps(
        {k: v for k, v in asdict(demand).items()
         if k not in ("buyer_name", "application", "destination", "evidence_source", "source")},
        ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    if kind == "baseline":
        return f"opt-base-{demand_fingerprint}"
    canonical = json.dumps(
        {"scenario": demand_fingerprint, "kind": kind,
         "allocation": [[sid, round(mass, 4)] for sid, mass in sorted((allocation or {}).items())]},
        ensure_ascii=False, sort_keys=True)
    return "opt-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def allocation_label(allocation, supplies_by_id):
    """把 {supply_id: mass} 转成「供应商名 质量 t + …」展示串（名称只用于显示）。"""
    parts = []
    for supply_id in sorted(allocation, key=lambda sid: supplies_by_id.get(sid, type("X", (), {"supplier_name": sid})()).supplier_name):
        supply = supplies_by_id.get(supply_id)
        name = supply.supplier_name if supply else supply_id
        parts.append(f"{name} {allocation[supply_id]:g}t")
    return " + ".join(parts)


# —— 净减排（v8 口径：账本） ——


def net_emissions(demand, supply, mass_t, factor_data, baseline_distance_km=None):
    """单一供给的净减排核算（功能单位：交付 1 t 合格颗粒）。委托 ledger 显式解析因子。"""
    return net_emissions_ledger(demand, supply, mass_t, factor_data,
                                baseline_distance_km=baseline_distance_km)


# —— 组合分配 ——


def _parallel_workers(hint):
    """场景级并行化的 worker 数（v19 任务 1，调度层改动不改计算）。

    环境变量 SUPPLYLENS_PARALLEL_WORKERS 可显式覆盖（=1 即串行路径，供等价测试
    与调试）；缺省取 min(并行单元数 hint, CPU 逻辑核, 8)——单元间互相独立，
    每单元输出只依赖输入，聚合按固定顺序，输出与串行逐位一致（红线 3）。"""
    override = int(os.environ.get("SUPPLYLENS_PARALLEL_WORKERS", "0") or 0)
    if override > 0:
        return max(1, override)
    return max(1, min(hint, os.cpu_count() or 2, 8))


def simulate_supply_mix(demand, supplies, factor_data, step_t=0.5, max_plans=20,
                        partial_capacity=True):
    """把需求质量在 eligible 供给间按离散步长完整枚举，返回非支配方案集。

    单供资格要求一家具备全部产能；组合允许部分产能批次进入（partial_capacity=True）。
    任何正分配供给缺价/缺因子 → 该方案标记不可计算，不进成本维度 Pareto。
    方案以 supply_id 为分配键（同供应商多批次独立），携带 option_id。
    返回：frontier=完整非支配集，plans=展示切片（max_plans），feasible_count/
    frontier_count/returned_count/is_complete/truncated/note。
    """
    required = demand.required_mass_t
    if required is None:
        return None
    if not (isinstance(step_t, (int, float)) and math.isfinite(step_t) and step_t > 0):
        return {"error": "step_t 必须为正有限数（t）"}
    if not isinstance(max_plans, (int, type(None))) or (max_plans is not None and max_plans <= 0):
        return {"error": "max_plans 必须为正整数或 None（返回完整前沿）"}
    steps = required / step_t
    if abs(steps - round(steps)) > TOL:
        return {"error": f"需求量 {required:g} t 无法按步长 {step_t:g} t 精确分配（非整数刻度）"}
    total_steps = int(round(steps))
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    if not partial_capacity:
        eligible = [s for s in eligible if (s.available_mass_t or 0) >= required]
    if len(eligible) < 2:
        return {"note": "eligible 供给不足两个，无组合方案", "plans": [], "frontier": [],
                "feasible_count": 0, "frontier_count": 0, "returned_count": 0,
                "is_complete": True, "truncated": False}
    combinations = math.comb(total_steps + len(eligible) - 1, len(eligible) - 1)
    if combinations > MAX_COMBINATIONS:
        # 计算时间上限：优先尝试 OR-Tools CP-SAT 全局最优（v13）；未安装或求解
        # 不可行/未证实最优时回退贪心可行解与真实最优性状态，不静默截断、不冒充全局最优。
        solved = _solver_mix(demand, eligible, factor_data, step_t, max_plans)
        if solved is not None:
            return solved
        fallback = _greedy_feasible_plan(demand, eligible, factor_data)
        suggested_step = _suggested_divisible_step(required)
        suggestion = (f"建议步长 {suggested_step:g} t（能整除需求量 {required:g} t）"
                      if suggested_step is not None else "请选择能整除需求量的更大步长")
        return {"error": f"组合规模约 {combinations} 种超过上限（{MAX_COMBINATIONS}），"
                         f"请增大步长（当前 {step_t:g} t，{suggestion}）或引入优化器",
                "solver_status": "feasible",
                "optimality": "未证实最优（超限回退：返回贪心可行解）",
                "best_effort_plan": fallback,
                "feasible_count": None, "frontier_count": None,
                "returned_count": 1, "is_complete": False, "truncated": False,
                "plans": [fallback] if fallback else [], "frontier": []}
    supplies_by_id = {s.supply_id: s for s in eligible}
    feasible = []
    for steps_alloc in _compositions(total_steps, len(eligible)):
        masses = [k * step_t for k in steps_alloc]
        if any(masses[i] > (eligible[i].available_mass_t or 0) + TOL
               for i in range(len(eligible))):
            continue
        feasible.append(_mix_plan(demand, eligible, masses, factor_data))
    frontier, failed = _nondominated_mix(feasible)
    frontier.sort(key=lambda p: (p["_cost_full"] if p["_cost_full"] is not None else 1e18,
                                 -(p["_net_full"] if p["_net_full"] is not None else -1e18),
                                 p["_share_full"] or 1.0))
    returned = frontier if max_plans is None else frontier[:max_plans]
    return {
        "note": f"在 {len(eligible)} 个 eligible 供给间按步长 {step_t:g} t 完整枚举，"
                f"满足规格与数量约束的方案 {len(feasible)} 个，其中非支配方案 {len(frontier)} 个",
        "feasible_count": len(feasible),
        "frontier_count": len(frontier),
        "returned_count": len(returned),
        "is_complete": True,
        "truncated": len(frontier) > (max_plans or len(frontier)),
        "max_plans": max_plans,
        "step_t": step_t,
        "supplies_by_id": {sid: s.supplier_name for sid, s in supplies_by_id.items()},
        "sort_rule": "前沿按总成本升序、保守净减排降序、订单最大份额升序稳定排序",
        "plans": returned,
        "frontier": frontier,
        "plans_unpriced": failed,
    }


def _suggested_divisible_step(required):
    """建议步长必须能整除需求量（候选从密到疏，均失败返回 None）。"""
    candidates = [required / 20.0, required / 19.0, required / 18.0,
                  5.0, 2.5, 2.0, 1.0, 0.5]
    for step in candidates:
        if step <= 0 or not math.isfinite(step):
            continue
        steps = required / step
        if abs(steps - round(steps)) <= TOL:
            return round(step, 6)
    return None


def _greedy_feasible_plan(demand, eligible, factor_data):
    """组合枚举超限时的贪心可行解（最低价优先填满需求），明确标注未证实最优。"""
    required = demand.required_mass_t or 0.0
    priced = [s for s in eligible if s.price_cny_per_t is not None]
    allocation, remaining = {}, required
    for supply in sorted(priced, key=lambda s: (s.price_cny_per_t, s.supply_id)):
        if remaining <= TOL:
            break
        take = min(remaining, supply.available_mass_t or 0.0)
        if take <= TOL:
            continue
        allocation[supply.supply_id] = round(take, 4)
        remaining = round(remaining - take, 4)
    if remaining > TOL:
        return None  # 无可行解：输出冲突/缺口解释由调用方 error 文案承担
    supplies_by_id = {s.supply_id: s for s in eligible}
    ledger = project_cost_ledger(demand, allocation, supplies_by_id,
                                 factor_data.get("cost_assumptions") or {})
    net_total = 0.0
    problems = list(ledger.problems)
    for supply_id, mass in allocation.items():
        result = net_emissions(demand, supplies_by_id[supply_id], mass, factor_data)
        if result["status"] == "可计算":
            net_total += result["net_avoided_kgco2e"]
        else:
            problems.append(f"{supplies_by_id[supply_id].supplier_name}：{'；'.join(result['problems'])}")
    order_fractions = {sid: mass / required for sid, mass in allocation.items()}
    computable = not problems and ledger.total is not None
    return {
        "allocation": allocation,
        "allocation_labels": {sid: supplies_by_id[sid].supplier_name for sid in allocation},
        "option_id": option_id("combo", demand, allocation),
        "total_cost_cny": round(ledger.total, 2) if ledger.total is not None else None,
        "cost_ledger": ledger.to_dict(),
        "net_avoided_kgco2e": round(net_total, 2) if not problems else None,
        "recycled_mass_t": None,
        "order_max_share": round(max(order_fractions.values()), 4) if order_fractions else 0.0,
        # v13 修正：补齐全精度内部字段（与 _mix_plan 方案同构）。此前缺 _cost_full，
        # build_options 生成的回退组合选项会带 None，与其它选项一起进 greedy_report
        # 的 min() 时触发 TypeError（未安装 ortools 的超限任务 + 有价单供即崩溃）。
        "_cost_full": ledger.total if ledger.total is not None else None,
        "_net_full": net_total if not problems else None,
        "_share_full": max(order_fractions.values()) if order_fractions else 0.0,
        "computable": computable,
        "problems": problems,
        "note": "贪心可行解（最低价优先，超限回退）：未证实全局最优，仅作可行解参考",
    }


def _compositions(total, parts):
    def rec(remaining, index, current):
        if index == parts - 1:
            yield current + [remaining]
            return
        for k in range(remaining + 1):
            yield from rec(remaining - k, index + 1, current + [k])
    yield from rec(int(total), 0, [])


# —— OR-Tools CP-SAT 求解路径（v13 任务 1） ——
#
# 背景：组合枚举超过 MAX_COMBINATIONS 时，v12 只能返回贪心可行解（solver_status=feasible）。
# 安装 OR-Tools 后，超限场景改走 CP-SAT，求「最低到厂成本 / 最高保守净减排 / 最低订单集中度」
# 三个锚点各自字典序的全局最优，solver_status/optimality 如实标注；未安装或求解不可行/未证实
# 最优时回退既有贪心路径，行为与 v12 完全一致。文档不夸称：OR-Tools 仅在求解路径实际生效时
# 才出现在结果与基准中。
#
# 与穷举互验的等价性依据（tests/test_v13_solver.py，≥10 组随机案例）：
# 1) 成本与净减排对分配量均为线性函数（成本账本各行项目、净减排基准-项目差都正比质量，
#    一次性检测费不进 total），故取 mass=step_t 的真实账本值作「每步」系数；
# 2) 系数定点整数化（×CP_SCALE 取整，单系数舍入误差 ≤5e-7），整数模型最优即真实最优；
#    求解后用与穷举完全相同的 _mix_plan 复算真实账本并校验量化偏差，偏差超限回退贪心；
# 3) 单供分配不属组合方案（_mix_plan 判不可计算），模型以「至少两家供给」约束对齐；
# 4) 穷举前沿排序键（成本升序、净减排降序、份额升序）即成本锚点的字典序，决胜规则一致。
# 求解器确定性：num_workers=1 + 固定随机种子 + 时间上限（小模型毫秒级，不会触限）。

CP_SCALE = 10 ** 6
CP_MAX_TIME_SECONDS = 30.0
FRONTIER_CAP = 500  # v14 完整前沿点数上限：触顶如实截断（is_complete=False），不静默


def _solver_debug(message):
    """求解路径降级观测开关（MATERIAL_SOLVER_DEBUG=1 时输出到 stderr，供诊断）。"""
    if os.environ.get("MATERIAL_SOLVER_DEBUG"):
        import sys
        print(f"[solver-debug] {message}", file=sys.stderr)


def _import_cp_sat():
    """延迟导入 OR-Tools CP-SAT；未安装返回 None（引擎其余功能不受影响）。"""
    try:
        from ortools.sat.python import cp_model
        return cp_model
    except Exception:
        return None


def _ortools_version():
    try:
        from importlib.metadata import version
        return version("ortools")
    except Exception:
        return ""


def _to_scaled_int(value, scale=CP_SCALE):
    """账本系数定点整数化（供互验与量化误差控制；单系数舍入误差 ≤ 0.5/scale）。"""
    return int(round(value * scale))


def _solver_unit_coefficients(demand, eligible, factor_data, step_t):
    """每个供给「每步长」的真实账本系数（成本, 净减排全精度）与不可计算批次清单。

    用真实 ledger/net 账本按 mass=step_t 取值（线性函数 → 每步系数），
    不在求解器里重新实现任何核算公式，避免两套口径漂移。
    """
    units, excluded = {}, []
    supplies_by_id = {s.supply_id: s for s in eligible}
    cost_assumptions = factor_data.get("cost_assumptions") or {}
    for supply in eligible:
        net = net_emissions(demand, supply, step_t, factor_data)
        if net.get("status") != "可计算" or net.get("_net_full") is None:
            excluded.append({"supply_id": supply.supply_id,
                             "reason": "净减排不可计算（" + "；".join(net.get("problems") or ["未知"]) + "）"})
            continue
        ledger = project_cost_ledger(demand, {supply.supply_id: step_t},
                                     supplies_by_id, cost_assumptions)
        if ledger.total is None:
            excluded.append({"supply_id": supply.supply_id,
                             "reason": "成本不可计算（" + "；".join(ledger.problems or ["缺价"]) + "）"})
            continue
        units[supply.supply_id] = (ledger.total, net["_net_full"])
    return units, excluded


def _solver_mix(demand, eligible, factor_data, step_t, max_plans):
    r"""组合超限时的 CP-SAT 完整前沿求解路径（v14 任务 2，替代 v13 的三锚点）。

    数学事实（两侧均有证明，属性测试锁定）：
    设 F_m = 可行集 {max-k ≤ m} 上的二维 (cost, net) 精确非支配前沿（整数系数），
    则三维完整前沿（cost/net/share，share = max-k/steps 由 max-k 唯一决定）
    = ⋃_m {F_m 中 max-k 恰等于 m 的点}。
    - 方向一（无遗漏）：三维非支配点 p（max-k=m）若在 F_m 内被二维支配，支配者
      max-k ≤ m；若其 max-k < m 则份额更小、二维更优 → 三维支配 p，矛盾；若 = m
      则份额相等且二维更优 → 三维支配 p，矛盾。故 p ∈ F_m，且不在 F_{m−1}
      （否则 (c,n) 在更小可行集可达，与差集矛盾）。
    - 方向二（无多余）：F_m \ F_{m−1} 中的点 (c,n) 的最小 max-k 必为 m（若 < m 则
      (c,n) ∈ F_{m−1}，矛盾）；对三维支配者 d：其二维投影在 {max-k ≤ m} 集合内支配
      该点 → 与二维非支配矛盾。

    实现：m 自 m_min 起扫描；每阶段先做可行性解（max-k = m 是否可达），再用二分
    递归（Aneja–Nair / ε-约束式分割）枚举该阶段二维前沿点集；新点以「最小 max-k」
    收尾（即最小订单集中度的规范代表）；最终方案用与穷举完全相同的 _mix_plan 出账。
    前沿点数超过 FRONTIER_CAP 时如实标注截断（is_complete=False），不静默、不冒充。
    求解器确定性：num_workers=1 + 固定随机种子；任何求解未证实最优 → 如实降级。
    """
    cp_model = _import_cp_sat()
    if cp_model is None:
        return None
    required = demand.required_mass_t or 0.0
    total_steps = int(round(required / step_t))
    units, excluded = _solver_unit_coefficients(demand, eligible, factor_data, step_t)
    cost_c, net_c, caps, order = {}, {}, {}, []
    for sid in sorted(units):
        supply = next(s for s in eligible if s.supply_id == sid)
        cap_steps = math.floor(((supply.available_mass_t or 0.0) / step_t) + 1e-9)
        if cap_steps <= 0:
            excluded.append({"supply_id": sid, "reason": "可用产能不足一个步长"})
            continue
        cost_c[sid] = _to_scaled_int(units[sid][0])
        net_c[sid] = _to_scaled_int(units[sid][1])
        caps[sid] = min(cap_steps, total_steps)
        order.append(sid)
    if len(order) < 2 or sum(caps.values()) < total_steps:
        return None  # 可计算供给不足两家或可计算产能填不满需求 → 交回既有路径处理

    def build_model(m, extra=None):
        """构造阶段模型：Σk=total、≥2 家、max-k ≤ m；extra 为追加 (cost/net, 方向, 界)。"""
        model = cp_model.CpModel()
        k, used = {}, {}
        for sid in order:
            k[sid] = model.NewIntVar(0, min(caps[sid], m), "k_" + sid)
            used[sid] = model.NewBoolVar("u_" + sid)
            model.Add(k[sid] >= 1).OnlyEnforceIf(used[sid])
            model.Add(k[sid] == 0).OnlyEnforceIf(used[sid].Not())
        model.Add(sum(k.values()) == total_steps)
        model.Add(sum(used.values()) >= 2)
        cost_expr = sum(cost_c[sid] * k[sid] for sid in order)
        net_expr = sum(net_c[sid] * k[sid] for sid in order)
        share = model.NewIntVar(0, total_steps, "max_share_steps")
        model.AddMaxEquality(share, [k[sid] for sid in order])
        for key, maximize, bound in (extra or []):
            expr = cost_expr if key == "cost" else net_expr
            if maximize:
                model.Add(expr >= bound)
            else:
                model.Add(expr <= bound)
        return model, k, cost_expr, net_expr, share

    def solve_lex(m, stages, extra=None):
        """字典序逐级求解；返回 (cost_int, net_int) 或 None（未证实最优不冒充）。

        stages 支持 "cost"/"net"/"share" 三键（share 恒为最小化方向语义，
        由 maximize 标志显式给出）。"""
        fixed = []
        for key, maximize in stages:
            model, k, cost_expr, net_expr, share = build_model(m, extra)
            expr = {"cost": cost_expr, "net": net_expr, "share": share}[key]
            for fkey, fmax, fval in fixed:
                fexpr = {"cost": cost_expr, "net": net_expr, "share": share}[fkey]
                if fmax:
                    model.Add(fexpr >= fval)
                else:
                    model.Add(fexpr <= fval)
            if maximize:
                model.Maximize(expr)
            else:
                model.Minimize(expr)
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.random_seed = 0
            solver.parameters.max_time_in_seconds = CP_MAX_TIME_SECONDS
            status = solver.Solve(model)
            if status != cp_model.OPTIMAL:
                return None
            val = {"cost": cost_expr, "net": net_expr, "share": share}[key]
            fixed.append((key, maximize, int(round(solver.Value(val)))))
        # 按语义返回 (cost_int, net_int)——不按 fixed 顺序（末级不一定是成本）
        cost_val = next(v for fkey, _, v in fixed if fkey == "cost")
        net_val = next(v for fkey, _, v in fixed if fkey == "net")
        return cost_val, net_val

    def solve_k_values(m, stages, extra=None):
        """同 solve_lex，但返回最终模型的 k 向量（确定性：seed/workers 固定）。"""
        fixed = []
        k_values = None
        for key, maximize in stages:
            model, k, cost_expr, net_expr, share = build_model(m, extra)
            expr = {"cost": cost_expr, "net": net_expr, "share": share}[key]
            for fkey, fmax, fval in fixed:
                fexpr = {"cost": cost_expr, "net": net_expr, "share": share}[fkey]
                if fmax:
                    model.Add(fexpr >= fval)
                else:
                    model.Add(fexpr <= fval)
            if maximize:
                model.Maximize(expr)
            else:
                model.Minimize(expr)
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.random_seed = 0
            solver.parameters.max_time_in_seconds = CP_MAX_TIME_SECONDS
            status = solver.Solve(model)
            if status != cp_model.OPTIMAL:
                return None
            val = {"cost": cost_expr, "net": net_expr, "share": share}[key]
            fixed.append((key, maximize, int(round(solver.Value(val)))))
            k_values = {sid: solver.Value(k[sid]) for sid in order}
        return k_values

    def stage_feasible(m):
        """是否存在 max-k 恰为 m 的可行分配（≥2 家、容量与需求约束内）。

        无该类分配时 F_m 与 F_{m−1} 完全相同，阶段可跳过。"""
        model, k, _, _, _ = build_model(m)
        top = model.NewIntVar(0, m, "max_k")
        model.AddMaxEquality(top, [k[sid] for sid in order])
        model.Add(top == m)
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.max_time_in_seconds = CP_MAX_TIME_SECONDS
        status = solver.Solve(model)
        if status == cp_model.INFEASIBLE:
            return False
        if status != cp_model.OPTIMAL:
            return True  # 未知按可达处理（后续求解会如实判定）
        return True

    def frontier_points_2d(m):
        """阶段 m 的二维 (cost, net) 精确非支配点集（直接后继搜索 / ε-约束分割）。

        A=（最小成本，同成本最大净减排），B=（最大净减排，同净减排最小成本）。
        A 的「直接后继」= 满足 成本 ≤ cB−1 且 净减排 ≥ nA+1 中成本最小者：
        - 净减排 ≤ nA 的点被 A 支配，成本 ≥ cB 的点被 B 支配，故后继若存在必在前沿上；
        - 后继与 A 之间无其他点（成本更小与净减排下界矛盾最小性）。
        逐个后继推进直至逼近 B；不依赖成本/净减排的相关性方向。"""
        point_a = solve_lex(m, [("cost", False), ("net", True)])
        if point_a is None:
            return None
        point_b = solve_lex(m, [("net", True), ("cost", False)])
        if point_b is None:
            return None
        points = {point_a, point_b}
        cursor = point_a
        while cursor != point_b:
            c_a, n_a = cursor
            c_b, n_b = point_b
            successor = solve_lex(m, [("cost", False), ("net", True)],
                                  extra=[("cost", False, c_b - 1),
                                         ("net", True, n_a + 1)])
            if successor is None:
                break  # cursor 与 B 之间无点，前沿枚举完成
            if successor in points:
                break  # 防御：不应发生（净减排严格递增），发生则停止避免死循环
            points.add(successor)
            cursor = successor
        return points

    def min_maxk_plan(c_int, n_int):
        """给定 (cost, net) 的规范代表：最小 max-k（最小订单集中度）。"""
        return solve_k_values(total_steps,
                              [("share", False), ("cost", False), ("net", True)],
                              extra=[("cost", False, c_int), ("net", True, n_int)])

    supplies_by_id = {s.supply_id: s for s in eligible}
    frontier_plans = []
    known_points = set()
    truncated = False
    degraded = False
    quant_tol = 0.5 * total_steps + 2.0  # 整数系数单步舍入 ≤0.5 单位，全方案累计有界

    def make_plan(c_int, n_int):
        """按 (cost, net) 整数点构建规范方案（最小 max-k 代表 + 真实账本复算校验）。"""
        k_values = min_maxk_plan(c_int, n_int)
        if k_values is None:
            _solver_debug(f"点({c_int},{n_int}) 最小 max-k 未证实最优")
            return None
        allocation = {sid: round(k_values[sid] * step_t, 4)
                      for sid in order if k_values[sid] > 0}
        masses = [allocation.get(s.supply_id, 0.0) for s in eligible]
        plan = _mix_plan(demand, eligible, masses, factor_data)
        cost_full = plan.get("_cost_full")
        if not plan.get("computable") or len(plan.get("allocation") or {}) < 2 \
                or cost_full is None \
                or abs(cost_full * CP_SCALE - c_int) > quant_tol \
                or abs(plan.get("_net_full", 0.0) * CP_SCALE - n_int) > quant_tol:
            _solver_debug(f"量化校验失败 plan_cost={cost_full} c_int={c_int} "
                          f"net={plan.get('_net_full')} n_int={n_int}")
            return None
        plan["note"] = (plan["note"]
                        + "；求解路径：OR-Tools CP-SAT 完整非支配前沿（ε-约束精确枚举）")
        return plan

    # —— 三锚点全局求解（恒精确，先于阶段扫描） ——
    # 截断只允许丢失前沿「中段」，三锚点（最低成本/最高净减排/最低集中度）必须
    # 全局精确：否则截断前沿的最低成本可能劣于贪心可行解，误导下游对照。
    for stages in ([("cost", False), ("net", True)],
                   [("net", True), ("cost", False)],
                   [("share", False), ("cost", False), ("net", True)]):
        point = solve_lex(total_steps, stages)
        if point is None:
            _solver_debug(f"锚点求解未证实最优：{stages}")
            degraded = True
            break
        if point in known_points:
            continue
        plan = make_plan(*point)
        if plan is None:
            degraded = True
            break
        known_points.add(point)
        frontier_plans.append(plan)
    if degraded:
        return None

    m_min = max(2, math.ceil(total_steps / len(order)))
    for m in range(m_min, total_steps):
        # ≥2 家正分配时 max-k ≤ total_steps−1
        if not stage_feasible(m):
            continue
        points_m = frontier_points_2d(m)
        if points_m is None:
            _solver_debug(f"stage m={m}: 二维前沿求解未证实最优")
            degraded = True
            break
        new_points = sorted(points_m - known_points)
        if not new_points:
            known_points |= points_m
            continue
        if len(frontier_plans) + len(new_points) > FRONTIER_CAP:
            truncated = True
            break
        known_points |= points_m
        for c_int, n_int in new_points:
            plan = make_plan(c_int, n_int)
            if plan is None:
                degraded = True
                break
            frontier_plans.append(plan)
        if degraded:
            break
    if not frontier_plans or degraded:
        _solver_debug(f"退出：frontier={len(frontier_plans)} degraded={degraded} "
                      f"truncated={truncated}")
        return None  # 求解失败/降级：交回既有贪心路径（v12 行为），不冒充
    frontier_plans.sort(key=lambda p: (p["_cost_full"] if p["_cost_full"] is not None else 1e18,
                                       -(p["_net_full"] if p["_net_full"] is not None else -1e18),
                                       p["_share_full"] or 1.0))
    returned = frontier_plans if max_plans is None else frontier_plans[:max_plans]
    notes = [f"组合规模超过穷举上限（{MAX_COMBINATIONS}），已用 OR-Tools CP-SAT "
             f"{'完整枚举' if not truncated else '部分枚举（触达 FRONTIER_CAP 上限）'}"
             f"三维非支配前沿（ε-约束精确分割，{len(frontier_plans)} 点）",
             "可行方案总数未知，feasible_count 如实置空"]
    if truncated:
        notes.append(f"前沿点数触达上限 FRONTIER_CAP={FRONTIER_CAP}，枚举提前停止，"
                     "is_complete=False（不冒充完整）")
    if degraded:
        notes.append("部分求解未证实最优/量化校验未过，结果已降级并如实标注")
    if excluded:
        notes.append("未进入求解器的批次：" + "；".join(
            f"{e['supply_id']}（{e['reason']}）" for e in excluded))
    return {
        "note": "；".join(notes),
        "solver_status": "optimal",
        "optimality": ("CP-SAT 证实：完整非支配前沿（ε-约束精确枚举；成本系数按 1e-6 CNY "
                       "定点量化，结果已用真实账本复算校验）" if not truncated
                       else "CP-SAT 证实：三锚点全局最优 + 部分前沿"
                            f"（触达 FRONTIER_CAP={FRONTIER_CAP} 截断，中段不完整）"),
        "solver": {"engine": "OR-Tools CP-SAT", "version": _ortools_version(),
                   "scale": CP_SCALE, "suppliers": len(order), "steps": total_steps,
                   "frontier_points": len(frontier_plans),
                   "truncated": truncated, "degraded": degraded,
                   "excluded_supplies": excluded},
        "feasible_count": None,
        "frontier_count": len(frontier_plans) if not truncated else None,
        "returned_count": len(returned),
        "is_complete": not truncated and not degraded,
        "truncated": truncated,
        "max_plans": max_plans,
        "step_t": step_t,
        "supplies_by_id": {sid: s.supplier_name for sid, s in supplies_by_id.items()},
        "sort_rule": "前沿按总成本升序、保守净减排降序、订单最大份额升序稳定排序",
        "plans": returned,
        "frontier": frontier_plans,
        "plans_unpriced": [],
    }


def _mix_plan(demand, eligible, masses, factor_data):
    total_cost, net_total = 0.0, 0.0
    allocation, problems = {}, []
    recycled_total = 0.0
    supplies_by_id = {s.supply_id: s for s in eligible}
    cost_assumptions = factor_data.get("cost_assumptions") or {}
    for supply, mass in zip(eligible, masses):
        if mass <= 0:
            continue
        allocation[supply.supply_id] = round(mass, 4)
    if len(allocation) < 2:
        problems.append("单一供给分配不属于组合方案（在 single 选项中表达）")
    ledger = project_cost_ledger(demand, allocation, supplies_by_id, cost_assumptions)
    for problem in ledger.problems:
        problems.append(problem)
    for supply_id, mass in allocation.items():
        supply = supplies_by_id[supply_id]
        result = net_emissions(demand, supply, mass, factor_data)
        if result["status"] != "可计算":
            problems.append(f"{supply.supplier_name}：{'；'.join(result['problems'])}")
            continue
        net_total += result.get("_net_full", result["net_avoided_kgco2e"])
        recycled_total += result.get("recycled_mass_t") or 0.0
    order_fractions = {
        sid: mass / (demand.required_mass_t or 1.0) for sid, mass in allocation.items()
    }
    computable = not problems and ledger.total is not None
    return {
        "allocation": allocation,
        "allocation_labels": {sid: supplies_by_id[sid].supplier_name for sid in allocation},
        "option_id": option_id("combo", demand, allocation),
        "total_cost_cny": round(ledger.total, 2) if ledger.total is not None else None,
        "cost_ledger": ledger.to_dict(),
        "net_avoided_kgco2e": round(net_total, 2) if not problems else None,
        "recycled_mass_t": round(recycled_total, 4) if not problems else None,
        "order_max_share": round(max(order_fractions.values()), 4) if order_fractions else 0.0,
        # 内部全精度值：Pareto 支配比较与排序用全精度（禁止用已展示舍入值比较），
        # 显示层（total_cost_cny/net_avoided_kgco2e）才舍入。
        "_cost_full": ledger.total if ledger.total is not None else None,
        "_net_full": net_total if not problems else None,
        "_share_full": max(order_fractions.values()) if order_fractions else 0.0,
        "computable": computable,
        "problems": problems,
        "note": "组合方案：规格与数量约束内按质量分配；净减排按同一功能单位求和",
    }


def _nondominated_mix(plans):
    """显式方向：总成本越小越好、保守净减排越大越好、订单最大份额越小越好。

    支配比较用内部全精度值（_cost_full/_net_full/_share_full），
    不用已舍入的展示值（独立核验约束：舍入值可致误支配）。
    缺价的方案不进入任何 Pareto 比较，单独返回并说明。
    """
    computable = [p for p in plans if p.get("computable")]
    unpriced = [{"allocation": p["allocation"], "reason": "；".join(p.get("problems") or ["不可计算"])}
                for p in plans if not p.get("computable")]
    if len(computable) < 2:
        return computable, unpriced
    # 性能优化（结果与原 O(n²) 逐位一致）：
    # 1) 预提取全精度三元组，避免内层反复 dict 取字段；
    # 2) 按成本升序排序：支配者必须满足 cost ≤ 目标 + 容差，成本更高的不可能支配，
    #    因此每个方案只与排在其前（含相等成本）的方案比较，检查量约减半；
    # 3) 边界值一次预计算，内层只做四个标量比较。
    vals = [(p["_cost_full"], p["_net_full"], p["_share_full"]) for p in computable]
    order = sorted(range(len(vals)), key=lambda i: vals[i][0])
    kept = []
    tol_c, tol_s = DOM_TOL_COST_NET, DOM_TOL_SHARE
    n_plans = len(vals)
    for idx_i, i in enumerate(order):
        c, n, s = vals[i]
        c_hi = c + tol_c
        c_lo = c - tol_c
        n_hi = n + tol_c
        n_lo = n - tol_c
        s_hi = s + tol_s
        s_lo = s - tol_s
        dominated = False
        for k in range(idx_i):
            c2, n2, s2 = vals[order[k]]
            if c2 <= c_hi and n2 >= n_lo and s2 <= s_hi and (
                    c2 < c_lo or n2 > n_hi or s2 < s_lo):
                dominated = True
                break
        if not dominated:
            kept.append(i)
    kept.sort()  # 保持原始枚举顺序（输出顺序与原实现一致）
    return [computable[i] for i in kept], unpriced


# —— 决策选项（统一契约，） ——


def build_options(demand, supplies, factor_data):
    """基准 + 单供 + 组合统一为 decision_options，全部携带 option_id。

    组合选项来自完整非支配前沿（不经 max_plans 截断），代表项从全集按
    option_id 去重选择。单供资格 = 一家具备全部产能。
    """
    required = demand.required_mass_t or 0.0
    baseline_price = demand.baseline_virgin_price_cny_per_t
    baseline_distance = factor_data.get("baseline_transport_distance_km", 100.0)
    cost_assumptions = factor_data.get("cost_assumptions") or {}
    supplies_by_id = {s.supply_id: s for s in supplies}
    options = []
    if baseline_price is not None and required > 0:
        baseline_total, baseline_problems = baseline_cost(
            demand, required, cost_assumptions, baseline_distance, factor_data)
        baseline_emissions = _baseline_emissions(demand, required, factor_data)
        options.append({
            "kind": "baseline", "label": "基准方案（全部原生 PP）",
            "option_id": option_id("baseline", demand),
            "allocation": {},
            "total_cost_cny": round(baseline_total, 2) if baseline_total is not None else None,
            "cost_delta_cny": 0.0,
            "net_avoided_kgco2e": 0.0,
            "marginal_abatement_cost_cny_per_tco2e": None,
            "category": "基准",
            "order_max_share": None,
            "note": (f"基准运输距离 {baseline_distance:g} km（演示假设）；"
                     f"基准到厂成本=原生价×数量+基准运费"),
            "emissions_kgco2e": round(baseline_emissions, 2) if baseline_emissions is not None else None,
            "problems": baseline_problems,
            "recycled_mass_t": 0.0,
        })
    for supply in supplies:
        state = supply_state(demand, supply)
        if state["state"] != "eligible":
            continue
        if (supply.available_mass_t or 0) < required:
            continue  # 单供资格：不足全部产能的批次只进组合
        allocation = {supply.supply_id: required}
        result = net_emissions(demand, supply, required, factor_data)
        ledger = project_cost_ledger(demand, allocation, supplies_by_id, cost_assumptions)
        problems = result["problems"] + ledger.problems
        if result["status"] != "可计算":
            options.append({"kind": "single", "label": supply.supplier_name,
                            "option_id": option_id("single", demand, allocation),
                            "allocation": allocation,
                            "allocation_labels": {supply.supply_id: supply.supplier_name},
                            "total_cost_cny": None, "cost_delta_cny": None,
                            "net_avoided_kgco2e": None,
                            "marginal_abatement_cost_cny_per_tco2e": None,
                            "category": "待补证据",
                            "order_max_share": 1.0,
                            "problems": result["problems"],
                            "emissions_kgco2e": None,
                            "recycled_mass_t": None,
                            "distance_km": supply.distance_km})
            continue
        cost = round(ledger.total, 2) if ledger.total is not None else None
        if cost is None:
            options.append({"kind": "single", "label": supply.supplier_name,
                            "option_id": option_id("single", demand, allocation),
                            "allocation": allocation,
                            "allocation_labels": {supply.supply_id: supply.supplier_name},
                            "total_cost_cny": None, "cost_delta_cny": None,
                            "net_avoided_kgco2e": result["net_avoided_kgco2e"],
                            "marginal_abatement_cost_cny_per_tco2e": None,
                            "category": "待补价格",
                            "order_max_share": 1.0,
                            "problems": ledger.problems,
                            "emissions_kgco2e": result["project_kgco2e"],
                            "recycled_mass_t": result["recycled_mass_t"],
                            "distance_km": supply.distance_km})
            continue
        baseline_total_cny = _baseline_total_of(options)
        cost_delta = cost - baseline_total_cny if baseline_total_cny is not None else None
        net = result["net_avoided_kgco2e"]
        marginal = cost_delta / (net / 1000.0) if (cost_delta is not None and net and net > 0) else None
        category = _category(cost_delta, net)
        option = {
            "kind": "single", "label": supply.supplier_name,
            "option_id": option_id("single", demand, allocation),
            "allocation": allocation,
            "allocation_labels": {supply.supply_id: supply.supplier_name},
            "total_cost_cny": cost,
            "cost_delta_cny": round(cost_delta, 2) if cost_delta is not None else None,
            "net_avoided_kgco2e": net,
            "marginal_abatement_cost_cny_per_tco2e": round(marginal, 2) if marginal is not None else None,
            "category": category,
            "order_max_share": 1.0,
            "emissions_kgco2e": result["project_kgco2e"],
            "recycled_mass_t": result["recycled_mass_t"],
            "cost_ledger": ledger.to_dict(),
            "distance_km": supply.distance_km,
            "net": result,
            "_cost_full": ledger.total,
            "_net_full": result.get("_net_full", result["net_avoided_kgco2e"]),
            "_share_full": 1.0,
        }
        # v14 任务 1：因子带 value_min/value_max 时输出确定性净减排区间（点估计必在其中）
        if factor_data_has_ranges(factor_data):
            interval = net_interval(demand, supply, required, factor_data)
            if interval["status"] == "可计算":
                lo, hi = interval["interval_kgco2e"]
                option["net_interval_kgco2e"] = [round(lo, 2), round(hi, 2)]
                option["net_interval_note"] = ("净减排区间为输入因子极值传播（value_min/value_max），"
                                               "非统计置信区间")
        options.append(option)
    # 组合方案并入 options（完整非支配前沿，不截断）
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    mix = simulate_supply_mix(demand, eligible, factor_data, partial_capacity=True, max_plans=None)
    combo_plans = list(mix.get("frontier") or [])
    if mix.get("error") and mix.get("best_effort_plan"):
        # 组合超限回退：贪心可行解也进入选项集，明确标注未证实最优
        combo_plans.append(mix["best_effort_plan"])
    for plan in combo_plans:
        if not plan.get("computable") or len(plan.get("allocation") or {}) < 2:
            continue  # 组合选项只收多供方方案；单供方分配已在 single 中表达
        baseline_total_cny = _baseline_total_of(options)
        cost_delta = plan["total_cost_cny"] - baseline_total_cny if baseline_total_cny is not None else None
        net = plan["net_avoided_kgco2e"]
        marginal = cost_delta / (net / 1000.0) if (cost_delta is not None and net and net > 0) else None
        option = {
            "kind": "combo",
            "label": "组合：" + allocation_label(plan["allocation"], supplies_by_id),
            "option_id": plan["option_id"],
            "allocation": plan["allocation"],
            "allocation_labels": plan["allocation_labels"],
            "total_cost_cny": plan["total_cost_cny"],
            "cost_delta_cny": round(cost_delta, 2) if cost_delta is not None else None,
            "net_avoided_kgco2e": net,
            "marginal_abatement_cost_cny_per_tco2e": round(marginal, 2) if marginal is not None else None,
            "category": _category(cost_delta, net),
            "order_max_share": plan["order_max_share"],
            "emissions_kgco2e": None,
            "recycled_mass_t": plan["recycled_mass_t"],
            "cost_ledger": plan["cost_ledger"],
            "note": plan["note"],
            "_cost_full": plan.get("_cost_full"),
            "_net_full": plan.get("_net_full"),
            "_share_full": plan.get("_share_full"),
        }
        # v14 任务 1：组合选项的净减排区间 = 各供给区间之和（区间加法，逐供给传播）
        if factor_data_has_ranges(factor_data):
            lo_sum, hi_sum, ok = 0.0, 0.0, True
            for sid, mass in plan["allocation"].items():
                interval = net_interval(demand, supplies_by_id[sid], mass, factor_data)
                if interval["status"] != "可计算":
                    ok = False
                    break
                lo_sum += interval["interval_kgco2e"][0]
                hi_sum += interval["interval_kgco2e"][1]
            if ok:
                option["net_interval_kgco2e"] = [round(lo_sum, 2), round(hi_sum, 2)]
                option["net_interval_note"] = ("净减排区间为输入因子极值传播（value_min/value_max），"
                                               "非统计置信区间")
        options.append(option)
    return options


def _baseline_total_of(options):
    """「较基准」的参照必须来自基准选项本身（无基准价时不得退化为 vs 首个单供）。"""
    for option in options:
        if option.get("kind") == "baseline":
            return option.get("total_cost_cny")
    return None


def _category(cost_delta, net):
    if cost_delta is None:
        return "待补价格"
    if net is None:
        return "待补证据"
    if cost_delta < 0 and net > 0:
        return "成本与碳双降"
    if cost_delta >= 0 and net > 0:
        return "绿色溢价"
    if cost_delta < 0 and net <= 0:
        return "省钱但增排"
    return "成本与碳双升"


def _baseline_emissions(demand, mass_t, factor_data):
    from .ledger import resolve_factor
    virgin, _, problems = resolve_factor(factor_data, "baseline_material",
                                         demand.target_material_code)
    # 基准运输方式来自需求层配置，与候选无关
    convention = factor_data.get("accounting_convention") or {}
    baseline_mode = convention.get("baseline_transport_mode") or "公路货运"
    transport, _, t_problems = resolve_factor(factor_data, "transport",
                                              demand.target_material_code or "ANY",
                                              transport_mode=baseline_mode)
    if problems or t_problems:
        return None
    distance = factor_data.get("baseline_transport_distance_km", 100.0)
    value = mass_t * float(virgin["value"])
    if not virgin.get("transport_included_in_factor"):
        value += mass_t * distance * float(transport["value"])
    return value


def _quality_rows(demand, supplies):
    from .evidence_quality import score_all
    return score_all(supplies, demand)


def greedy_compare(demand, supplies, factor_data, mix):
    from .greedy_baseline import greedy_report
    return greedy_report(demand, supplies, factor_data, mix)


# —— 敏感性（option_id 键 + OAT + 翻转阈值 + 联合情景） ——


def run_sensitivity(demand, supplies, factor_data):
    """结论稳定性核查：OAT 确定性扰动 + 关键参数翻转阈值 + 两参数联合情景。

    核查对象以 option_id 为身份（不同分配量的方案不再被字典覆盖）。
    成本排序只来自到厂成本账本，N-1 服务率只依赖产能、分配与 N-1 模型参数，
    二者与因子无关，为确定性结论，不参与扰动。
    """
    base_options = build_options(demand, supplies, factor_data)
    base = {o["option_id"]: o for o in base_options if o["kind"] != "baseline"}
    base_rep_ids = {o["option_id"] for o in representative_options(base_options)}
    anchor = None
    for o in base_options:
        if o["kind"] == "baseline" or o.get("total_cost_cny") is None or o.get("net_avoided_kgco2e") is None:
            continue
        if anchor is None or o["total_cost_cny"] < anchor["total_cost_cny"]:
            anchor = o
    def _eval_scenario(item):
        """单场景评估（只依赖输入与只读的 base/anchor，线程安全）；输出确定性。"""
        name, f2 = item
        # 每场景只枚举一次方案集（此前 by_id 与代表集合各枚举一遍，重复全前沿计算）
        opts2 = build_options(demand, supplies, f2)
        by_id = {o["option_id"]: o for o in opts2 if o["kind"] != "baseline"}
        rep_ids2 = {o["option_id"] for o in representative_options(opts2)}
        sign_flips, category_changes = [], []
        for oid, o in base.items():
            o2 = by_id.get(oid)
            if o2 is None or o.get("net_avoided_kgco2e") is None or o2.get("net_avoided_kgco2e") is None:
                continue
            if (o["net_avoided_kgco2e"] > 0) != (o2["net_avoided_kgco2e"] > 0):
                sign_flips.append(oid)
            elif o2["category"] != o["category"]:
                category_changes.append(oid)
        anchor_swing = None
        if anchor is not None:
            anchor2 = by_id.get(anchor["option_id"])
            if anchor2 is not None and anchor2.get("net_avoided_kgco2e") is not None:
                anchor_swing = round(anchor2["net_avoided_kgco2e"]
                                     - anchor["net_avoided_kgco2e"], 2)
        return {"name": name, "sign_flips": sign_flips,
                "category_changes": category_changes,
                "selection_changed": rep_ids2 != base_rep_ids,
                "anchor_swing": anchor_swing}

    scenario_inputs = _sensitivity_scenarios(factor_data)
    # v19 任务 1：场景级并行（executor.map 保序聚合，输出与串行逐位一致）
    workers = _parallel_workers(len(scenario_inputs))
    if workers > 1 and len(scenario_inputs) > 1:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="sensitivity") as ex:
            out = list(ex.map(_eval_scenario, scenario_inputs))
    else:
        out = [_eval_scenario(item) for item in scenario_inputs]
    thresholds = _break_even_thresholds(demand, supplies, factor_data, anchor)
    joint = _joint_scenarios(demand, supplies, factor_data, base_rep_ids)
    flips = sum(len(s["sign_flips"]) for s in out)
    cat_changes = sum(len(s["category_changes"]) for s in out)
    sel_changes = sum(1 for s in out if s["selection_changed"])
    verdict = "结论稳定" if flips == 0 and sel_changes == 0 else "结论对假设敏感，需复核"
    return {"scenarios": out, "thresholds": thresholds, "joint": joint,
            "sign_flips": flips, "category_changes": cat_changes,
            "selection_changes": sel_changes, "verdict": verdict,
            "anchor_label": anchor["label"] if anchor is not None else None,
            "anchor_option_id": anchor["option_id"] if anchor is not None else None,
            "anchor_base_net": anchor["net_avoided_kgco2e"] if anchor is not None else None,
            "note": "扰动范围为演示假设（不是统计置信区间）；"
                    "成本排序只来自到厂成本账本，N-1 服务率只依赖产能、分配与模型参数，"
                    "二者与因子无关，为确定性结论。"}


def _sensitivity_scenarios(factor_data):
    """扰动场景：名称 → 变换后的因子副本（深拷贝，绝不改动原因子表）。

    扰动范围来自因子配置的 sensitivity 注册表，缺省回退 ±20%/±10%（向后兼容）。
    """
    factors = factor_data.get("factors", {})
    registry = factor_data.get("sensitivity") or {}
    dist = factor_data.get("baseline_transport_distance_km", 100.0)
    scenarios = []

    def add(name, mutate):
        f2 = copy.deepcopy(factor_data)
        mutate(f2)
        scenarios.append((name, f2))

    dist_low = float((registry.get("baseline_transport_distance_km") or {}).get("low_factor", 0.8))
    dist_high = float((registry.get("baseline_transport_distance_km") or {}).get("high_factor", 1.2))
    add("基准运输距离 −20%", lambda f: f.update({"baseline_transport_distance_km": dist * dist_low}))
    add("基准运输距离 +20%", lambda f: f.update({"baseline_transport_distance_km": dist * dist_high}))
    from .ledger import resolve_factor
    for role, label in (("project_material", "再生PP颗粒生产因子"),
                        ("transport", "运输因子")):
        resolved, _, _ = resolve_factor(factor_data, role, "PP", transport_mode="公路货运")
        if resolved is None:
            continue
        key = str(resolved.get("factor_id"))
        value = float(resolved["value"])
        low = float((registry.get(key) or registry.get(role) or {}).get("low_factor", 0.9))
        high = float((registry.get(key) or registry.get(role) or {}).get("high_factor", 1.1))
        add(f"{label} −{round((1 - low) * 100)}%",
            lambda f, k=key, v=value, m=low: f["factors"][k].update({"value": round(v * m, 6)}))
        add(f"{label} +{round((high - 1) * 100)}%",
            lambda f, k=key, v=value, m=high: f["factors"][k].update({"value": round(v * m, 6)}))
    return scenarios


def _break_even_thresholds(demand, supplies, factor_data, anchor):
    """关键参数翻转阈值：二分查找使锚点方案净减排符号翻转的参数倍数（OAT）。

    报告「参数、基准值、翻转倍数、翻转值、说明」；找不到翻转（区间内符号不变）
    如实报告未翻转。演示范围内仅是情景阈值，不是统计置信区间。
    """
    from .ledger import resolve_factor
    if anchor is None or not anchor.get("net_avoided_kgco2e"):
        return []
    base_net = anchor["net_avoided_kgco2e"]
    registry = factor_data.get("sensitivity") or {}
    dist = factor_data.get("baseline_transport_distance_km", 100.0)
    results = []

    def param_net(multiplier):
        f2 = copy.deepcopy(factor_data)
        f2["baseline_transport_distance_km"] = dist * multiplier
        by_id = {o["option_id"]: o for o in build_options(demand, supplies, f2)}
        o2 = by_id.get(anchor["option_id"])
        return o2["net_avoided_kgco2e"] if o2 else None

    def find_flip(base_value, base_multiplier=1.0, lo=0.01, hi=3.0):
        def net_at(m):
            f2 = copy.deepcopy(factor_data)
            f2["baseline_transport_distance_km"] = dist * m
            by_id = {o["option_id"]: o for o in build_options(demand, supplies, f2)}
            o2 = by_id.get(anchor["option_id"])
            return o2["net_avoided_kgco2e"] if o2 else None

        sign = lambda x: 0 if x is None else (1 if x > 0 else -1)
        if sign(net_at(lo)) == sign(net_at(hi)):
            return None
        for _ in range(60):
            mid = (lo + hi) / 2
            if sign(net_at(mid)) == sign(net_at(lo)):
                lo = mid
            else:
                hi = mid
        return round(hi, 6)

    flip_m = find_flip(dist)
    results.append({
        "param": "baseline_transport_distance_km", "base_value": dist,
        "anchor_option_id": anchor["option_id"], "anchor_base_net_kgco2e": base_net,
        "flip_multiplier": flip_m, "flip_value": round(dist * flip_m, 2) if flip_m else None,
        "note": ("净减排符号在 [1%,300%] 区间内翻转的阈值倍数；None=区间内未翻转"
                 "（基准运输距离越大基准排放越高，净减排越大，向小侧翻转）"),
    })
    return results


def _joint_scenarios(demand, supplies, factor_data, base_rep_ids):
    """两参数联合情景：配置 joint 参数对的 low/base/high 3×3 网格，
    报告代表方案集合（option_id 集）是否变化与净减排符号翻转数。"""
    registry = factor_data.get("sensitivity") or {}
    pairs = registry.get("joint") or []
    from .ledger import resolve_factor
    out = []
    dist = factor_data.get("baseline_transport_distance_km", 100.0)
    for pair in pairs:
        if len(pair) != 2:
            continue
        name1, name2 = pair
        def multipliers(name):
            entry = registry.get(name) or {}
            return [float(entry.get("low_factor", 0.8)), 1.0, float(entry.get("high_factor", 1.2))]
        grid = []
        changed = 0
        sign_flips_total = 0
        for m1 in multipliers(name1):
            for m2 in multipliers(name2):
                f2 = copy.deepcopy(factor_data)
                if name1 == "baseline_transport_distance_km":
                    f2["baseline_transport_distance_km"] = dist * m1
                else:
                    resolved, _, _ = resolve_factor(factor_data, "project_material", "PP")
                    if resolved:
                        f2["factors"][resolved["factor_id"]]["value"] = round(
                            float(resolved["value"]) * m1, 6)
                if name2 == "baseline_transport_distance_km":
                    f2["baseline_transport_distance_km"] = dist * m2
                else:
                    resolved, _, _ = resolve_factor(factor_data, "project_material", "PP")
                    if resolved:
                        f2["factors"][resolved["factor_id"]]["value"] = round(
                            float(resolved["value"]) * m2, 6)
                opts2 = build_options(demand, supplies, f2)
                rep_ids2 = {o["option_id"] for o in representative_options(opts2)}
                flips = sum(
                    1 for o in opts2 if o["kind"] != "baseline"
                    and o.get("net_avoided_kgco2e") is not None
                    and o.get("net_avoided_kgco2e") < 0)
                if rep_ids2 != base_rep_ids:
                    changed += 1
                sign_flips_total += flips
                grid.append({"m1": round(m1, 3), "m2": round(m2, 3), "flips": flips,
                             "selection_changed": rep_ids2 != base_rep_ids})
        out.append({"params": [name1, name2], "grid": grid,
                    "cells_selection_changed": changed,
                    "total_sign_flips": sign_flips_total})
    return out


def representative_options(options):
    """代表项：最低成本 / 最高保守净减排 / 最低集中度 / 综合平衡 / 基准，
    按 option_id 去重（同 label 不同分配不再被误合并，）。"""
    baseline = next((o for o in options if o["kind"] == "baseline"), None)
    comparable = [o for o in options
                  if o["kind"] != "baseline"
                  and o.get("total_cost_cny") is not None
                  and o.get("net_avoided_kgco2e") is not None]
    picks = []
    if comparable:
        picks.append(min(comparable, key=lambda o: o["total_cost_cny"]))
        picks.append(max(comparable, key=lambda o: o["net_avoided_kgco2e"]))
        picks.append(min(comparable,
                         key=lambda o: o.get("order_max_share") if o.get("order_max_share") is not None else 1.0))
        by_net = sorted(comparable, key=lambda o: o["net_avoided_kgco2e"])
        picks.append(by_net[len(by_net) // 2])
    if baseline:
        picks.append(baseline)
    seen, unique = set(), []
    for option in picks:
        if option["option_id"] not in seen:
            seen.add(option["option_id"])
            unique.append(option)
    return unique


# —— N-1 断供压力测试（供应商实体失效 + 显式模型 + 两张分离对照） ——


def default_n1_model(demand):
    """默认 N-1 模型（显式声明假设；未知约束标注为未知，不冒充交付保证）。"""
    return {
        "failure_entity": "supplier",          # 失效实体=供应商（名下所有批次同时失效）
        "deadline": demand.due_date or "未提供",
        "committed_mass_t": demand.required_mass_t or 0.0,
        "logistics_days": None,                # 切换时间未知 → 产能上限口径并标注
        "switching_fee_cny_per_supplier": None,  # 切换费用未知
        "spare_reservation_t": 0.0,            # 备用预留量
        "qualification": "eligible_only",      # 备用资格=当前证据下 eligible
        "static_capacity_only": False,         # False=含时窗；True=仅产能静态上限
        "joint_failure_model": "单家供应商失效（未启用共同失效模型，只测单家失效）",
    }


def n1_stress_test(demand, supplies, factor_data, options=None, model=None):
    """对每个方案逐个模拟一个供应商实体不可用（名下全部批次同时失效），
    重新分配后输出交付服务率、缺口吨数与单点故障判断。

    备用产能按实体聚合：其他已分配供应商的剩余产能 + 未分配 eligible 供应商的全部产能；
    受模型约束（切换时间/时窗/预留）过滤；约束未知时按产能上限口径并标注。
    """
    options = options or representative_options(build_options(demand, supplies, factor_data))
    model = model or default_n1_model(demand)
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    by_id = {s.supply_id: s for s in eligible}
    required = demand.required_mass_t or 0.0
    static = bool(model.get("static_capacity_only"))
    logistics_days = model.get("logistics_days")
    reservation = float(model.get("spare_reservation_t") or 0.0)
    results = []
    for option in options:
        allocation = option.get("allocation") or {}
        if not allocation:
            continue
        entities = {}
        for supply_id, mass in allocation.items():
            supply = by_id.get(supply_id)
            # 失效实体优先按 supplier_id 聚合；缺失时回退供应商名并标注近似
            entity_key = (supply.supplier_id or supply.supplier_name) if supply else supply_id
            entities.setdefault(entity_key, {"allocated": 0.0, "supply_ids": [],
                                             "label": supply.supplier_name if supply else supply_id,
                                             "identity_note": (
                                                 "" if (supply and supply.supplier_id)
                                                 else "（实体身份按供应商名近似，无 supplier_id）")})
            entities[entity_key]["allocated"] += mass
            entities[entity_key]["supply_ids"].append(supply_id)
        worst = None
        single_point_failures = []
        per_entity = []
        for entity_key, info in entities.items():
            display_name = info["label"]
            failed_mass = info["allocated"]
            failed_ids = set(info["supply_ids"])
            spare = 0.0
            spare_detail = []
            for other_key, other_info in entities.items():
                if other_key == entity_key:
                    continue
                other_label = other_info["label"]
                for sid in other_info["supply_ids"]:
                    supply = by_id.get(sid)
                    if not supply:
                        continue
                    unused = (supply.available_mass_t or 0) - allocation.get(sid, 0.0)
                    usable, note = _usable_spare(supply, max(0.0, unused), demand, model,
                                                 static, logistics_days)
                    if usable > 0:
                        spare += usable
                        spare_detail.append(f"{other_label}:{usable:g}t{note}")
                    elif note:
                        spare_detail.append(f"{other_label}:0t{note}（备用不可及，不计入）")
            for supply in eligible:
                if supply.supply_id in allocation or supply.supply_id in failed_ids:
                    continue
                usable, note = _usable_spare(supply, supply.available_mass_t or 0.0, demand,
                                             model, static, logistics_days)
                if usable > 0:
                    spare += usable
                    spare_detail.append(f"{supply.supplier_name}:{usable:g}t{note}")
                elif note:
                    spare_detail.append(f"{supply.supplier_name}:0t{note}（备用不可及，不计入）")
            spare = max(0.0, spare - reservation)
            delivered = (required - failed_mass) + min(failed_mass, spare)
            service = delivered / required if required else 0.0
            gap = max(0.0, required - delivered)
            entry = {"failed_supplier": display_name + info["identity_note"],
                     "failed_entity": entity_key,
                     "failed_batches": sorted(failed_ids),
                     "fallback_available_t": round(spare, 4),
                     "delivery_service_rate": round(min(1.0, service), 4),
                     "gap_t": round(gap, 4),
                     "spare_detail": spare_detail}
            per_entity.append(entry)
            if gap > 0:
                single_point_failures.append(display_name)
            if worst is None or entry["delivery_service_rate"] < worst["delivery_service_rate"]:
                worst = entry
        if worst is None:
            continue
        results.append({
            "option_id": option.get("option_id"),
            "label": option["label"],
            "kind": option["kind"],
            "allocation": allocation,
            "min_delivery_service_rate": worst["delivery_service_rate"],
            "worst_case": worst,
            "per_entity": per_entity,
            "single_point_failures": single_point_failures,
            "total_cost_cny": option.get("total_cost_cny"),
            "net_avoided_kgco2e": option.get("net_avoided_kgco2e"),
            "model": model,
        })
    return results


def _usable_spare(supply, candidate_mass, demand, model, static, logistics_days):
    """备用可用性判定：资格（eligible 已在调用方保证）+ 时窗/切换时间约束。"""
    if candidate_mass <= TOL:
        return 0.0, ""
    if static:
        return candidate_mass, "（仅产能静态上限，未含时窗/物流约束）"
    deadline = model.get("deadline") or demand.due_date
    if logistics_days is None:
        return candidate_mass, "（切换时间未知：产能上限口径，非交付保证）"
    try:
        from datetime import date, timedelta
        deadline_d = date.fromisoformat(deadline)
        if supply.available_from:
            ready_d = date.fromisoformat(supply.available_from) + timedelta(days=logistics_days)
            if ready_d > deadline_d:
                return 0.0, f"（备用不可及：{supply.available_from}+{logistics_days:g}天切换 > 截止 {deadline}）"
            return candidate_mass, ""
        return candidate_mass, "（供给可用时窗缺失，按产能上限口径，非交付保证）"
    except ValueError:
        return candidate_mass, "（截止日期非法，按产能上限口径）"


def n1_compare_strategies(demand, supplies, factor_data, model=None):
    """对照一：固定信息集（保守现状证据）下，比较各采购策略（代表方案）的 N-1 表现。"""
    model = model or default_n1_model(demand)
    options = representative_options(build_options(demand, supplies, factor_data))
    results = n1_stress_test(demand, supplies, factor_data, options=options, model=model)
    return {"comparison": "采购策略对照（固定信息集：保守现状证据）",
            "model": model, "results": results,
            "conclusion_note": ("不同策略的 N-1 服务率差异只归因于分配策略本身；"
                                "补证带来的差异见 n1_compare_evidence（对照二）")}


def n1_compare_evidence(demand, supplies, factor_data, model=None):
    """对照二：固定采购方案下，比较补证前后（保守 vs 乐观）的 N-1 服务率差异。"""
    from .evidence_value import optimistic_supplies
    model = model or default_n1_model(demand)
    options = representative_options(build_options(demand, supplies, factor_data))
    anchor = min((o for o in options if o["kind"] != "baseline"
                  and o.get("total_cost_cny") is not None),
                 key=lambda o: o["total_cost_cny"], default=None)
    if anchor is None:
        return {"comparison": "补证对照（固定采购方案）", "model": model,
                "results": [], "note": "无可计算方案，未做补证对照"}
    anchor_allocation = anchor["allocation"]
    optimistic = optimistic_supplies(demand, supplies)
    fixed_option = {"option_id": anchor["option_id"], "label": anchor["label"],
                    "kind": anchor["kind"], "allocation": anchor_allocation}
    before = n1_stress_test(demand, supplies, factor_data, options=[fixed_option], model=model)
    after = n1_stress_test(demand, optimistic, factor_data, options=[fixed_option], model=model)
    before_rate = before[0]["min_delivery_service_rate"] if before else None
    after_rate = after[0]["min_delivery_service_rate"] if after else None
    return {"comparison": "补证对照（固定采购方案不变，只改变证据状态）",
            "model": model,
            "fixed_option": {"option_id": anchor["option_id"], "label": anchor["label"],
                             "allocation": anchor_allocation},
            "before": before, "after": after,
            "delta_service_rate": (round(after_rate - before_rate, 4)
                                   if before_rate is not None and after_rate is not None else None),
            "conclusion_note": ("服务率变化只归因于证据补证（备用供给资格改变）；"
                                "采购策略差异见 n1_compare_strategies（对照一）")}


# —— 演示数据 ——


def demo_demand():
    return MaterialDemand(
        demand_id="DEM-001", buyer_name="某包装制品有限公司（演示）",
        target_material_code="PP", application="非食品接触周转箱",
        required_mass_t=15.0, min_recycled_content_pct=80.0,
        mfi_min=8.0, mfi_max=16.0, max_moisture_pct=0.5, max_ash_pct=5.0,
        accepted_form="颗粒", accepted_color="黑色、灰色",
        required_from="2026-09-15", due_date="2026-10-15",
        destination="某工业园区（演示）",
        baseline_virgin_price_cny_per_t=5500.0, max_distance_km=500.0,
        evidence_source="采购规格书（演示）", source="演示",
    )


def demo_supplies():
    """演示供给：8t 完全匹配（只进组合）+ 7t 需补检测 + 熔指淘汰 + 18t 低价但脆弱。"""
    return [
        MaterialSupply(
            supply_id="SUP-01", supplier_name="本地再生资源有限公司（演示）",
            material_code="PP", material_name_raw="再生PP颗粒（黑色注塑级）",
            polymer_type="PP", form="颗粒", color="黑色", grade="注塑级",
            mfi_g_10min=12.0, moisture_pct=0.3, ash_pct=3.0, recycled_content_pct=95.0,
            available_mass_t=8.0, available_from="2026-09-10", available_to="2026-10-31",
            origin="某工业园区（演示）", price_cny_per_t=4200.0,
            price_includes_freight=False, yield_pct=100.0, inspection_required=False,
            preprocessing="已造粒",
            transport_mode="公路货运", factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
            distance_km=30.0, evidence_source="检测报告（演示）", evidence_date="2026-08-15",
            source="演示",
        ),
        MaterialSupply(
            supply_id="SUP-02", supplier_name="邻区再生企业（演示）",
            material_code="PP", material_name_raw="再生PP颗粒（灰色）",
            polymer_type="PP", form="颗粒", color="灰色", grade="注塑级",
            mfi_g_10min=10.0, moisture_pct=0.4, ash_pct=4.0, recycled_content_pct=85.0,
            available_mass_t=7.0, available_from="2026-09-12", available_to="2026-10-20",
            origin="邻区（演示）", price_cny_per_t=4000.0,
            price_includes_freight=False, yield_pct=100.0, inspection_required=False,
            preprocessing="已造粒",
            transport_mode="公路货运", factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
            distance_km=60.0, evidence_source="", evidence_date="",
            source="演示",
        ),
        MaterialSupply(
            supply_id="SUP-03", supplier_name="某改性塑料企业（演示）",
            material_code="PP", material_name_raw="高流动PP再生颗粒",
            polymer_type="PP", form="颗粒", color="黑色", grade="注塑级",
            mfi_g_10min=30.0, moisture_pct=0.3, ash_pct=3.0, recycled_content_pct=90.0,
            available_mass_t=18.0, available_from="2026-09-10", available_to="2026-10-25",
            origin="外市（演示）", price_cny_per_t=3600.0,
            price_includes_freight=False, yield_pct=100.0, inspection_required=False,
            preprocessing="已造粒",
            transport_mode="公路货运", factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
            distance_km=120.0, evidence_source="检测报告（演示）", evidence_date="2026-08-10",
            source="演示",
        ),
        MaterialSupply(
            supply_id="SUP-04", supplier_name="外省再生企业（演示）",
            material_code="PP", material_name_raw="再生PP颗粒（黑色）",
            polymer_type="PP", form="颗粒", color="黑色", grade="注塑级",
            mfi_g_10min=11.0, moisture_pct=0.3, ash_pct=3.5, recycled_content_pct=90.0,
            available_mass_t=18.0, available_from="2026-09-08", available_to="2026-10-10",
            origin="外省（演示）", price_cny_per_t=3800.0,
            price_includes_freight=False, yield_pct=100.0, inspection_required=False,
            preprocessing="已造粒",
            transport_mode="公路货运", factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
            distance_km=480.0, evidence_source="检测报告（演示）", evidence_date="2026-08-05",
            source="演示",
        ),
    ]


# —— 数据模式（demo / local / auto / online） ——


def run_material(data_mode="auto", demand_id=None):
    """再生材料主线入口。返回统一结果对象；online 抛错（未实现，禁止伪装）。

    多条需求必须显式选择（demand_id）或逐项建任务，禁止静默取第一条（）。
    """
    if data_mode == "online":
        raise ValueError("online 模式未实现：当前不接入在线数据源，请使用 demo 或 local")
    if data_mode == "local":
        if not config.MATERIAL_SUPPLY_CSV.exists() or not config.MATERIAL_DEMAND_CSV.exists():
            raise ValueError("local 模式需要 data/input/material_supply.csv 与 material_demand.csv，"
                             "文件缺失，禁止回退演示数据")
        supplies, supply_stats = load_supplies_csv(config.MATERIAL_SUPPLY_CSV)
        demands, demand_stats = load_demands_csv(config.MATERIAL_DEMAND_CSV)
        if not supplies or not demands:
            raise ValueError("local 模式没有可分析的有效供给或需求记录")
        if len(demands) > 1 and demand_id is None:
            raise ValueError(
                "需求 CSV 包含多条需求，必须显式指定："
                + "、".join(d.demand_id for d in demands)
                + "。请用 --demand-id 选择，或逐条需求建任务（当前不支持联合优化）。")
        demand = next((d for d in demands if d.demand_id == demand_id), demands[0]) \
            if demand_id else demands[0]
        if len(demands) > 1:
            mode_note = f"数据模式：本地 CSV（需求 {demand.demand_id}，其余 {len(demands) - 1} 条未纳入本任务）"
        else:
            mode_note = "数据模式：本地 CSV"
        load_stats = {"supply": supply_stats, "demand": demand_stats}
        resolved_mode = "local"
        fallback_reason = ""
    elif data_mode == "auto":
        if config.MATERIAL_SUPPLY_CSV.exists() and config.MATERIAL_DEMAND_CSV.exists():
            resolved_mode, fallback_reason = "local", ""
        else:
            resolved_mode, fallback_reason = "demo", "本地 CSV 缺失，回退演示数据"
        if resolved_mode == "local":
            return run_material("local", demand_id=demand_id)
        demand = demo_demand()
        supplies = demo_supplies()
        mode_note = "数据模式：演示数据（虚构供给与需求，不得用于真实决策）"
        load_stats = None
    else:  # demo
        demand = demo_demand()
        supplies = demo_supplies()
        mode_note = "数据模式：演示数据（虚构供给与需求，不得用于真实决策）"
        load_stats = None
        resolved_mode, fallback_reason = "demo", ""
    factor_data = load_material_factors()
    return _build_result(demand, supplies, factor_data, data_mode, resolved_mode,
                         fallback_reason, mode_note, load_stats)


def analyze_task(demand, supplies, factor_data=None, resolved_mode="local",
                 requested_mode="local", fallback_reason="", mode_note=None,
                 load_stats=None, mix_step_t=0.5):
    """由需求+供给构建统一结果对象（试点 API / 静态演示共用同一计算核心）。"""
    factor_data = factor_data or load_material_factors()
    mode_note = mode_note or f"数据模式：工作台任务（{resolved_mode}）"
    return _build_result(demand, supplies, factor_data, requested_mode, resolved_mode,
                         fallback_reason, mode_note, load_stats, mix_step_t=mix_step_t)


def _build_result(demand, supplies, factor_data, requested_mode, resolved_mode,
                  fallback_reason, mode_note, load_stats, mix_step_t=0.5):
    """由需求+供给构建统一结果对象（demo/local 共用同一重算核心）。"""
    states = [supply_state(demand, s) for s in supplies]
    eligible = [s for s in supplies if supply_state(demand, s)["state"] == "eligible"]
    singles = []
    for supply, state in zip(supplies, states):
        if state["state"] != "eligible" or (supply.available_mass_t or 0) < (demand.required_mass_t or 0):
            singles.append({"supply": supply, "state": state, "net": None})
            continue
        singles.append({"supply": supply, "state": state,
                        "net": net_emissions(demand, supply, demand.required_mass_t, factor_data)})
    from .evidence_value import plan_evidence_actions, optimistic_supplies
    from .scenarios import build_scenario_payload
    # —— v19 任务 1：场景级并行化（调度层改动，输出与串行逐位一致，红线 3） ——
    # 双情景 payload 与敏感性核查互相独立且为耗时大头（docs/完整分析耗时结构-v18.md：
    # 敏感性 47% + 双情景构建约 40%），提交线程池与主线程的 mix/options/N-1 链并发；
    # 聚合按字面量固定顺序，输出确定性。
    workers = _parallel_workers(3)
    futures = {}
    if workers > 1:
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="result")
        futures = {
            "conservative": pool.submit(build_scenario_payload, demand, supplies,
                                        factor_data, "conservative",
                                        include_passport=False),
            "optimistic": pool.submit(build_scenario_payload, demand,
                                      optimistic_supplies(demand, supplies),
                                      factor_data, "optimistic",
                                      include_passport=False),
            "sensitivity": pool.submit(run_sensitivity, demand, supplies, factor_data),
        }
    mix = simulate_supply_mix(demand, eligible, factor_data, partial_capacity=True,
                              step_t=mix_step_t)
    options = build_options(demand, supplies, factor_data)
    reps = representative_options(options)
    n1_model = default_n1_model(demand)
    n1 = n1_stress_test(demand, supplies, factor_data, options=reps, model=n1_model)
    evidence_plans = plan_evidence_actions(demand, supplies, factor_data)
    if futures:
        scenarios = {"conservative": futures["conservative"].result(),
                     "optimistic": futures["optimistic"].result()}
        sensitivity = futures["sensitivity"].result()
        pool.shutdown()
    else:
        scenarios = {
            "conservative": build_scenario_payload(demand, supplies, factor_data,
                                                  "conservative", include_passport=False),
            "optimistic": build_scenario_payload(demand, optimistic_supplies(demand, supplies),
                                                factor_data, "optimistic",
                                                include_passport=False),
        }
        sensitivity = run_sensitivity(demand, supplies, factor_data)
    return {
        "industry": "再生PP",
        "data_mode": resolved_mode,
        "requested_mode": requested_mode,
        "resolved_mode": resolved_mode,
        "fallback_reason": fallback_reason,
        "data_version": factor_data,
        "algorithm_version": ALGORITHM_VERSION,
        "data_note": mode_note,
        "demand": demand,
        "supplies": supplies,
        "match_states": states,
        "singles": singles,
        "mix": mix,
        "options": options,
        "representatives": reps,
        "n1_stress": n1,
        "scenarios": scenarios,
        "evidence_plans": evidence_plans,
        "n1_compare_strategies": n1_compare_strategies(demand, supplies, factor_data, n1_model),
        "n1_compare_evidence": n1_compare_evidence(demand, supplies, factor_data, n1_model),
        "sensitivity": sensitivity,
        "quality": _quality_rows(demand, supplies),
        "greedy_baseline": greedy_compare(demand, supplies, factor_data, mix),
        "factor_data": factor_data,
        "load_stats": load_stats,
        "warnings": (["当前使用内置虚构演示数据：企业、批次、价格与因子均为演示值，不得用于真实决策。"]
                     if resolved_mode == "demo" else []),
        "quality_stats": {
            "supplies": {"n": len(supplies),
                         "eligible": sum(1 for st in states if st["state"] == "eligible"),
                         "pending": sum(1 for st in states if st["state"] == "pending_evidence"),
                         "ineligible": sum(1 for st in states if st["state"] == "ineligible")},
            "emission_uncertainty": "净减排为情景核算（同一功能单位），非实测减排",
        },
    }
