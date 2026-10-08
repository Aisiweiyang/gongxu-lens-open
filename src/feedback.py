"""人工反馈记录：只做统计与复盘，不自动修改任何权重。

壁垒是「真实采购人员持续审核形成的领域标注与规则迭代记录」，
不是预留一个大模型 API。没有足够、经审核的标注数据前，本模块不提供
任何自动改权重的代码路径——评审对权重的调整必须显式改 config/scoring.yaml。
"""

from __future__ import annotations

import csv
from collections import Counter

_COLUMNS = {"scenario_id", "candidate_id", "decision", "reason_code", "notes", "reviewed_at", "rule_version"}


def load_feedback_csv(path):
    """读取人工反馈 CSV（templates/review_feedback.csv），返回 (records, stats)。"""
    stats = {"read": 0, "accepted": 0, "rejected": 0, "warnings": []}
    records = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = _COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError("反馈 CSV 缺少字段：" + "、".join(sorted(missing)))
        for line_no, row in enumerate(reader, 2):
            stats["read"] += 1
            if not _clean(row.get("scenario_id")) or not _clean(row.get("candidate_id")):
                stats["warnings"].append(f"第 {line_no} 行缺少 scenario_id 或 candidate_id，已剔除")
                stats["rejected"] += 1
                continue
            records.append(
                {
                    "scenario_id": _clean(row.get("scenario_id")),
                    "candidate_id": _clean(row.get("candidate_id")),
                    "decision": _clean(row.get("decision")),
                    "reason_code": _clean(row.get("reason_code")),
                    "notes": _clean(row.get("notes")),
                    "reviewed_at": _clean(row.get("reviewed_at")),
                    "rule_version": _clean(row.get("rule_version")),
                }
            )
            stats["accepted"] += 1
    return records, stats


def feedback_stats(records):
    """反馈统计：总数、结论分布、原因代码分布、规则版本分布。只读统计。"""
    return {
        "total": len(records),
        "by_decision": dict(Counter(record.get("decision") or "" for record in records)),
        "by_reason_code": dict(Counter(record.get("reason_code") or "" for record in records)),
        "by_rule_version": dict(Counter(record.get("rule_version") or "" for record in records)),
    }


def _clean(value):
    return str(value or "").strip()


ALLOWED_DECISIONS = {"通过", "否决", "补证"}


def review_feedback(records, system_states=None):
    """反馈复盘：校验 + 分歧矩阵 + 原因统计 + 只读建议。

    system_states: {(scenario_id, candidate_id): 系统状态}，可选；
    无足够真实样本时明确「尚不能校准」。不提供任何自动修改评分配置的代码路径。
    """
    validation = {"total": len(records), "invalid_decision": 0, "invalid_date": 0,
                  "missing_rule_version": 0, "duplicates_removed": 0}
    clean = []
    seen = set()
    for record in records:
        key = (record.get("scenario_id"), record.get("candidate_id"), record.get("reviewed_at"))
        if key in seen:
            validation["duplicates_removed"] += 1
            continue
        seen.add(key)
        if record.get("decision") not in ALLOWED_DECISIONS:
            validation["invalid_decision"] += 1
        if record.get("reviewed_at") and not _looks_like_date(record["reviewed_at"]):
            validation["invalid_date"] += 1
        if not record.get("rule_version"):
            validation["missing_rule_version"] += 1
        clean.append(record)
    disagreement = {}
    if system_states:
        for record in clean:
            state = system_states.get((record.get("scenario_id"), record.get("candidate_id")))
            if not state:
                continue
            pair = (state, record.get("decision"))
            disagreement[pair] = disagreement.get(pair, 0) + 1
    suggestions = []
    reason_counts = {}
    for record in clean:
        reason = record.get("reason_code") or ""
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if reason_counts:
        top = sorted(reason_counts.items(), key=lambda kv: -kv[1])[:3]
        suggestions = [
            f"建议复核规则：原因代码「{reason}」出现 {count} 次" for reason, count in top
        ]
    sufficient = len(clean) >= 20
    return {
        "validation": validation,
        "disagreement_matrix": disagreement,
        "reason_counts": reason_counts,
        "suggestions": suggestions,
        "calibration_status": "可校准" if sufficient else "尚不能校准（有效反馈不足 20 条，不声称模型学习）",
        "note": "只生成统计与建议；评分配置的修改必须人工显式编辑 config/scoring.yaml",
    }


def _looks_like_date(value):
    import re
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""))
