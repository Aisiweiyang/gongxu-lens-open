"""批次证据护照 Schema v2：可校验机器可读 JSON，可追溯到输入文件与计算项（）。

v2 新增（相对 v1）：
- run_id / case_id / scenario_id / option_id 全链路身份；
- 生成时间带时区；输入文件哈希与规范化记录哈希分开（文件身份 vs 批次身份，
  哈希不证明源数据真实）；数据来源身份；配置/算法/因子版本；
- 分配、完整账本 trace、未知项、核算边界、证据引用与动作清单；
- 年份不等于有效起始日期；未知不编造。

不实现区块链、DID 或电子签名，也不声称符合任何正式 DPP 标准。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import config
from .ledger import ALGORITHM_VERSION

SCHEMA_VERSION = "2.0.0"
RULE_VERSION = "material-gates-v2"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "material_evidence_passport.schema.v2.json"

_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _now():
    return datetime.now(_TZ).isoformat(timespec="seconds")


def sha256_file(path):
    """文件 SHA-256 十六进制；文件缺失返回 None（演示数据无输入文件 → 显式 null）。"""
    path = Path(path)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_record_sha256(record):
    """规范化记录哈希：把记录规范化（排序键、统一类型）后的 SHA-256，
    用于具体批次/需求身份。文件哈希用于文件身份，两者分开。"""
    if is_dataclass(record):
        payload = asdict(record)
    elif isinstance(record, dict):
        payload = record
    else:
        payload = {"value": str(record)}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value):
    if is_dataclass(value):
        return asdict(value)
    return value


def build_passport(result, run_id=None, case_id=None, scenario_id=None,
                   input_hashes=None, evidence_actions=None):
    """由统一结果对象生成护照 v2 文档。

    input_hashes: {"demand_file": sha256|None, "supply_file": sha256|None, "factor_file": sha256|None}
    """
    demand = result["demand"]
    input_hashes = input_hashes or {}
    supplies = []
    for supply, state in zip(result["supplies"], result["match_states"]):
        supplies.append({
            "supply_id": supply.supply_id,
            "supplier_name": supply.supplier_name,
            "normalized_record_sha256": normalized_record_sha256(supply),
            "material": {
                "material_code": supply.material_code,
                "material_name_raw": supply.material_name_raw,
                "polymer_type": supply.polymer_type,
                "form": supply.form,
                "color": supply.color,
                "grade": supply.grade,
                "mfi_g_10min": supply.mfi_g_10min,
                "moisture_pct": supply.moisture_pct,
                "ash_pct": supply.ash_pct,
                "recycled_content_pct": supply.recycled_content_pct,
            },
            "available_mass_t": supply.available_mass_t,
            "evidence": {
                "source": supply.evidence_source,
                "date": supply.evidence_date,
                "verification_status": state["state"],
                "provenance": supply.source or "未标注",
            },
            "source_grade": "演示假设" if (supply.source or "").strip() == "演示" else "未标注",
        })
    factors = []
    for factor in (result.get("factor_data") or {}).get("factors", {}).values():
        factors.append({
            "factor_id": factor.get("factor_id"),
            "role": factor.get("role"),
            "stage": factor.get("stage"),
            "version": factor.get("version"),
            "boundary": factor.get("boundary"),
            "unit": factor.get("unit"),
            "region": factor.get("region"),
            "year": factor.get("year"),
            "grade": factor.get("grade"),
            "source_name": factor.get("source_name"),
        })
    from .ledger import net_emissions_ledger
    factor_data = result.get("factor_data") or {}
    supplies_by_id = {s.supply_id: s for s in result.get("supplies") or []}
    representative_ids = {o.get("option_id") for o in result.get("representatives") or []}
    options = []
    for option in result.get("options") or []:
        entry = {
            "option_id": option.get("option_id"),
            "kind": option.get("kind"),
            "label": option.get("label"),
            "allocation": option.get("allocation") or {},
            "allocation_labels": option.get("allocation_labels") or {},
            "total_cost_cny": option.get("total_cost_cny"),
            "cost_ledger": option.get("cost_ledger"),
            "net_avoided_kgco2e": option.get("net_avoided_kgco2e"),
            "recycled_mass_t": option.get("recycled_mass_t"),
            "category": option.get("category"),
            "order_max_share": option.get("order_max_share"),
            "problems": option.get("problems") or [],
        }
        # 排放侧账本 trace——单供直接带 terms；代表组合逐批次复算 terms，
        # 使净减排凭护照可复算（活动量×因子值×版本×边界）。
        emission_terms = None
        net = option.get("net")
        if isinstance(net, dict) and net.get("terms"):
            emission_terms = [{"supply_id": k, "terms": net["terms"]}
                              for k in (option.get("allocation") or {})] or [
                {"supply_id": None, "terms": net["terms"]}]
        elif option.get("option_id") in representative_ids and option.get("allocation"):
            terms_by_batch = []
            for supply_id, mass in (option.get("allocation") or {}).items():
                supply = supplies_by_id.get(supply_id)
                if supply is None:
                    continue
                ledger_result = net_emissions_ledger(demand, supply, mass, factor_data)
                terms_by_batch.append({"supply_id": supply_id,
                                       "mass_t": round(mass, 4),
                                       "terms": ledger_result.get("terms") or [],
                                       "status": ledger_result.get("status")})
            emission_terms = terms_by_batch or None
        if emission_terms is not None:
            entry["emission_terms"] = emission_terms
        options.append(entry)
    n1_entries = []
    for entry in result.get("n1_stress") or []:
        n1_entries.append({
            "option_id": entry.get("option_id"),
            "label": entry.get("label"),
            "kind": entry.get("kind"),
            "min_delivery_service_rate": entry.get("min_delivery_service_rate"),
            "worst_case": entry.get("worst_case"),
            "single_point_failures": entry.get("single_point_failures"),
            "model": entry.get("model"),
        })
    unknowns = []
    for supply in result["supplies"]:
        for field in ("price_cny_per_t", "distance_km", "evidence_source", "recycled_content_pct"):
            if getattr(supply, field) is None:
                unknowns.append({"supply_id": supply.supply_id, "field": field,
                                 "status": "未知（未编造）"})
    convention = (result.get("factor_data") or {}).get("accounting_convention") or {}
    factor_file_sha = input_hashes.get("factor_file")
    if factor_file_sha is None:
        factor_file_sha = sha256_file(config.CONFIG_DIR / "material_emission_factors.yaml")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "run_id": run_id,
        "case_id": case_id,
        "scenario_id": scenario_id or result.get("resolved_mode") or result.get("data_mode"),
        "data_mode": {
            "requested": result.get("requested_mode"),
            "resolved": result.get("resolved_mode") or result.get("data_mode"),
            "fallback_reason": result.get("fallback_reason", ""),
        },
        "versions": {
            "algorithm_version": ALGORITHM_VERSION,
            "rule_version": RULE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "factor_config_sha256": factor_file_sha,
        },
        "demand": {
            "demand_id": demand.demand_id,
            "buyer_name": demand.buyer_name,
            "target_material_code": demand.target_material_code,
            "application": demand.application,
            "required_mass_t": demand.required_mass_t,
            "normalized_record_sha256": normalized_record_sha256(demand),
            "input_file_sha256": input_hashes.get("demand_file"),
            "provenance": demand.source or "未标注",
        },
        "input_files": {
            "demand_file_sha256": input_hashes.get("demand_file"),
            "supply_file_sha256": input_hashes.get("supply_file"),
            "factor_file_sha256": factor_file_sha,
            "note": "文件哈希标识文件身份；哈希不能证明源数据真实。",
        },
        "supplies": supplies,
        "factors": factors,
        "accounting_convention": convention,
        "options": options,
        "representatives": [o.get("option_id") for o in result.get("representatives") or []],
        "n1": {
            "comparison_strategies": result.get("n1_compare_strategies", {}).get("results") or n1_entries,
            "comparison_evidence": result.get("n1_compare_evidence"),
        },
        "evidence": {
            "actions": evidence_actions or [],
            "quality": [
                {"supply_id": r.get("supply_id"), "score": r.get("score"),
                 "grade": r.get("grade"), "flags": r.get("flags") or [],
                 "critical_missing": r.get("critical_missing") or [],
                 "gate_state": r.get("gate_state")}
                for r in result.get("quality") or []
            ],
        },
        "unknowns": unknowns,
        "boundaries": {
            "functional_unit": "交付 1 t 满足需方规格的合格颗粒",
            "scope": "原生颗粒生产与运输（基准）、再生颗粒生产与运输（项目）；"
                     "不含使用阶段与处置环节",
            "emission_note": "净减排为情景核算（同一功能单位），非实测减排",
            "demo_note": "演示因子为虚构演示假设因子；未核实数值保持演示身份",
        },
        "next_actions": [
            "对「待核验」批次补送样检测",
            "与最低成本供给议价",
            "核对基准运输距离假设与各批次实际运输路线",
        ],
    }


def validate_against_schema(document, schema, use_jsonschema=True):
    """Schema 校验：优先使用成熟 JSON Schema 验证器（jsonschema），
    不可用时回退内置最小验证器并在结果中注明（）。"""
    if use_jsonschema:
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            use_jsonschema = False
    if use_jsonschema:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
        problems = [f"{'.'.join(str(p) for p in e.path) or '$'}：{e.message}" for e in errors]
        return {"problems": problems, "validator": "jsonschema"}
    return {"problems": _validate_minimal(document, schema), "validator": "内置最小验证器"}


def _validate_minimal(document, schema):
    """内置最小 JSON Schema 校验（type/required/properties/enum/items）。"""
    problems = []

    def check_type(value, expected):
        if expected == "string":
            return isinstance(value, str)
        if expected == "number":
            return isinstance(value, (int, float))
        if expected == "array":
            return isinstance(value, list)
        if expected == "object":
            return isinstance(value, dict)
        if expected == "boolean":
            return isinstance(value, bool)
        return True

    def visit(value, subschema, path):
        if not isinstance(subschema, dict):
            return
        if isinstance(value, dict):
            for key in subschema.get("required", []):
                if key not in value:
                    problems.append(f"{path}.{key} 缺失")
            for key, sub in subschema.get("properties", {}).items():
                if key in value:
                    visit(value[key], sub, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, subschema.get("items", {}), f"{path}[{index}]")
        if "type" in subschema and not check_type(value, subschema["type"]):
            problems.append(f"{path} 类型应为 {subschema['type']}")
        if "enum" in subschema and value not in subschema["enum"]:
            problems.append(f"{path} 不在允许值 {subschema['enum']} 内")

    visit(document, schema, "$")
    return problems


def load_schema_v2():
    if SCHEMA_FILE.exists():
        return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Schema v2 缺失：{SCHEMA_FILE}")


def migrate_v1_to_v2(document_v1):
    """v1 → v2 读取/迁移：补全身份与版本字段，未知字段标注待补（不是编造）。"""
    document = dict(document_v1)
    document["schema_version"] = SCHEMA_VERSION
    document["versions"] = {
        "algorithm_version": document.pop("rules", {}).get("algorithm_version", "unknown-v1"),
        "rule_version": document.pop("rules", {}).get("rule_version", "unknown-v1"),
        "schema_version": SCHEMA_VERSION,
        "migration": "from-v1（身份与账本字段为待补，未编造）",
    }
    document.setdefault("generated_at", _now())
    document.setdefault("run_id", None)
    document.setdefault("case_id", None)
    document.setdefault("scenario_id", None)
    for option in document.get("options") or []:
        option.setdefault("option_id", None)
        option.setdefault("cost_ledger", None)
    document.setdefault("unknowns", [])
    document.setdefault("boundaries", {})
    return document


def export_passport_json(result, path, run_id=None, case_id=None, scenario_id=None,
                         evidence_actions=None):
    """导出护照 v2 JSON：需求/供给文件分别计算哈希（供给文件缺失时对应哈希为空集合）。"""
    input_hashes = {
        "demand_file": sha256_file(config.MATERIAL_DEMAND_CSV)
        if config.MATERIAL_DEMAND_CSV.exists() else None,
        "supply_file": sha256_file(config.MATERIAL_SUPPLY_CSV)
        if config.MATERIAL_SUPPLY_CSV.exists() else None,
        "factor_file": sha256_file(config.CONFIG_DIR / "material_emission_factors.yaml"),
    }
    document = build_passport(result, run_id=run_id, case_id=case_id,
                              scenario_id=scenario_id, input_hashes=input_hashes,
                              evidence_actions=evidence_actions)
    path = Path(path)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document
