"""供需透镜命令行入口：再生材料供需匹配与净减排决策。"""

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config
from src.dashboard import build_material_dashboard_html
from src.main import run_industry
from src.report import build_material_markdown, to_html
from src.site_builder import build_site_html


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="供需透镜：再生材料供需匹配与净减排决策")
    parser.add_argument("--industry", default="再生PP", help="行业名（当前主线：再生PP）")
    parser.add_argument("--list-industries", action="store_true", help="列出已配置行业")
    parser.add_argument(
        "--data-mode",
        choices=["auto", "local", "demo", "online"],
        default="auto",
        help="auto=本地 CSV 优先、无文件时演示；local=仅本地；demo=虚构演示；online=未实现",
    )
    parser.add_argument(
        "--demand-id",
        default=None,
        help="需求 CSV 含多条需求时必填：显式选择一条需求建任务（禁止静默取第一条）",
    )
    parser.add_argument(
        "--stamp",
        default=None,
        help="输出文件名日期戳（YYYYMMDD，缺省为当天）；供发行构建固定批次",
    )
    args = parser.parse_args(argv)

    if args.list_industries:
        for industry in config.load_industries():
            print(industry.get("name"))
        return 0
    try:
        stamp = _validate_stamp(args.stamp)
        result = run_industry(args.industry, args.data_mode, demand_id=args.demand_id)
        markdown = build_material_markdown(result)
        title = f"再生PP供需匹配与净减排决策"
        _write_markdown(markdown, title, stamp)
        _write_dashboard(result, "再生PP绿色决策仪表盘", stamp)
        _write_site(result)
    except (ValueError, OSError) as exc:
        logging.error("无法生成报告：%s", exc)
        return 2
    return 0


def _validate_stamp(stamp):
    """输出日期戳：缺省当天；显式给定必须为 YYYYMMDD（发行构建固定批次用，v15）。"""
    if stamp is None:
        return f"{date.today():%Y%m%d}"
    import re as _re
    if not _re.fullmatch(r"\d{8}", stamp):
        raise ValueError(f"--stamp 必须为 YYYYMMDD，实际为：{stamp}")
    return stamp


def _write_markdown(markdown, title, stamp=None):
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp or f"{date.today():%Y%m%d}"
    stem = f"{sanitize_filename(title)}_{stamp}"
    md_path = config.OUTPUT_DIR / f"{stem}.md"
    html_path = config.OUTPUT_DIR / f"{stem}.html"
    _atomic_write(md_path, markdown + "\n")
    _atomic_write(html_path, to_html(markdown, title))
    print(f"已生成：{md_path}")
    print(f"已生成：{html_path}")
    return md_path, html_path


def _write_site(result):
    config.SITE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DIR / "index.html"
    _atomic_write(path, build_site_html(result))
    print(f"已生成：{path}")
    return path


def _write_dashboard(result, title, stamp=None):
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp or f"{date.today():%Y%m%d}"
    stem = f"{sanitize_filename(title)}_{stamp}"
    path = config.OUTPUT_DIR / f"{stem}.html"
    _atomic_write(path, build_material_dashboard_html(result))
    print(f"已生成：{path}")
    return path


def sanitize_filename(value):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))
    safe = safe.strip(" .")[:100]
    return safe or "报告"


def _atomic_write(path, content):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
