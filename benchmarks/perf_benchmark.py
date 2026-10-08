"""性能基准：10/50/100 个候选批次下的建模/求解/渲染耗时与内存。

- 小规模保留穷举作为可解释基准；规模增加使用成熟优化器（当前：组合爆炸时
  增大离散步长 + 完整枚举上限保护；OR-Tools 集成见 docs/开源借鉴落实-v10.md，
  未安装不冒充已使用）。
- 返回真实状态：optimal/feasible/timeout/infeasible；只有穷举证实才称全局最优。
- 缓存按输入、配置、因子和算法版本失效（结果快照带 config_sha256 与 algorithm_version）。

用法：python benchmarks/perf_benchmark.py [--n 10 50 100] [--out docs/性能基准-v10.md]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.material import (MaterialDemand, MaterialSupply, analyze_task,
                          load_material_factors, simulate_supply_mix)
from src.passport import sha256_file
from src import config


def make_supplies(n):
    """构造 n 个候选批次：规格合格、价格/距离有梯度、部分缺证据（真实混合场景）。"""
    supplies = []
    for i in range(n):
        supplies.append(MaterialSupply(
            supply_id=f"BENCH-{i:03d}",
            supplier_name=f"基准供应商{i:03d}",
            material_code="PP",
            material_name_raw=f"再生PP颗粒（黑色）{i:03d}",
            polymer_type="PP", form="颗粒", color="黑色", grade="注塑级",
            mfi_g_10min=10.0 + (i % 6), moisture_pct=0.3, ash_pct=3.0,
            recycled_content_pct=90.0 + (i % 10),
            available_mass_t=2.0 + (i % 9),  # 2~10 t
            available_from="2026-09-10", available_to="2026-10-31",
            origin=f"区域{i % 5}", price_cny_per_t=3600.0 + 20 * (i % 21),
            price_includes_freight=False, yield_pct=100.0, inspection_required=False,
            preprocessing="已造粒", transport_mode="公路货运",
            factor_id="DEFRA-ARTIC-HGV-DIESEL-2024",
            distance_km=20.0 + 10 * (i % 45),
            evidence_source=("检测报告" if i % 4 != 3 else ""),
            evidence_date=("2026-08-01" if i % 4 != 3 else ""),
            source="基准",
        ))
    return supplies


def run_case(n, factor_data, step_t=0.5):
    demand = MaterialDemand(
        demand_id=f"BENCH-D{n}", buyer_name="基准需方", target_material_code="PP",
        application="非食品接触周转箱", required_mass_t=15.0,
        min_recycled_content_pct=80.0, mfi_min=8.0, mfi_max=16.0,
        max_moisture_pct=0.5, max_ash_pct=5.0, accepted_form="颗粒",
        accepted_color="黑色", required_from="2026-09-15", due_date="2026-10-15",
        destination="基准园区", baseline_virgin_price_cny_per_t=5500.0,
        max_distance_km=500.0, evidence_source="基准", source="基准",
    )
    supplies = make_supplies(n)
    tracemalloc.start()
    t0 = time.perf_counter()
    result = analyze_task(demand, supplies, factor_data, mix_step_t=step_t)
    engine_ms = (time.perf_counter() - t0) * 1000
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 渲染耗时：报告 Markdown + 站点 HTML
    t1 = time.perf_counter()
    from src.report import build_material_markdown
    build_material_markdown(result)
    render_report_ms = (time.perf_counter() - t1) * 1000
    t2 = time.perf_counter()
    from src.site_builder import build_site_html
    build_site_html(result)
    render_site_ms = (time.perf_counter() - t2) * 1000

    mix = result["mix"]
    eligible = sum(1 for st in result["match_states"] if st["state"] == "eligible")
    if mix.get("error"):
        status = mix.get("solver_status") or "infeasible"
        optimality = mix.get("optimality") or "见 mix.error"
    elif mix.get("is_complete"):
        status, optimality = "optimal", "穷举证实：完整可行集 + 非支配前沿"
    else:
        status, optimality = "feasible", "部分结果"
    return {
        "n_supplies": n,
        "eligible": eligible,
        "feasible_combos": mix.get("feasible_count") if isinstance(mix, dict) else None,
        "frontier": mix.get("frontier_count") if isinstance(mix, dict) else None,
        "options": len(result["options"]),
        "solver_status": status,
        "optimality": optimality,
        "step_t": step_t,
        "engine_ms": round(engine_ms, 1),
        "report_render_ms": round(render_report_ms, 1),
        "site_render_ms": round(render_site_ms, 1),
        "peak_memory_kb": peak // 1024,
        "note": "optimal=穷举证实；feasible=超限回退的贪心可行解（未证实最优）；infeasible=无可行解",
    }


def main():
    parser = argparse.ArgumentParser(description="供需透镜性能基准")
    parser.add_argument("--n", type=int, nargs="+", default=[10, 50, 100])
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                             / "docs" / "性能基准-v10.md"))
    args = parser.parse_args()

    factor_data = load_material_factors()
    config_sha = sha256_file(config.CONFIG_DIR / "material_emission_factors.yaml")
    machine = {"platform": sys.platform, "python": sys.version.split()[0]}
    rows = []
    for n in args.n:
        case = run_case(n, factor_data, step_t=0.5)
        rows.append(case)
        print(json.dumps(case, ensure_ascii=False))
    # 可证实最优的粗步长配置（步数 ≤ MAX_COMBINATIONS 内，穷举完整可行集）
    coarse = run_case(10, factor_data, step_t=5.0)
    print(json.dumps(coarse, ensure_ascii=False))
    lines = ["# 性能基准", "",
             f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；机器：{machine}；",
             f"算法版本：material-v8；因子配置哈希：{config_sha[:16]}…；",
             "需求 15 t。两个配置：细步长 0.5 t（组合超限时回退贪心可行解+真实"
             "最优性状态，不冒充全局最优）；粗步长 5 t（穷举完整可行集，optimal=穷举证实）。",
             "规模更大时按 docs/开源借鉴落实-v10.md 引入成熟优化器并互验。", "",
             "| 候选批次 | 步长 t | eligible | 可行组合 | 前沿 | 选项数 | 求解状态 | 最优性 | 引擎 ms | 报告渲染 ms | 站点渲染 ms | 峰值内存 KB |",
             "|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|"]
    for r in rows + [coarse]:
        lines.append(f"| {r['n_supplies']} | {r['step_t']:g} | {r['eligible']} | "
                     f"{r['feasible_combos'] if r['feasible_combos'] is not None else '-'} | "
                     f"{r['frontier'] if r['frontier'] is not None else '-'} | {r['options']} | "
                     f"{r['solver_status']} | {r['optimality']} | "
                     f"{r['engine_ms']} | {r['report_render_ms']} | {r['site_render_ms']} | "
                     f"{r['peak_memory_kb']} |")
    lines += ["", "## 超时与无解策略", "",
              "- 计算时间上限：完整枚举组合数超过 MAX_COMBINATIONS（20,000）时，引擎"
              "返回超限说明 + 贪心可行解（solver_status=feasible、optimality=未证实最优），"
              "并给出建议步长，不静默截断。",
              "- 无解（无可行组合）：输出冲突/缺口解释（见 simulate_supply_mix 的 error 字段）。",
              "- 缓存失效键：输入、配置、因子与算法版本（runs 表 config_sha256 + result.algorithm_version）。",
              "- 结论：小规模保留穷举作为可解释基准；更大规模引入 OR-Tools 时与穷举互验"
              "（见开源借鉴落实文档，未安装不冒充已使用）。", ""]
    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
