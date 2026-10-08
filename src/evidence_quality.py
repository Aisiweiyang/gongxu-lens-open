"""证据质量评分：质量规则拆分为独立维度，1 最优方向显式。

借鉴 ecoinvent pedigree 矩阵思路（Edelen & Ingwersen 2018）的轻量实现，
但按 把规则拆清：
- 来源存在：空白字符串视为缺失；
- 来源类型：第三方独立报告 / 供应商自报 / 演示或未标注；
- 独立核验：演示来源不自动获得真实证据最高评级；
- 附件可追溯：证据附件/引用链接可追溯；
- 字段覆盖：关键指标字段覆盖率；
- 时效：证据日期年龄；未来日期、无效日期、过期状态分别标记（flags），不混为一个分；
- 地理代表性：数据对核算地区的代表性（产地/证据地区 vs 需求地区），
  不能简单等同于「离买方近」；
- 技术相关性：用途、工艺（牌号/等级）与需求匹配。

合成规则：
- 各维度 1-5 分（1 最优），几何平均合成质量分；该合成是**启发式**
  （明确标注），不包装成统计置信度；
- 关键缺证据（来源缺失或关键字段缺失）单独输出 critical_missing，
  不被其他维度高分平均抵消；
- 质量分只描述证据强弱，不替代硬门槛判定（门槛该待核验还是待核验）；
- 演示数据的分数全部基于演示虚构证据，不构成对任何真实企业的评价。
"""

import math
import statistics
from datetime import date

# 等级映射（配置化，可被 config/evidence_quality.yaml 覆盖）
GRADE_THRESHOLDS = {"高": 1.5, "中": 2.5, "低": math.inf}

# 来源类型评分（1 最优）
SOURCE_TYPE_SCORES = {
    "第三方独立检测/认证报告": 1,
    "第三方平台/权威数据库": 1,
    "供应商自报+检测报告": 2,
    "供应商自报": 4,
}

_TODAY = None  # 测试注入用


def _today():
    return _TODAY or date.today()


def _source_type(source):
    """来源类型判定（优先级匹配，先判第三方独立语义，再判供应商自报+报告）。

    仅凭文本含「报告」不再自动判为第三方——「供应商自检报告（内部实验室）」
    属供应商自报+检测报告（2 分），不是第三方独立（1 分）。
    """
    text = (source or "").strip()
    if not text:
        return "演示或未标注"
    if "第三方" in text or "认证" in text or "CMA" in text.upper() or "CNAS" in text.upper() \
            or "独立检测" in text:
        return "第三方独立检测/认证报告"
    if "报告" in text or "检测" in text or "自检" in text:
        return "供应商自报+检测报告"
    return "供应商自报"


def evidence_quality_score(supply, demand, rules=None):
    """对一个供给批次做多维证据质量评分，返回各维分数、几何平均分、等级、
    日期标记（flags）与关键缺证据（critical_missing）。"""
    rules = rules or {}
    thresholds = rules.get("grade_thresholds") or GRADE_THRESHOLDS
    dims = {}

    def score(name, value, reason):
        dims[name] = {"score": value, "reason": reason}

    src = (supply.evidence_source or "").strip()
    date_txt = (supply.evidence_date or "").strip()

    # 1. 来源存在（空白=缺失）
    score("来源存在", 1 if src else 5,
          f"有证据来源（{src[:20]}）" if src else "无证据来源，数值无支撑")

    # 2. 来源类型
    src_type = _source_type(src)
    score("来源类型", SOURCE_TYPE_SCORES.get(src_type, 4),
          f"来源类型：{src_type}")

    # 3. 独立核验（演示来源不自动获得真实证据最高评级）
    provenance = (supply.source or "").strip()
    is_demo = provenance == "演示" or ("演示" in src and "非演示" not in src)
    if is_demo:
        score("独立核验", 4, "演示/虚构来源：不自动获得真实证据最高评级，待真实报告替换")
    elif src_type == "第三方独立检测/认证报告":
        score("独立核验", 1, "第三方独立来源")
    elif src:
        score("独立核验", 3, "非独立来源，建议第三方独立核验")
    else:
        score("独立核验", 5, "无来源，无从核验")

    # 4. 附件可追溯（专用附件/引用字段；来源描述文本不算附件）
    attachment = (supply.evidence_attachment or "").strip() \
        or (rules.get("attachment_url_of", {}) or {}).get(supply.supply_id, "")
    if attachment:
        score("附件可追溯", 1, f"有可追溯附件/引用（{attachment[:30]}）")
    elif src:
        score("附件可追溯", 3, "有来源描述但无可追溯附件/链接")
    else:
        score("附件可追溯", 5, "无附件，无法追溯")

    # 5. 字段覆盖（7 项关键字段）
    fields = [supply.mfi_g_10min, supply.moisture_pct, supply.ash_pct,
              supply.recycled_content_pct, supply.price_cny_per_t,
              supply.distance_km, supply.evidence_source]
    filled = sum(1 for f in fields if f is not None)
    missing_names = [n for n, f in zip(
        ("熔指", "水分", "灰分", "再生含量", "价格", "距离", "证据来源"), fields) if f is None]
    completeness = {7: 1, 6: 2, 5: 2, 4: 3, 3: 4}.get(filled, 5)
    score("字段覆盖", completeness,
          f"7 项关键字段已填 {filled} 项" + (f"，缺 {('、'.join(missing_names))}" if missing_names else ""))

    # 6. 时效（年龄维度 + 三种日期状态分开标记）
    flags = []
    temporal = 5
    if date_txt:
        try:
            year, month, day = (int(x) for x in date_txt.split("-")[:3])
            parsed = date(year, month, day)
            if parsed > _today():
                flags.append("未来日期")
                temporal = 4
            else:
                days = (_today() - parsed).days
                temporal = 1 if days <= 365 else 2 if days <= 730 else 3 if days <= 1095 else 4
                if days > 1095:
                    flags.append("过期（超过 3 年）")
        except ValueError:
            flags.append("无效日期")
            temporal = 4
    score("时效", temporal, f"证据日期 {date_txt or '无'}"
          + (f"；标记：{'、'.join(flags)}" if flags else ""))

    # 7. 地理代表性（地区代表性，不是「离买方近」）
    origin = (supply.origin or "").strip()
    destination = (demand.destination or "").strip()
    if origin and destination:
        if origin == destination:
            score("地理代表性", 1, f"产地与需求地区一致（{origin}）")
        elif origin.split("（")[0].split("(")[0] == destination.split("（")[0].split("(")[0]:
            score("地理代表性", 2, f"产地（{origin}）与需求地区（{destination}）属同一地区（近似）")
        else:
            score("地理代表性", 3, f"产地（{origin}）与需求地区（{destination}）不同："
                                   "对核算地区代表性弱，需核算运输且注意因子地区匹配")
    elif origin:
        score("地理代表性", 3, f"有产地（{origin}）但需求目的地未填写，地区代表性无法判断")
    else:
        score("地理代表性", 5, "产地缺失，地理代表性无从判断")

    # 8. 技术相关性（用途/工艺/等级）
    tech = 5
    code_match = (supply.material_code or "").strip().upper() == (demand.target_material_code or "").strip().upper()
    if code_match and supply.grade:
        tech = 1  # 原「grade_match or True」恒真属死代码，有牌号即有工艺依据（1 最优）
    elif code_match:
        tech = 2
    elif supply.material_code:
        tech = 4
    score("技术相关性", tech, f"材料代码{'一致' if code_match else '不一致'}、"
          f"牌号/等级：{supply.grade or '缺失'}；需求用途：{demand.application or '未填写'}")

    values = [d["score"] for d in dims.values()]
    score_value = round(statistics.geometric_mean(values), 2)
    grade = "低"
    for name, limit in sorted(thresholds.items(), key=lambda kv: kv[1]):
        if score_value <= limit:
            grade = name
            break
    critical_missing = []
    if not src:
        critical_missing.append("证据来源缺失")
    for name, value in zip(("熔指", "水分", "灰分", "再生含量"), fields[:4]):
        if value is None:
            critical_missing.append(f"{name}缺失")
    weakest = None
    if score_value > 1.0:
        worst = max(dims.items(), key=lambda kv: kv[1]["score"])
        weakest = worst[0]
    return {"dimensions": dims, "score": score_value, "grade": grade,
            "flags": flags, "critical_missing": critical_missing,
            "weakest": weakest,
            "note": "质量分为启发式合成（几何平均），不是统计置信度；"
                    "关键缺证据不被其他维度高分抵消"}


def load_rules():
    """从 config/evidence_quality.yaml 加载可配置规则（缺失时用模块默认值）。"""
    import yaml
    from . import config as cfg

    path = cfg.CONFIG_DIR / "evidence_quality.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def score_all(supplies, demand):
    """对全部供给批次评分，返回按质量分排序的列表（不修改原始数据）。"""
    from .material import supply_state

    rules = load_rules()
    rows = []
    for supply in supplies:
        quality = evidence_quality_score(supply, demand, rules)
        gate = supply_state(demand, supply)["state"]
        gate_label = {"eligible": "可比较", "pending_evidence": "待核验",
                      "ineligible": "不符合"}[gate]
        rows.append({"supply_id": supply.supply_id,
                     "supplier_name": supply.supplier_name,
                     "score": quality["score"],
                     "grade": quality["grade"],
                     "dimensions": quality["dimensions"],
                     "weakest": quality["weakest"],
                     "flags": quality["flags"],
                     "critical_missing": quality["critical_missing"],
                     "gate_state": gate,
                     "gate_label": gate_label,
                     "gate_note": ("已被硬门槛淘汰：质量分只描述证据强弱，不改变淘汰结论"
                                   if gate == "ineligible" else "")})
    # 排序：先按门槛状态（可比较/待核验/不符合），再按质量分（1 最优 → 升序）
    order = {"eligible": 0, "pending_evidence": 1, "ineligible": 2}
    rows.sort(key=lambda r: (order[r["gate_state"]], r["score"], r["supply_id"]))
    return rows
