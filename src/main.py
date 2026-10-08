"""主流程：再生材料供需匹配与净减排决策。"""

from . import material


def run_industry(industry_name="再生PP", data_mode="auto", demand_id=None):
    """行业分析入口。当前唯一主线行业为「再生PP」。"""
    if industry_name != "再生PP":
        raise ValueError(f"未配置行业：{industry_name}（当前主线行业：再生PP）")
    return material.run_material(data_mode, demand_id=demand_id)
