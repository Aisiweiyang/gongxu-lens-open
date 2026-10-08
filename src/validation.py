"""唯一领域校验层：网页表单、JSON API、CSV 预检与正式导入共同调用。

- 数量/价格/距离/百分比给出合理范围与单位；日期必须真实可解析且满足业务时间关系；
- NaN/Infinity、千分位、错误日期、负数、超长字符串统一在此拒绝或规范化；
- 前端校验只改善体验，服务端以此为准；无效请求返回结构化 4xx 且不产生写入。

错误结构：{"field": 字段名, "problem": 问题, "suggestion": 建议}
"""

from __future__ import annotations

import math
import re
from datetime import date

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MAX_ID_LEN = 64
_MAX_NAME_LEN = 200

DEMAND_NUMERIC_FIELDS = (
    ("required_mass_t", 0.0, 1e6, False, "需求量 (t)"),
    ("min_recycled_content_pct", 0.0, 100.0, True, "再生含量下限 (%)"),
    ("mfi_min", 0.0, 1000.0, False, "熔指下限 (g/10min)"),
    ("mfi_max", 0.0, 1000.0, False, "熔指上限 (g/10min)"),
    ("max_moisture_pct", 0.0, 100.0, True, "水分上限 (%)"),
    ("max_ash_pct", 0.0, 100.0, True, "灰分上限 (%)"),
    ("baseline_virgin_price_cny_per_t", 0.0, 1e7, False, "基准原生价 (CNY/t)"),
    ("max_distance_km", 0.0, 20000.0, False, "距离上限 (km)"),
)

SUPPLY_NUMERIC_FIELDS = (
    ("mfi_g_10min", 0.0, 1000.0, False, "熔指 (g/10min)"),
    ("moisture_pct", 0.0, 100.0, True, "水分 (%)"),
    ("ash_pct", 0.0, 100.0, True, "灰分 (%)"),
    ("recycled_content_pct", 0.0, 100.0, True, "再生含量 (%)"),
    ("available_mass_t", 0.0, 1e6, False, "可供数量 (t)"),
    ("price_cny_per_t", 0.0, 1e7, False, "价格 (CNY/t)"),
    ("distance_km", 0.0, 20000.0, False, "距离 (km)"),
    ("yield_pct", 0.0, 200.0, True, "收率 (%)"),
)


class ValidationError(ValueError):
    """携带结构化字段错误的领域校验异常（API 层转成 400 + details）。"""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or []


def parse_number(text, field, allow_thousands=True):
    """统一数值解析：支持千分位（如 "4,000"），拒绝 NaN/Infinity。

    返回 (value, error)；value 为 None 且 error 为空表示字段留空。
    """
    if text is None:
        return None, None
    raw = str(text).strip()
    if raw == "":
        return None, None
    if allow_thousands:
        cleaned = raw.replace(",", "")
        # 千分位格式宽松校验：形如 1,234 / 1,234.5；拒绝 "1,2,3"
        if "," in raw:
            parts = raw.split(".")
            int_part = parts[0]
            if not re.fullmatch(r"\d{1,3}(,\d{3})*", int_part):
                return None, {"field": field,
                              "problem": f"数值格式非法：{raw!r}（千分位格式应为 1,234 或 1,234.5）",
                              "suggestion": "改为纯数字或标准千分位格式"}
    else:
        cleaned = raw
    try:
        value = float(cleaned)
    except ValueError:
        return None, {"field": field, "problem": f"数值格式非法：{raw!r}",
                      "suggestion": "改为有限数字"}
    if not math.isfinite(value):
        return None, {"field": field,
                      "problem": f"{field} 必须为有限数字（拒绝 NaN/Infinity）",
                      "suggestion": "填写有限数字"}
    return value, None


def parse_date(text, field):
    """日期必须真实可解析。返回 (iso_string, error)；空串返回 (None, None)。"""
    if text is None:
        return None, None
    raw = str(text).strip()
    if raw == "":
        return None, None
    if not _DATE_RE.fullmatch(raw):
        return None, {"field": field, "problem": f"日期格式非法：{raw!r}（应为 YYYY-MM-DD）",
                      "suggestion": "改为 YYYY-MM-DD"}
    try:
        year, month, day = (int(x) for x in raw.split("-"))
        date(year, month, day)
    except ValueError:
        return None, {"field": field, "problem": f"非法日期：{raw!r}（不存在这一天）",
                      "suggestion": "填写真实存在的日期"}
    return raw, None


def _check_length(value, field, max_len):
    if value is not None and len(str(value)) > max_len:
        return {"field": field, "problem": f"{field} 超过 {max_len} 字符",
                "suggestion": f"缩短至 {max_len} 字符以内"}
    return None


def validate_demand_payload(payload):
    """需求 payload 领域校验（表单/API/CSV 共用）。返回 errors 列表。"""
    errors = []
    payload = payload or {}
    for field in ("demand_id", "target_material_code"):
        if not str(payload.get(field) or "").strip():
            errors.append({"field": field, "problem": f"缺少必填字段 {field}",
                           "suggestion": f"填写 {field}"})
    if payload.get("required_mass_t") in (None, ""):
        errors.append({"field": "required_mass_t", "problem": "缺少必填字段 required_mass_t",
                       "suggestion": "填写正数需求量"})
    for field, low, high, _include_high, label in DEMAND_NUMERIC_FIELDS:
        value, err = parse_number(payload.get(field), field)
        if err:
            errors.append(err)
            continue
        if value is None:
            continue
        if field == "required_mass_t" and value <= 0:
            errors.append({"field": field, "problem": f"{label}必须为正数（{value}）",
                           "suggestion": "改为正数"})
        elif field != "required_mass_t" and value < 0:
            errors.append({"field": field, "problem": f"{label}不得为负（{value}）",
                           "suggestion": "改为非负数"})
        elif value > high:
            errors.append({"field": field, "problem": f"{label}超过上限 {high:g}（{value}）",
                           "suggestion": f"改为 ≤ {high:g}"})
    from_field = payload.get("required_from")
    due_field = payload.get("due_date")
    from_value, err = parse_date(from_field, "required_from")
    if err:
        errors.append(err)
    due_value, err = parse_date(due_field, "due_date")
    if err:
        errors.append(err)
    if from_value and due_value and from_value > due_value:
        errors.append({"field": "due_date",
                       "problem": f"需求时窗倒挂：required_from（{from_value}）晚于 due_date（{due_value}）",
                       "suggestion": "核对起止日期"})
    for field, max_len in (("demand_id", _MAX_ID_LEN), ("buyer_name", _MAX_NAME_LEN),
                           ("application", _MAX_NAME_LEN)):
        err = _check_length(payload.get(field), field, max_len)
        if err:
            errors.append(err)
    if payload.get("mfi_min") not in (None, "") and payload.get("mfi_max") not in (None, ""):
        mfi_min, _ = parse_number(payload.get("mfi_min"), "mfi_min")
        mfi_max, _ = parse_number(payload.get("mfi_max"), "mfi_max")
        if mfi_min is not None and mfi_max is not None and mfi_min > mfi_max:
            errors.append({"field": "mfi_max",
                           "problem": f"熔指范围倒挂：下限 {mfi_min:g} > 上限 {mfi_max:g}",
                           "suggestion": "核对熔指范围"})
    return errors


def validate_supply_payload(payload):
    """供给 payload 领域校验（表单/API/CSV 共用）。"""
    errors = []
    payload = payload or {}
    for field in ("supply_id",):
        if not str(payload.get(field) or "").strip():
            errors.append({"field": field, "problem": f"缺少必填字段 {field}",
                           "suggestion": f"填写 {field}"})
    for field, low, high, _include_high, label in SUPPLY_NUMERIC_FIELDS:
        value, err = parse_number(payload.get(field), field)
        if err:
            errors.append(err)
            continue
        if value is None:
            continue
        if value < low:
            errors.append({"field": field, "problem": f"{label}不得为负（{value}）",
                           "suggestion": "改为非负数"})
        elif value > high:
            errors.append({"field": field, "problem": f"{label}超过上限 {high:g}（{value}）",
                           "suggestion": f"改为 ≤ {high:g}"})
    from_value, err = parse_date(payload.get("available_from"), "available_from")
    if err:
        errors.append(err)
    to_value, err = parse_date(payload.get("available_to"), "available_to")
    if err:
        errors.append(err)
    if from_value and to_value and from_value > to_value:
        errors.append({"field": "available_to",
                       "problem": f"可用时窗倒挂：available_from（{from_value}）晚于 available_to（{to_value}）",
                       "suggestion": "核对起止日期"})
    _, err = parse_date(payload.get("evidence_date"), "evidence_date")
    if err:
        errors.append(err)
    for field, max_len in (("supply_id", _MAX_ID_LEN), ("supplier_id", _MAX_ID_LEN),
                           ("supplier_name", _MAX_NAME_LEN),
                           ("material_name_raw", _MAX_NAME_LEN)):
        err = _check_length(payload.get(field), field, max_len)
        if err:
            errors.append(err)
    return errors


def ensure_valid(payload, errors):
    """errors 非空时抛出带 details 的 ValidationError。"""
    if errors:
        raise ValidationError(
            "；".join(f"{e['field']}：{e['problem']}" for e in errors[:5])
            + (f"（共 {len(errors)} 项）" if len(errors) > 5 else ""),
            details=errors)
