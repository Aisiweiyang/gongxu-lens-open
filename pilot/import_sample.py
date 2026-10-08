"""导入示例数据（试点演示用，明确标注演示身份，不混入真实任务口径）。

用法：python pilot/import_sample.py --task demo01
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pilot.db import Database, now

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "pilot.sqlite3"

SAMPLE_DEMAND = {
    "demand_id": "DEM-001", "buyer_name": "某包装制品有限公司（演示）",
    "target_material_code": "PP", "application": "非食品接触周转箱",
    "required_mass_t": 15.0, "min_recycled_content_pct": 80.0,
    "mfi_min": 8.0, "mfi_max": 16.0, "max_moisture_pct": 0.5, "max_ash_pct": 5.0,
    "accepted_form": "颗粒", "accepted_color": "黑色、灰色",
    "required_from": "2026-09-15", "due_date": "2026-10-15",
    "destination": "某工业园区（演示）",
    "baseline_virgin_price_cny_per_t": 5500.0, "max_distance_km": 500.0,
}

SAMPLE_SUPPLIES = [
    {"supply_id": "SUP-01", "supplier_name": "本地再生资源有限公司（演示）",
     "material_code": "PP", "material_name_raw": "再生PP颗粒（黑色注塑级）",
     "polymer_type": "PP", "form": "颗粒", "color": "黑色", "grade": "注塑级",
     "mfi_g_10min": 12.0, "moisture_pct": 0.3, "ash_pct": 3.0,
     "recycled_content_pct": 95.0, "available_mass_t": 8.0,
     "available_from": "2026-09-10", "available_to": "2026-10-31",
     "origin": "某工业园区（演示）", "price_cny_per_t": 4200.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 30.0,
     "evidence_source": "检测报告（演示）", "evidence_date": "2026-08-15", "source": "演示"},
    {"supply_id": "SUP-02", "supplier_name": "邻区再生企业（演示）",
     "material_code": "PP", "material_name_raw": "再生PP颗粒（灰色）",
     "polymer_type": "PP", "form": "颗粒", "color": "灰色", "grade": "注塑级",
     "mfi_g_10min": 10.0, "moisture_pct": 0.4, "ash_pct": 4.0,
     "recycled_content_pct": 85.0, "available_mass_t": 7.0,
     "available_from": "2026-09-12", "available_to": "2026-10-20",
     "origin": "邻区（演示）", "price_cny_per_t": 4000.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 60.0,
     "evidence_source": "", "evidence_date": "", "source": "演示"},
    {"supply_id": "SUP-03", "supplier_name": "某改性塑料企业（演示）",
     "material_code": "PP", "material_name_raw": "高流动PP再生颗粒",
     "polymer_type": "PP", "form": "颗粒", "color": "黑色", "grade": "注塑级",
     "mfi_g_10min": 30.0, "moisture_pct": 0.3, "ash_pct": 3.0,
     "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-10", "available_to": "2026-10-25",
     "origin": "外市（演示）", "price_cny_per_t": 3600.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 120.0,
     "evidence_source": "检测报告（演示）", "evidence_date": "2026-08-10", "source": "演示"},
    {"supply_id": "SUP-04", "supplier_name": "外省再生企业（演示）",
     "material_code": "PP", "material_name_raw": "再生PP颗粒（黑色）",
     "polymer_type": "PP", "form": "颗粒", "color": "黑色", "grade": "注塑级",
     "mfi_g_10min": 11.0, "moisture_pct": 0.3, "ash_pct": 3.5,
     "recycled_content_pct": 90.0, "available_mass_t": 18.0,
     "available_from": "2026-09-08", "available_to": "2026-10-10",
     "origin": "外省（演示）", "price_cny_per_t": 3800.0,
     "price_includes_freight": False, "yield_pct": 100.0, "inspection_required": False,
     "preprocessing": "已造粒", "transport_mode": "公路货运",
     "factor_id": "DEFRA-ARTIC-HGV-DIESEL-2024", "distance_km": 480.0,
     "evidence_source": "检测报告（演示）", "evidence_date": "2026-08-05", "source": "演示"},
]


def main():
    parser = argparse.ArgumentParser(description="导入演示示例任务")
    parser.add_argument("--task", default="demo01")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    db = Database(Path(args.db))
    try:
        db.execute(
            "INSERT OR REPLACE INTO tasks(id,case_id,demand_json,status,created_by,created_at,updated_at)"
            " VALUES(?,?,?,?,1,?,?)",
            (args.task, "演示案例", json.dumps(SAMPLE_DEMAND, ensure_ascii=False),
             "draft", now(), now()))
        for supply in SAMPLE_SUPPLIES:
            db.execute(
                "INSERT OR REPLACE INTO supplies(task_id,supply_id,supply_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",
                (args.task, supply["supply_id"], json.dumps(supply, ensure_ascii=False),
                 now(), now()))
        db.commit()
        db.audit("system", "sample.import", f"task:{args.task}",
                 after={"supplies": len(SAMPLE_SUPPLIES), "demo": True})
        print(f"示例任务 {args.task} 已导入（{len(SAMPLE_SUPPLIES)} 个演示供给批次，明确为演示数据）。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
