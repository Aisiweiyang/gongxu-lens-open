"""数据导入前体检（）：行级错误定位与分级，拒绝静默丢行、静默修正。

分级：
- structure（结构错误）：缺必要列、ID 缺失/重复、数值非法（NaN/Inf/负价格/负质量）、
  比例越界、非法日期、时窗倒挂。存在结构错误时 local 模式默认拒绝整批导入
  （strict=True 抛 ValueError 并附完整问题清单），并给出行级修复建议。
- business（业务待补证）：字段缺失但可通过补证动作获得（如无检测报告、缺价）。
  不静默修正，原样保留缺失，由证据任务流程显式补证。
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

SUPPLY_REQUIRED = {"supply_id", "supplier_name", "material_code", "available_mass_t"}
DEMAND_REQUIRED = {"demand_id", "buyer_name", "target_material_code", "required_mass_t"}

SUPPLY_NUMERIC_FIELDS = ("mfi_g_10min", "moisture_pct", "ash_pct", "recycled_content_pct",
                         "available_mass_t", "price_cny_per_t", "distance_km", "yield_pct")
DEMAND_NUMERIC_FIELDS = ("required_mass_t", "min_recycled_content_pct", "mfi_min", "mfi_max",
                         "max_moisture_pct", "max_ash_pct", "baseline_virgin_price_cny_per_t",
                         "max_distance_km")

PERCENT_FIELDS = ("moisture_pct", "ash_pct", "recycled_content_pct", "min_recycled_content_pct",
                  "max_moisture_pct", "max_ash_pct", "yield_pct")


def _clean(value):
    return str(value or "").strip()


def parse_date_text(value):
    """解析 YYYY-MM-DD；返回 (ok, date_obj) 或 (False, 原因)。"""
    text = _clean(value)
    if not text:
        return True, None  # 缺失 = 业务待补证，不在此判结构错误
    if not _DATE_RE.fullmatch(text):
        return False, f"日期格式应为 YYYY-MM-DD，实际为「{text}」"
    try:
        year, month, day = (int(x) for x in text.split("-"))
        parsed = date(year, month, day)
    except ValueError:
        return False, f"非法日期「{text}」"
    if parsed > date.today():
        return True, parsed  # 未来日期合法输入，标记在质量/有效性维度
    return True, parsed


def preflight_supply_rows(rows, file_name):
    """供给行级体检。rows: [{"row_no": 行号(2 起), "data": dict}, ...]。

    解析与领域校验统一委托 src.validation（与表单/API/正式导入同一套），
    本层只负责行号上下文、必填列、重复 ID 与实体身份一致性。
    返回 issues：{file, row, col, problem, suggestion, severity}。
    """
    from .validation import validate_supply_payload
    issues = []
    seen_ids = {}
    for item in rows:
        line_no, row = item["row_no"], item["data"]
        for field in ("supplier_name", "material_code", "available_mass_t"):
            if not _clean(row.get(field)):
                issues.append(dict(file=file_name, row=line_no, col=field,
                                   problem=f"缺少必填字段 {field}",
                                   suggestion=f"填写 {field}", severity="structure"))
        if not _clean(row.get("material_code")) and not _clean(row.get("polymer_type")):
            issues.append(dict(file=file_name, row=line_no, col="material_code",
                               problem="material_code 与 polymer_type 均缺失",
                               suggestion="填写材料代码（如 PP）", severity="business"))
        rid = _clean(row.get("supply_id"))
        if rid:
            if rid in seen_ids:
                first_line, first_name = seen_ids[rid]
                conflict = _clean(row.get("supplier_name")) != first_name
                issues.append(dict(
                    file=file_name, row=line_no, col="supply_id",
                    problem=(f"supply_id 重复：{rid}（与第 {first_line} 行"
                             + ("内容冲突" if conflict else "重复") + "）"),
                    suggestion="为每条供给分配稳定唯一批次编号后重导（单行幂等提交除外，"
                               "同一批导入文件内不允许重复 ID）",
                    severity="structure"))
                continue
            seen_ids[rid] = (line_no, _clean(row.get("supplier_name")))
        # 统一领域校验（同一套解析：千分位、NaN、范围、日期）
        for err in validate_supply_payload(row):
            issues.append(dict(file=file_name, row=line_no, col=err["field"],
                               problem=err["problem"], suggestion=err["suggestion"],
                               severity="structure"))
    # 供应商实体身份一致性提示（业务级，不阻断）
    name_to_ids, id_to_names = {}, {}
    for item in rows:
        sid = _clean(item["data"].get("supplier_id"))
        name = _clean(item["data"].get("supplier_name"))
        if not sid or not name:
            continue
        name_to_ids.setdefault(name, set()).add(sid)
        id_to_names.setdefault(sid, set()).add(name)
    for name, ids in name_to_ids.items():
        if len(ids) > 1:
            issues.append(dict(file=file_name, row=0, col="supplier_id",
                               problem=f"同一供应商名「{name}」对应多个 supplier_id（{'、'.join(sorted(ids))}）",
                               suggestion="N-1 失效实体按 supplier_id 聚合：同名不同 ID 会被视为不同实体，请核对",
                               severity="business"))
    for sid, names in id_to_names.items():
        if len(names) > 1:
            issues.append(dict(file=file_name, row=0, col="supplier_name",
                               problem=f"同一 supplier_id「{sid}」对应多个供应商名（{'、'.join(sorted(names))}）",
                               suggestion="同 ID 不同名会被视为同一实体，请统一名称",
                               severity="business"))
    return issues


def preflight_demand_rows(rows, file_name):
    """需求行级体检（统一委托 src.validation）。含重复 demand_id 检测。"""
    from .validation import validate_demand_payload
    issues = []
    seen_ids = {}
    for item in rows:
        line_no, row = item["row_no"], item["data"]
        for field in ("buyer_name", "target_material_code", "required_mass_t"):
            if not _clean(row.get(field)):
                issues.append(dict(file=file_name, row=line_no, col=field,
                                   problem=f"缺少必填字段 {field}",
                                   suggestion=f"填写 {field}", severity="structure"))
        did = _clean(row.get("demand_id"))
        if did:
            if did in seen_ids:
                first_line = seen_ids[did]
                issues.append(dict(
                    file=file_name, row=line_no, col="demand_id",
                    problem=f"demand_id 重复：{did}（与第 {first_line} 行）",
                    suggestion="为每条需求分配稳定唯一编号（多条需求请逐条建任务）",
                    severity="structure"))
                continue
            seen_ids[did] = line_no
        for err in validate_demand_payload(row):
            issues.append(dict(file=file_name, row=line_no, col=err["field"],
                               problem=err["problem"], suggestion=err["suggestion"],
                               severity="structure"))
    return issues


def format_issues(issues):
    """把 issues 列表格式化为可读多行文本（CLI / API 错误消息）。"""
    lines = []
    for issue in issues:
        lines.append(f"{issue['file']} 第 {issue['row']} 行 {issue['col']} 列"
                     f"[{issue['severity']}]：{issue['problem']} → {issue['suggestion']}")
    return "\n".join(lines)


def split_issues(issues):
    structural = [i for i in issues if i["severity"] == "structure"]
    business = [i for i in issues if i["severity"] == "business"]
    return structural, business
