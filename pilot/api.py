"""试点 API 桥接：任务数据 ↔ 计算引擎，结果 JSON 序列化。

- 需求向导字段 → MaterialDemand；供给行 → MaterialSupply（缺失值保留 None，
  不静默补 demo 数据：试点应用默认禁止缺真实数据后悄悄注入 demo）。
- 计算全部走 src.material.analyze_task（与静态演示共用同一核心）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass

from src.imports import preflight_demand_rows, preflight_supply_rows, split_issues
from src.material import MaterialDemand, MaterialSupply, analyze_task, load_material_factors

DEMAND_FIELDS = {
    "demand_id", "buyer_name", "target_material_code", "application",
    "required_mass_t", "min_recycled_content_pct", "mfi_min", "mfi_max",
    "max_moisture_pct", "max_ash_pct", "accepted_form", "accepted_color",
    "required_from", "due_date", "destination",
    "baseline_virgin_price_cny_per_t", "max_distance_km",
}

SUPPLY_FIELDS = {
    "supply_id", "supplier_id", "supplier_name", "material_code", "material_name_raw",
    "polymer_type", "form", "color", "grade", "mfi_g_10min", "moisture_pct",
    "ash_pct", "recycled_content_pct", "available_mass_t", "available_from",
    "available_to", "origin", "price_cny_per_t", "price_includes_freight",
    "yield_pct", "inspection_required", "preprocessing", "transport_mode",
    "factor_id", "distance_km", "evidence_source", "evidence_date",
    "evidence_attachment", "source", "external_id",
}

WIZARD_REQUIRED = {"demand_id", "target_material_code", "required_mass_t"}


def _to_float(value):
    """数值解析走唯一领域校验层（千分位统一支持、NaN/Inf 统一拒绝）。"""
    from src.validation import parse_number, ValidationError
    if value is None or value == "":
        return None
    parsed, err = parse_number(value, "value")
    if err:
        raise ValidationError(err["problem"], [err])
    return parsed


def _to_bool(value):
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "y", "是", "含", "含运费")


def demand_from_wizard(payload):
    """需求向导 → MaterialDemand。统一领域校验层（与 CSV/表单一致）。"""
    from src.validation import ensure_valid, validate_demand_payload
    ensure_valid(payload, validate_demand_payload(payload))
    kwargs = {k: payload.get(k) for k in DEMAND_FIELDS if k in payload}
    for field in ("required_mass_t", "min_recycled_content_pct", "mfi_min", "mfi_max",
                  "max_moisture_pct", "max_ash_pct", "baseline_virgin_price_cny_per_t",
                  "max_distance_km"):
        if field in kwargs:
            kwargs[field] = _to_float(kwargs[field])
    return MaterialDemand(**kwargs)


def supply_from_payload(payload):
    """供给行 → MaterialSupply（单行录入与 CSV 导入共用，统一领域校验层）。"""
    from src.validation import ensure_valid, validate_supply_payload
    ensure_valid(payload, validate_supply_payload(payload))
    kwargs = {k: payload.get(k) for k in SUPPLY_FIELDS if k in payload}
    for field in ("mfi_g_10min", "moisture_pct", "ash_pct", "recycled_content_pct",
                  "available_mass_t", "price_cny_per_t", "distance_km", "yield_pct"):
        if field in kwargs:
            kwargs[field] = _to_float(kwargs[field])
    for field in ("price_includes_freight", "inspection_required"):
        if field in kwargs:
            kwargs[field] = _to_bool(kwargs[field])
    for field in SUPPLY_FIELDS - set(kwargs):
        if field in ("price_includes_freight", "inspection_required"):
            kwargs[field] = False
        elif field.endswith("_pct") or field in ("mfi_g_10min", "available_mass_t",
                                                 "price_cny_per_t", "distance_km",
                                                 "yield_pct"):
            kwargs[field] = None
        else:
            kwargs[field] = ""
    return MaterialSupply(**kwargs)


def preflight_import(task_demand_payload, csv_text, kind):
    """导入前体检：返回 {issues, structural, business, preview_count}，不写入。

    预检与正式导入共用同一套解析与领域校验（src.imports → src.validation）。
    """
    import csv as _csv
    import io

    rows = []
    reader = _csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV 为空或缺少表头")
    for line_no, row in enumerate(reader, 2):
        rows.append({"row_no": line_no, "data": row})
    if kind == "supply":
        issues = preflight_supply_rows(rows, "import.csv")
    else:
        issues = preflight_demand_rows(rows, "import.csv")
    structural, business = split_issues(issues)
    return {"issues": issues, "structural": structural, "business": business,
            "preview_count": len(rows)}


def run_engine(demand, supplies):
    """共用计算核心：一次调用产出全部结果快照（与静态演示同一引擎）。"""
    factor_data = load_material_factors()
    result = analyze_task(demand, supplies, factor_data,
                          resolved_mode="local", requested_mode="local",
                          fallback_reason="", mode_note="数据模式：工作台任务（本地）")
    return result, factor_data


def serialize_result(result):
    """结果对象 → JSON 可序列化 dict（dataclass 转 dict）。"""
    def conv(value):
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, dict):
            return {k: conv(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [conv(v) for v in value]
        return value

    return json.loads(json.dumps(conv(result), ensure_ascii=False, default=str))


def demand_to_wizard(demand):
    return asdict(demand)


def supply_to_payload(supply):
    return asdict(supply)
