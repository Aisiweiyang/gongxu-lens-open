"""因子影子试算（EcoProfiles rPP）：以真实因子覆写副本配置运行演示案例，与生产口径对比。

约束：
- 生产配置 config/ 与产物 output/、site/ 一律不触碰——影子运行只在临时副本
  目录上覆写因子，进程内切换 CONFIG_DIR 并在 finally 恢复；
- 场景 0（无覆写）必须复现生产锚点（保守净减排 20,844 kgCO2e / 基准 83,025
  CNY / 最优 59,820 CNY），不符即中止，证明影子链路无扰动；
- 覆写仅限 DEMO-PP-RECYCLE-PROCESS 的 value/value_min/value_max 及边界描述
  字段（供报告引用）；基准侧原生因子维持演示假设（范围声明见试算报告）。

用法：python3 scripts/shadow_trial.py（零外部依赖；demo 案例走穷举路径）
产物：benchmarks/影子因子-EcoProfiles/影子试算-raw-20260917.md（每次运行重写）
"""

import copy
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent
SHADOW_DIR = BASE / "benchmarks" / "影子因子-EcoProfiles"
OVERRIDES_YAML = SHADOW_DIR / "影子因子集-EcoProfiles-rPP-20260917.yaml"
RAW_OUT = SHADOW_DIR / "影子试算-raw-20260917.md"

ANCHORS = {"baseline_cost": 83025, "cheapest_cost": 59820, "best_net": 20844.0}
RECYCLE_FACTOR_ID = "DEMO-PP-RECYCLE-PROCESS"


def _load_scenarios():
    import yaml
    data = yaml.safe_load(OVERRIDES_YAML.read_text(encoding="utf-8"))
    return data["scenarios"]


def _extract(result):
    """提取对比所需字段；因子被边界兼容性检查拦截时返回 blocked 记录而非崩溃。"""
    options = result["options"]
    computable = [o for o in options
                  if o.get("total_cost_cny") is not None
                  and o.get("net_avoided_kgco2e") is not None]
    baseline = {"baseline_cost": options[0]["total_cost_cny"]}
    if not computable:
        blocked = [p for o in options for p in (o.get("problems") or [])]
        return {**baseline, "blocked": True, "message": blocked[0] if blocked else "",
                "blocked_options": len(options) - len(computable)}
    cheapest = min(computable, key=lambda o: o["total_cost_cny"])
    best_net = max(computable, key=lambda o: o["net_avoided_kgco2e"])
    interval = best_net.get("net_interval_kgco2e")
    return {**baseline, "blocked": False,
            "cheapest_cost": cheapest["total_cost_cny"],
            "best_net": best_net["net_avoided_kgco2e"],
            "best_net_label": best_net.get("label", best_net.get("option_id", "")),
            "best_net_interval": interval}


def _run_with_override(overrides):
    """在临时副本配置上覆写因子后运行演示案例；返回统一结果对象。"""
    import yaml
    from src import config as config_mod

    tmp = tempfile.mkdtemp(prefix="shadow-factors-")
    shadow_config = Path(tmp) / "config"
    shutil.copytree(BASE / "config", shadow_config)
    factors_path = shadow_config / "material_emission_factors.yaml"
    data = yaml.safe_load(factors_path.read_text(encoding="utf-8"))
    by_id = {item["factor_id"]: item for item in data.get("factors", [])}
    for override in overrides:
        item = by_id.get(override["factor_id"])
        assert item is not None, f"覆写未命中：{override['factor_id']}"
        item["value"] = float(override["value"])
        if override.get("value_min") is not None:
            item["value_min"] = float(override["value_min"])
            item["value_max"] = float(override["value_max"])
        item["boundary"] = override["boundary"]
        item["region"] = override["region"]
        item["year"] = int(override["year_int"])
        item["source_name"] = override["source_name"]
        item["source_url"] = override["source_url"]
        item["grade"] = "权威"
    factors_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    original = config_mod.CONFIG_DIR
    try:
        config_mod.CONFIG_DIR = shadow_config
        from src.material import run_material
        return _extract(run_material("demo"))
    finally:
        config_mod.CONFIG_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    production = _run_production()
    for key, expected in ANCHORS.items():
        actual = production[key]
        assert abs(actual - expected) < 0.5, f"生产锚点漂移：{key} {actual} != {expected}"
    print("场景 0（生产口径）复现锚点：", production)

    rows = [("production（演示因子，无覆写）", production)]
    for scenario in _load_scenarios():
        result = _run_with_override(scenario["overrides"])
        parts = []
        for o in scenario["overrides"]:
            seg = f"{o['factor_id']}={o['value']:.0f}"
            if o.get("value_min") is not None:
                seg += f"[{o['value_min']:.0f},{o['value_max']:.0f}]"
            parts.append(seg)
        rows.append((f"{scenario['scenario']}（{' + '.join(parts)}）", result))
        print("场景完成：", scenario["scenario"], result)

    lines = ["# 影子试算原始输出 · EcoProfiles rPP（脚本生成，每次运行重写）", "",
             "| 场景 | 基准成本 CNY | 最低成本 CNY | 最大保守净减排 kgCO2e | 代表方案 | 净减排区间 [min, max] |",
             "|---|---|---|---|---|---|"]
    for name, r in rows:
        if r.get("blocked"):
            lines.append(f"| {name} | {r['baseline_cost']:,.0f} | — | — | "
                         f"被边界兼容性检查拦截（{r['blocked_options']} 方案不可计算） | — |")
            lines.append(f"| ↳ 拦截信息 | {r['message']} |||||")
            continue
        interval = r["best_net_interval"]
        if interval and isinstance(interval[0], (int, float)):
            itxt = f"[{interval[0]:,.2f}, {interval[1]:,.2f}]"
        else:
            itxt = "—（因子无区间）"
        lines.append(
            f"| {name} | {r['baseline_cost']:,.0f} | {r['cheapest_cost']:,.0f} | "
            f"{r['best_net']:,.2f} | {r['best_net_label']} | {itxt} |")
    lines += ["", "口径：demo 固定案例（1 需求 × 4 供给，15 t）；各场景仅覆写列明因子，",
              "其余输入维持生产口径；成本不受材料因子影响（可计算场景成本列一致）；",
              "区间为确定性极值传播，非统计置信区间。生产锚点断言通过后方可产出本文件。"]
    RAW_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("raw 写入：", RAW_OUT.name)
    return 0


def _run_production():
    from src.material import run_material
    return _extract(run_material("demo"))


if __name__ == "__main__":
    sys.exit(main())
