"""配置加载：路径常量与行业定义。"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
SITE_DIR = BASE_DIR / "site"

MATERIAL_SUPPLY_CSV = DATA_DIR / "input" / "material_supply.csv"
MATERIAL_DEMAND_CSV = DATA_DIR / "input" / "material_demand.csv"
REVIEW_FEEDBACK_CSV = DATA_DIR / "input" / "review_feedback.csv"


def load_industries():
    """加载行业定义，返回 [industry_def, ...]。"""
    import yaml

    path = CONFIG_DIR / "industries.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("industries", [])
