"""分阶段材料召回：名称标准化 → 同义词/字符 n-gram 召回 → 规格硬门槛。

文本相似只负责扩大候选召回，不得覆盖材料类型、熔指、水分、灰分、颜色、
时窗和证据门槛——准入仍由 match_gates 的硬规则决定（可解释智能决策，
不声称大模型或机器学习）。
"""

from __future__ import annotations

import csv
import re

from .material import match_gates, supply_state

# 版本化同义词词典（演示配置，可扩展；命中词会随结果输出供复核）
_SYNONYMS = {
    "PP": ("pp", "聚丙烯", "聚丙稀", "pp料"),
    "颗粒": ("粒料", "颗粒料", "再生粒", "粒子"),
    "再生": ("回收", "循环", "再生料", "recycled", "rpp"),
    "黑色": ("黑", "black"),
    "灰色": ("灰", "gray", "grey"),
    "注塑级": ("注塑", "注塑成型", "injection"),
}

_NOISE_WORDS = {"牌号", "型号", "定制", "厂家直供", "现货", "量大从优", "现货供应"}
_BRACKET_RE = re.compile(r"（.*?）|\(.*?\)|【.*?】|\[.*?\]")
_NOISE_RE = re.compile(r"[\s\-_/·,，。.]+")

# v13 阈值门控 + 兜底（借鉴 industry-chain-matcher 的「阈值门控 + 兜底」结构思路，
# 未使用其代码、无在线依赖、不涉及任何模型）。
# v12 规则 `similarity >= threshold or 供给名同义词命中` 的缺陷：同义词只在供给名上
# 匹配——含材料代号的供给对任意查询都「命中」（实测：PP 池对「黑色粒料现货」这类
# 无材料语义查询全部被召回），精度漏洞。门控改为两条通道：
#   G1 高相似通道：similarity ≥ RECALL_GATE_HIGH_SIM 直接召回；
#   G2 类别联合通道：查询侧与供给名都含材料类别证据（材料代码或其类别同义词）→ 召回
#      （PP 池 + 类别查询下与 v12 行为逐位一致；差异只在查询无类别语义时）；
#   兜底层：有召回信号（similarity ≥ threshold 或供给侧同义词命中）但未过门控 →
#      recalled=False、tier="fallback"，展示可见、排序位于召回之后、不进指标，供人工复核；
#      其余 tier="out"。替代性准入仍由 match_gates 硬门槛负责，本层只做召回。
RECALL_GATE_HIGH_SIM = 0.45


def normalize_material_name(text):
    """名称标准化：小写、去全角、去噪声词与符号，保留材料核心词。"""
    text = (text or "").lower()
    fullwidth = str.maketrans(
        "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
        "0123456789abcdefghijklmnopqrstuvwxyz()",
    )
    text = text.translate(fullwidth)
    # 括号内容（如颜色）是有效信号：去括号保留内容
    text = _BRACKET_RE.sub(lambda m: " " + m.group(0)[1:-1] + " ", text)
    text = _NOISE_RE.sub(" ", text)
    for word in _NOISE_WORDS:
        text = text.replace(word, " ")
    return " ".join(text.split())


def _bigrams(text):
    text = "".join(text.split())
    if len(text) < 2:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


def dice_similarity(a, b):
    """字符二元组 Dice 相似度（0~1）。空字符串返回 0。"""
    set_a, set_b = _bigrams(a), _bigrams(b)
    if not set_a or not set_b:
        return 0.0
    return 2.0 * len(set_a & set_b) / (len(set_a) + len(set_b))


def matched_synonyms(normalized_name, demand_code):
    """返回命中的同义词条目（材料类别与通用词）。命中必须来自文本本身——
    v13 修正：移除旧实现中「key==材料代码即无条件命中」的分支（它让任何供给
    对应需求都带幻影命中，tier 判定与命中展示失真）。"""
    hits = []
    text = normalized_name
    for key, words in _SYNONYMS.items():
        for word in words:
            if word in text:
                if any(word in hit for hit in hits):
                    continue
                hits.append(f"{key}:{word}")
    if demand_code and demand_code.lower() in text:
        hits.append(f"材料代码:{demand_code.lower()}")
    return hits


def _class_signal(normalized_text, code):
    """材料类别证据：材料代码（含连写如 rpp 中的 pp）或其类别同义词出现在文本中。"""
    code = (code or "").strip()
    if not code:
        return False
    lowered = normalized_text.lower()
    if code.lower() in lowered:
        return True
    return any(word in lowered for word in _SYNONYMS.get(code.upper(), ()))


def recall_candidates(demand, supplies, threshold=0.25, query_name=None,
                      high_threshold=RECALL_GATE_HIGH_SIM):
    """对供给做名称召回：输出每个候选的标准化名、相似度、命中词、召回层级与是否召回。

    query_name：查询文本覆盖（基准评测时传入 case 的查询名；
    生产调用不传则用需求的材料代码+形态+颜色构造）。
    tier：gated=过门控（G1 高相似 / G2 类别联合）；fallback=有信号未过门控（兜底展示，
    不进召回指标）；out=无信号。recalled == (tier == "gated")。
    """
    if query_name is not None and query_name.strip():
        target = normalize_material_name(query_name)
    else:
        target = normalize_material_name(
            f"{demand.target_material_code} {demand.accepted_form} {demand.accepted_color}")
    code = demand.target_material_code
    query_class = _class_signal(target, code)
    results = []
    for supply in supplies:
        raw = normalize_material_name(supply.material_name_raw)
        similarity = round(dice_similarity(target, raw), 4)
        hits = matched_synonyms(raw, code)
        supply_class = _class_signal(raw, code)
        gate_pass = similarity >= high_threshold or (query_class and supply_class)
        fallback_pass = (not gate_pass) and (similarity >= threshold or bool(hits))
        tier = "gated" if gate_pass else ("fallback" if fallback_pass else "out")
        results.append({
            "supply_id": supply.supply_id,
            "raw_name": supply.material_name_raw,
            "normalized": raw,
            "similarity": similarity,
            "matched_synonyms": hits,
            "recalled": gate_pass,
            "tier": tier,
        })
    return results


def run_recall_benchmark(demand, supplies, cases_path=None, threshold=0.25, k_values=(1, 3, 5)):
    """重定义的召回基准：固定完整候选池 + 独立标注 + Top-k 排名。

    评测对象拆分，禁止混用：
    1. 「名称实体归一」recall 任务：查询文本 → 对固定候选池（全部演示供给）排名，
       每条查询独立标注相关批次集合（relevant_supply_ids）与负例类型；
       报告 Recall@k 与 Hit@k（多相关项时二者不同）；空查询必须显式拒绝，
       不得补回正常值。开发集调阈值，保留集只做评估。
    2. 「硬门槛误放行」gate_reject 任务：构造正负规格/临界值/缺证据/材料不兼容/
       时窗不符用例，验证三态判定，按标签报告误放行。

    合成用例不能宣称真实业务准确率；报告样本规模、构造方式、各类错误与原始输出。
    """
    import copy as _copy
    import json as _json

    cases = []
    if cases_path:
        with open(cases_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                cases.append(row)
    pool = list(supplies)
    recall_cases = [c for c in cases if c.get("task") == "recall"]
    gate_cases = [c for c in cases if c.get("task") == "gate_reject"]
    dev_recall = [c for c in recall_cases if c.get("split") == "dev"]
    holdout_recall = [c for c in recall_cases if c.get("split") != "dev"]
    dev_gate = [c for c in gate_cases if c.get("split") == "dev"]
    holdout_gate = [c for c in gate_cases if c.get("split") != "dev"]

    def rank_pool(query_name):
        """对固定候选池排名：recalled 优先，similarity 降序。空查询返回显式拒绝。"""
        if not (query_name or "").strip():
            return None, {"rejected": True,
                          "reason": "空查询被显式拒绝：请先输入材料名称/代码，不返回任何候选"}
        scored = recall_candidates(demand, pool, threshold=threshold, query_name=query_name)
        scored.sort(key=lambda r: (not r["recalled"], -r["similarity"], r["supply_id"]))
        return scored, None

    def eval_recall_set(case_set, split_label):
        rows, per_case = [], {}
        for case in case_set:
            query = (case.get("query_name") or "").strip()
            relevant = [x for x in (case.get("relevant_supply_ids") or "").split(";") if x]
            scored, rejection = rank_pool(query)
            if rejection is not None:
                expected_reject = not relevant  # 空查询且无相关项 → 期望显式拒绝
                ok = expected_reject
                per_case[case["case_id"]] = {"ok": ok, "rejection": rejection["reason"],
                                             "label": case.get("reject_label") or "空查询"}
                rows.append({"case_id": case["case_id"], "split": split_label,
                             "rejected": True, "rejection_reason": rejection["reason"],
                             "expected_empty_reject": expected_reject, "ok": ok})
                continue
            ranking = [r["supply_id"] for r in scored if r["recalled"]]
            full_ranking = [r["supply_id"] for r in scored]
            metrics = {}
            for k in k_values:
                top = ranking[:k]
                hits = [sid for sid in relevant if sid in top]
                metrics[f"recall_at_{k}"] = round(len(hits) / len(relevant), 4) if relevant else None
                metrics[f"hit_at_{k}"] = 1 if any(sid in full_ranking[:k] for sid in relevant) else 0
            # 错误类型：相关项漏召回（漏召回）、无关项被召回（误召回）
            missed = [sid for sid in relevant if sid not in ranking]
            false_hits = [sid for sid in ranking if sid not in relevant]
            ok = (not missed) and (not false_hits)
            per_case[case["case_id"]] = {"ok": ok, "ranking": ranking,
                                         "relevant": relevant, "missed": missed,
                                         "false_hits": false_hits,
                                         "label": case.get("reject_label") or "相关项"}
            rows.append({"case_id": case["case_id"], "split": split_label,
                         "rejected": False, "ranking": ranking, "relevant": relevant,
                         "missed": missed, "false_hits": false_hits,
                         **metrics, "ok": ok})
        return rows, per_case

    recall_rows = []
    recall_rows += eval_recall_set(dev_recall, "dev")[0]
    recall_rows += eval_recall_set(holdout_recall, "holdout")[0]

    def eval_gate_set(case_set, split_label):
        rows, false_pass_by_label = [], {}
        for case in case_set:
            fake = None
            for supply in pool:
                if supply.supply_id == (case.get("source_supply_id") or "").strip():
                    fake = _copy.copy(supply)
                    break
            if fake is None:
                fake = _copy.copy(pool[0]) if pool else type(next(iter(pool), None))()
            overrides = {}
            try:
                overrides = _json.loads(case.get("field_overrides") or "{}")
            except _json.JSONDecodeError:
                overrides = {}
            for field, value in overrides.items():
                setattr(fake, field, value)
            if case.get("query_name"):
                fake.material_name_raw = case["query_name"]
            state = supply_state(demand, fake)
            expected = (case.get("expected_state") or "").strip()
            label = case.get("reject_label") or "未标注"
            actual_state = state["state"]
            # 误放行 = 期望不通过（pending/ineligible）但判为 eligible；
            # 误杀 = 期望通过但未通过
            false_pass = (expected in ("pending_evidence", "ineligible")
                          and actual_state == "eligible")
            false_kill = (expected == "eligible" and actual_state != "eligible")
            ok = actual_state == expected
            false_pass_by_label.setdefault(label, {"n": 0, "false_pass": 0, "false_kill": 0,
                                                   "errors": []})
            false_pass_by_label[label]["n"] += 1
            if false_pass:
                false_pass_by_label[label]["false_pass"] += 1
                false_pass_by_label[label]["errors"].append(case["case_id"])
            if false_kill:
                false_pass_by_label[label]["false_kill"] += 1
                false_pass_by_label[label]["errors"].append(case["case_id"])
            rows.append({"case_id": case["case_id"], "split": split_label,
                         "expected_state": expected, "actual_state": actual_state,
                         "label": label, "false_pass": false_pass, "false_kill": false_kill,
                         "ok": ok, "gates": state["gates"]})
        return rows, false_pass_by_label

    gate_rows = []
    gate_labels = {}
    for split, case_set, split_label in (("dev", dev_gate, "dev"),
                                         ("holdout", holdout_gate, "holdout")):
        rows, labels = eval_gate_set(case_set, split_label)
        gate_rows += rows
        for label, stats in labels.items():
            gate_labels.setdefault(label, {"n": 0, "false_pass": 0, "false_kill": 0,
                                           "errors": [], "splits": {}})
            gate_labels[label]["n"] += stats["n"]
            gate_labels[label]["false_pass"] += stats["false_pass"]
            gate_labels[label]["false_kill"] += stats["false_kill"]
            gate_labels[label]["errors"] += stats["errors"]
            gate_labels[label]["splits"][split_label] = {"n": stats["n"],
                                                         "false_pass": stats["false_pass"],
                                                         "false_kill": stats["false_kill"]}

    # 阈值敏感性：只在开发集上扫网格（保留集不可用来调阈值，不得混入 holdout）
    threshold_grid = []
    for t in (0.0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5):
        scored = recall_candidates(demand, pool, threshold=t)
        recalled_ids = {r["supply_id"] for r in scored if r["recalled"]}
        misses = 0
        for case in dev_recall:
            relevant = [x for x in (case.get("relevant_supply_ids") or "").split(";") if x]
            misses += sum(1 for sid in relevant if sid not in recalled_ids)
        threshold_grid.append({"threshold": t, "recalled_count": len(recalled_ids),
                               "dev_missed_relevant": misses,
                               "dev_n_cases": len(dev_recall)})

    # 稳定性：同一输入跑两遍结果一致
    first = [r["similarity"] for r in recall_candidates(demand, pool, threshold=threshold)]
    second = [r["similarity"] for r in recall_candidates(demand, pool, threshold=threshold)]
    stable = first == second

    def aggregate(rows, split_label):
        agg = {}
        for k in k_values:
            values = [r[f"recall_at_{k}"] for r in rows
                      if r["split"] == split_label and not r.get("rejected")
                      and r.get(f"recall_at_{k}") is not None]
            if values:
                agg[f"recall_at_{k}"] = round(sum(values) / len(values), 4)
            # 空相关集用例（负例/噪声查询）不计入 Hit@k 均值：命中按定义不可能，
            # 其考核目标是「不产生误召回」（ok 判定），混入均值只会稀释口径。
            hits = [r[f"hit_at_{k}"] for r in rows
                    if r["split"] == split_label and not r.get("rejected")
                    and r.get("relevant")]
            if hits:
                agg[f"hit_at_{k}"] = round(sum(hits) / len(hits), 4)
        agg["n_cases"] = len([r for r in rows if r["split"] == split_label])
        agg["errors"] = [r["case_id"] for r in rows
                         if r["split"] == split_label and not r.get("ok")]
        return agg

    empty_query_behavior = [r for r in recall_rows if r.get("rejected")]

    return {
        "benchmark": "人工标注演示集（非生产数据，合成用例不写成真实业务准确率）",
        "candidate_pool_size": len(pool),
        "k_values": list(k_values),
        "recall": {
            "dev": aggregate(recall_rows, "dev"),
            "holdout": aggregate(recall_rows, "holdout"),
            "cases": recall_rows,
            "empty_query_behavior": empty_query_behavior,
            "threshold_grid": threshold_grid,
            "note": ("开发集用于调阈值（threshold_grid 只基于 dev），保留集只评估；"
                     "空查询显式拒绝并计入错误；Recall@k 与 Hit@k 分别报告（多相关项时不同）；"
                     "空相关集用例（噪声/负例查询）不计入 Hit@k 均值，只考核误召回"),
        },
        "gate": {
            "by_label": gate_labels,
            "cases": gate_rows,
            "note": "误放行=期望不通过却判 eligible；误杀=期望通过却未通过；缺证据应为待核验而非通过",
        },
        "stable": stable,
    }
