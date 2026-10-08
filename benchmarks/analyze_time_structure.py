"""v18 任务 4：完整分析耗时结构测量（纯测量，不改任何产品代码）。

回答「大案例端到端 809s（docs/压力基准-v16.md 第五节）花在哪」：
对 5/8/12/16/20/30 供给（与 solver_benchmark.make_case 同源）分段计时——
  ① simulate_supply_mix（前沿求解，v13/v14 基准测的就是这一段）
  ② build_options（完整方案集；敏感性每个 OAT 场景各重枚举一次的就是它）
  ③ representative_options + n1_stress_test（N-1 压测）
  ④ run_sensitivity（结论稳定性核查）
  ⑤ analyze_task 总耗时（试点 /runs 的真实工作内容）
输出：benchmarks/分析耗时结构-raw-v18.md（自动汇总表，原始数字以本文件为准）。
本脚本只产生测量数据，不下结论、不提出任何产品改动；结论与提速评估在
docs/完整分析耗时结构-v18.md 由人工撰写。

运行（WSL，需 OR-Tools）：python3 benchmarks/analyze_time_structure.py
机器相关：结果与硬件强相关，仅作量级参照，非承诺。
"""

import platform
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.material import (analyze_task, build_options, n1_stress_test,
                          representative_options, run_sensitivity,
                          simulate_supply_mix)
from benchmarks.solver_benchmark import make_case

OUT = Path(__file__).resolve().parent / "分析耗时结构-raw-v18.md"
SCALES = (5, 8, 12, 16, 20, 30)


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000.0


def main():
    rows = []
    for n in SCALES:
        demand, eligible, factor_data = make_case(n)
        if len(eligible) < 2:
            print(f"[skip] n={n}: eligible 不足")
            continue
        print(f"[n={n}] eligible={len(eligible)} 计时开始…", flush=True)
        _mix, mix_ms = timed(simulate_supply_mix, demand, eligible, factor_data,
                             partial_capacity=True, step_t=0.5)
        print(f"  ① mix        {mix_ms/1000:9.1f} s", flush=True)
        options, build_ms = timed(build_options, demand, eligible, factor_data)
        print(f"  ② options    {build_ms/1000:9.1f} s", flush=True)
        reps, reps_ms = timed(representative_options, options)
        _n1, n1_ms = timed(n1_stress_test, demand, eligible, factor_data,
                           options=reps, model=None)
        print(f"  ③ n1         {n1_ms/1000:9.1f} s", flush=True)
        _sens, sens_ms = timed(run_sensitivity, demand, eligible, factor_data)
        print(f"  ④ sensitivity {sens_ms/1000:8.1f} s"
              f"（场景数 {len(_sens.get('scenarios') or [])}）", flush=True)
        _total, total_ms = timed(analyze_task, demand, eligible, factor_data)
        print(f"  ⑤ analyze    {total_ms/1000:9.1f} s", flush=True)
        rows.append({
            "n": n, "eligible": len(eligible),
            "mix_ms": round(mix_ms, 1), "build_ms": round(build_ms, 1),
            "reps_ms": round(reps_ms, 1), "n1_ms": round(n1_ms, 1),
            "sens_ms": round(sens_ms, 1),
            "sens_scenarios": len(_sens.get("scenarios") or []),
            "total_ms": round(total_ms, 1),
            "options_count": len(options),
        })

    lines = [
        "# 完整分析耗时结构 · 原始汇总（脚本自动产出）", "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 机器：`{platform.platform()}` · Python {platform.python_version()}"
        f" · CPU {__import__('os').cpu_count()} 逻辑核",
        "- 口径：make_case 同源夹具（5/8/12/16/20/30 供给、需求 20t）；分段计时"
        " time.perf_counter；N-1 用代表性方案（与试点 analyze_task 同构）；"
        "机器相关，仅作量级参照。", "",
        "| 供给 | options 数 | ① mix 前沿 s | ② build_options s | ③ N-1 s |"
        " ④ 敏感性 s（场景数） | ⑤ analyze_task 总 s |",
        "|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['n']} | {r['options_count']} | {r['mix_ms']/1000:.1f} | "
            f"{r['build_ms']/1000:.1f} | {r['n1_ms']/1000:.1f} | "
            f"{r['sens_ms']/1000:.1f}（{r['sens_scenarios']}） | "
            f"{r['total_ms']/1000:.1f} |")
    lines.append("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written:", OUT)


if __name__ == "__main__":
    main()
