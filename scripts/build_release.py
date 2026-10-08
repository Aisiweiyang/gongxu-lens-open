"""发行构建：白名单构建 dist/competition-demo 与 dist/pilot-app。

原则：
- 排除旧医疗报告、archive/legacy、pycache、缓存、真实客户附件、凭据与无关文件；
- 保留原工作副本历史资料（构建只拷贝，不删除原目录任何文件）；
- 发行包从临时新目录独立安装/解压运行，不依赖原项目绝对路径；
- 输出文件清单、SHA-256 校验和、版本与运行手册。
"""

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VERSION = "v27.1"
DIST = BASE / "dist"
DEMO = DIST / "competition-demo"
PILOT = DIST / "pilot-app"

# 排除项（相对 BASE）
EXCLUDE_DIRS = {
    "dist", ".git", "__pycache__", ".e2e-venv", "output/archive",
    "pilot/backups", "pilot/data", "cron", ".venv",
    "docs/e2e-shots/probe390.py",
}
EXCLUDE_GLOBS = ["**/__pycache__", "**/*.pyc", "**/.pytest_cache",
                 "pilot/backups/*", "pilot/data/*", "dist/**"]

# —— 包内容白名单 ——
DEMO_FILES = [
    "site/index.html",                      # 评委演示（单文件离线）
    "README.md",
    "启动演示网站.bat",
    "templates/material_demand.csv",
    "templates/material_supply.csv",
    "templates/review_feedback.csv",
    "run.py", "serve.py", "requirements.txt",
    "src/__init__.py", "src/config.py", "src/main.py",
    "src/material.py", "src/ledger.py", "src/imports.py", "src/validation.py",
    "src/material_match.py", "src/visuals.py",
    "src/evidence_value.py", "src/evidence_quality.py", "src/feedback.py",
    "src/greedy_baseline.py", "src/passport.py", "src/report.py",
    "src/dashboard.py", "src/site_builder.py", "src/scenarios.py",
    "config/industries.yaml", "config/material_emission_factors.yaml",
    "config/evidence_actions.yaml",
    "schemas/material_evidence_passport.schema.v2.json",
    "schemas/material_evidence_passport.schema.json",  # v1 保留供迁移读取
    "benchmarks/industry_players.csv",
    "benchmarks/material_matching_cases.csv",
    "benchmarks/material_matching_cases_v2.csv",
    "docs/参考来源.md", "docs/来源与许可.md", "docs/开源借鉴落实-v10.md",
    "docs/运行手册-演示包-v10.md",
    # README 徽章与首屏截图（README 相对路径引用，包内随 README 携带）
    ".github/badges/tests.svg", ".github/badges/checks.svg",
    ".github/badges/e2e.svg", ".github/badges/scope.svg",
    "docs/e2e-shots/site-1440.png",
]
# 演示包附带当前生成的报告/仪表盘（供离线查看）。
# v15：按名取 output/ 下各模式「最新批次」（文件名内嵌日期后缀排序），消除固定日期失配。
def _latest_outputs():
    out_dir = BASE / "output"
    picks = {}
    for path in out_dir.glob("*.html"):
        name = path.name
        for prefix in ("再生PP供需匹配与净减排决策_", "再生PP绿色决策仪表盘_"):
            if name.startswith(prefix):
                picks.setdefault(prefix, []).append(name)
    extra = []
    for prefix, names in sorted(picks.items()):
        extra.append(f"output/{sorted(names)[-1]}")
        stem = sorted(names)[-1][:-len(".html")]
        md = out_dir / f"{stem}.md"
        if stem.startswith("再生PP供需匹配与净减排决策_") and md.exists():
            extra.append(f"output/{stem}.md")
    return sorted(set(extra))


# v12 PPT 截图（演示包附带，评委可直接查看）
PPT_SHOT_FILES = [f"docs/ppt_assets/{i:02d}_{name}.png" for i, name in enumerate([
    "overview", "demand", "supply_pool", "evidence_before", "evidence_after",
    "compare", "carbon_ledger", "confirm_reserve", "feedback", "passport"], 1)]

# v17 PPT 增量批次（撮合页全页/压测实测摘要/仪表盘图表总览；ppt_shots.py 缺省批次产出）
V17_PPT_SHOTS = ["docs/ppt_assets/11_matching.png",
                 "docs/ppt_assets/12_load_benchmark.png",
                 "docs/ppt_assets/13_dashboard_charts.png"]

PILOT_FILES = [
    "README.md", "requirements.txt", "requirements-dev.txt", "run.py", "serve.py",
    "src/__init__.py", "src/config.py", "src/main.py",
    "src/material.py", "src/ledger.py", "src/imports.py", "src/validation.py",
    "src/material_match.py", "src/matching.py", "src/visuals.py",
    "src/evidence_value.py", "src/evidence_quality.py", "src/feedback.py",
    "src/greedy_baseline.py", "src/passport.py", "src/report.py",
    "src/dashboard.py", "src/site_builder.py", "src/scenarios.py",
    "pilot/__init__.py", "pilot/db.py", "pilot/auth.py", "pilot/api.py",
    "pilot/server.py", "pilot/start_pilot.py", "pilot/init_admin.py",
    "pilot/import_sample.py", "pilot/backup_restore.py",
    "pilot/static/workbench.html", "pilot/static/app.js", "pilot/static/style.css",
    "config/industries.yaml", "config/material_emission_factors.yaml",
    "config/evidence_actions.yaml",
    "schemas/material_evidence_passport.schema.v2.json",
    "schemas/material_evidence_passport.schema.json",
    "templates/material_demand.csv", "templates/material_supply.csv",
    "templates/review_feedback.csv",
    "benchmarks/industry_players.csv", "benchmarks/material_matching_cases.csv",
    "benchmarks/material_matching_cases_v2.csv", "benchmarks/perf_benchmark.py",
    "tests/__init__.py", "tests/test_material.py", "tests/test_site.py",
    "tests/test_dashboard.py", "tests/test_innovations.py", "tests/test_feedback.py",
    "tests/test_oss_patterns.py", "tests/test_sensitivity.py", "tests/test_run.py",
    "tests/test_docs_wording.py", "tests/test_v10_regressions.py", "tests/test_pilot.py",
    "docs/商用试点手册-v10.md", "docs/archive/实施跟踪-v10.md",
    "docs/archive/自审第一轮-v10.md", "docs/自审第二轮-v10.md",
    "docs/独立核验脚本-v10.py", "docs/性能基准-v10.md",
    "docs/试点交付模板-v10.md", "docs/开源借鉴落实-v10.md",
    "docs/来源与许可.md", "docs/参考来源.md",
    "docs/archive/材料事实修订清单-v10.md", "docs/六分钟预答辩脚本-v10.md",
    "docs/发行验证-v10.md", "docs/archive/自审第一轮-v11.md", "docs/archive/自审第二轮-v11.md",
    "docs/archive/实施跟踪-v11.md",
    # v12：自主验收、优化循环与 PPT/视频素材
    "tests/test_v12_gates.py", "tests/e2e_v12.py", "scripts/ppt_shots.py",
    "docs/archive/最终自主验收-v12.md", "docs/archive/自审第一轮-v12.md", "docs/自审第二轮-v12.md",
    "docs/PPT与视频素材说明-v12.md", "docs/ppt_assets/截图索引.md",
    # v13：求解器互验、数据模型 v5、因子库方案、门控回归与自审
    "tests/test_v13_solver.py", "tests/test_v13_datamodel.py",
    "tests/test_v13_recall_gating.py",
    "benchmarks/solver_benchmark.py", "benchmarks/求解基准-v13.md",
    "docs/数据模型-v13.md", "docs/因子库接入方案-v13.md",
    # v14：完整前沿、区间传播、撮合引擎 v0 与自审
    "tests/test_v14_interval.py", "tests/test_v14_frontier.py",
    "tests/test_v14_matching.py",
    "benchmarks/solver_benchmark_v14.py", "benchmarks/求解基准-v14.md",
    # v15：结构与呈现轮（归档/文档地图/SVG 可视化）
    "tests/test_v15_visual.py",
    "docs/INDEX.md",
    # v16：试点就绪与平台雏形轮（撮合上工作台、压测基准、ADR、src/matching.py）
    "tests/test_v16_matching_api.py", "tests/e2e_v16.py",
    "benchmarks/load_benchmark.py",
    "benchmarks/压力基准-raw-wsl-shared.md", "benchmarks/压力基准-raw-wsl-shared.json",
    "benchmarks/压力基准-raw-wsl-per-user.md", "benchmarks/压力基准-raw-wsl-per-user.json",
    "benchmarks/压力基准-raw-windows-shared.md", "benchmarks/压力基准-raw-windows-shared.json",
    "benchmarks/压力基准-raw-windows-per-user.md", "benchmarks/压力基准-raw-windows-per-user.json",
    "benchmarks/压力基准-raw-v17large-wsl-shared.md", "benchmarks/压力基准-raw-v17large-wsl-shared.json",
    "benchmarks/压力基准-raw-v22parallel-wsl-shared.md", "benchmarks/压力基准-raw-v22parallel-wsl-shared.json",
    "docs/压力基准-v16.md", "docs/自审第二轮-v16.md",
    "docs/adr/README.md", "docs/adr/adr-0001-sqlite-single-file.md",
    "docs/adr/adr-0002-solver-dual-path.md", "docs/adr/adr-0003-interval-not-in-passport.md",
    "docs/adr/adr-0004-no-ruff-linter.md", "docs/adr/adr-0005-matching-preview-no-persistence.md",
    "docs/adr/adr-0006-async-jobs-in-memory.md",
    # v17：复赛就绪与试点交付轮（答辩素材升级/撮合建议单导出/大案例压力复核）
    "src/matching_sheet.py", "tests/test_v17_matching_sheet.py",
    # v18：界面美化与容错性（e2e_v18 容错套件）
    "tests/e2e_v18.py", "docs/自审第二轮-v18.md",
    "docs/e2e-shots/v18-fault-tolerance.png",
    "tests/test_v18_async_runs.py",
    "benchmarks/analyze_time_structure.py", "benchmarks/分析耗时结构-raw-v18.md",
    "docs/完整分析耗时结构-v18.md",
    # v19：场景级并行化（等价回归测试）
    "tests/test_v19_parallel.py",
    # v20：商业准备轮（安全合规清单/Linux 部署/自检脚本/一页纸模板）
    "docs/安全与合规应答清单-v20.md", "docs/试点部署指南-Linux-v20.md",
    "scripts/pilot_selfcheck.py", "tests/test_v20_selfcheck.py",
    "docs/试点一页纸方案模板-v20.md",
    # v21：双语管理（英文 README 与语言政策）
    "README.en.md",
    # v22：口径刷新与收尾打磨（README 刷新/SVG 标签槽/大案例实测/CHANGELOG 复位）
    # v23：开源准备（贡献指南与安全政策）
    "CONTRIBUTING.md", "SECURITY.md",
    # v24：去 AI 痕迹与呈现精修
    # v25：呈现细节与工程卫生
    # v26：README 呈现补强（徽章与首屏截图随 README 入包）
    # 试点工作台一键启动（随包分发，与 启动演示网站.bat 同模式）
    "启动试点工作台.bat",
    # v26：深色模式回归（四端深色块与配色声明）
    "tests/test_v26_dark_mode.py",
    # v26：安全加固回归（安全响应头/Cookie/口令策略/防穿越）
    "tests/test_v26_security.py",
    # README 徽章（相对路径引用，包内必须随 README 一起携带）
    ".github/badges/tests.svg", ".github/badges/checks.svg",
    ".github/badges/e2e.svg", ".github/badges/scope.svg",
    "docs/e2e-shots/site-1440.png",
]
# 验收证据目录
PILOT_EVIDENCE = [
    "docs/e2e-shots/site-1440.png", "docs/e2e-shots/site-768.png",
    "docs/e2e-shots/site-390.png", "docs/e2e-shots/pilot-768.png",
    "docs/e2e-shots/pilot-390.png", "docs/e2e-shots/downloaded_passport.json",
    "docs/e2e-shots/pilot_passport.json", "docs/e2e-shots/matching-v16.png",
    "docs/e2e-shots/matching-sheet-e2e.html",
    "docs/性能基准-v10.md",
]
# v12 PPT 截图（两个发行包都附带，评委可直接查看）
PILOT_EVIDENCE += [f"docs/ppt_assets/{i:02d}_{name}.png" for i, name in enumerate([
    "overview", "demand", "supply_pool", "evidence_before", "evidence_after",
    "compare", "carbon_ledger", "confirm_reserve", "feedback", "passport"], 1)]
# v17 PPT 增量批次（同附两包）
PILOT_EVIDENCE += V17_PPT_SHOTS

# 原创代码的授权与来源声明随两个发行包分发。
OPEN_SOURCE_FILES = ["LICENSE", "THIRD_PARTY_NOTICES.md", "README.en.md",
                     "requirements-test.txt", "docs/开源发布指南.md",
                     "docs/开源准备验收-20261008.md"]
DEMO_FILES += OPEN_SOURCE_FILES
PILOT_FILES += OPEN_SOURCE_FILES + [
    "tests/test_static_server.py", "tests/test_public_release.py",
    "scripts/check_project.py", "scripts/prepare_public_release.py",
    ".gitignore", ".gitattributes", ".github/workflows/ci.yml",
    "安装环境.bat", "启动演示网站.bat", "打开最新报告.bat", "CHANGELOG.md",
]



def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package(files, extra_output, target, name, missing_ok=False):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    manifest = {}
    for rel in files + extra_output:
        src = BASE / rel
        if not src.exists():
            if missing_ok:
                continue
            print(f"警告：白名单文件缺失，跳过：{rel}")
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest[rel] = sha256(dst)
    (target / "FILES.txt").write_text(
        "\n".join(f"{h}  {rel}" for rel, h in sorted(manifest.items())) + "\n",
        encoding="utf-8")
    (target / "VERSION.txt").write_text(
        f"供需透镜 {name} {VERSION}\n构建时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"算法版本：material-v8\n文件数：{len(manifest)}\n", encoding="utf-8")
    print(f"{name} 构建完成：{target}（{len(manifest)} 个文件）")
    return manifest


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    demo_extra = _latest_outputs() + PPT_SHOT_FILES + V17_PPT_SHOTS
    print("演示包附带最新报告/仪表盘：", demo_extra)
    demo_manifest = build_package(DEMO_FILES, demo_extra, DEMO,
                                  "静态演示包（评委演示）", missing_ok=True)
    pilot_manifest = build_package(PILOT_FILES, PILOT_EVIDENCE, PILOT,
                                   "商用试点应用包", missing_ok=False)
    # 顶层清单
    summary = {"demo": {"files": len(demo_manifest)},
               "pilot": {"files": len(pilot_manifest)}}
    (DIST / "RELEASE-MANIFEST.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"顶层清单已写入 {DIST / 'RELEASE-MANIFEST.json'}")


if __name__ == "__main__":
    main()
