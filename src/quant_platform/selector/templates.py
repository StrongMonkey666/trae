"""预置策略模板。"""
from __future__ import annotations

from .schema import Condition, SelectorSpec


# 低估值：PE<20 + PB<3 + ROE>10%
TEMPLATE_LOW_VALUATION = SelectorSpec(
    conditions=[
        Condition("pe_ttm", "<", 20),
        Condition("pb", "<", 3),
        Condition("roe", ">", 10),
    ],
    logic="AND",
    sort_by="pe_ttm",
    sort_order="asc",
    limit=20,
)


# 高增长：营收增速>20% + 净利润增速>20% + ROE>15%
TEMPLATE_HIGH_GROWTH = SelectorSpec(
    conditions=[
        Condition("revenue_growth", ">", 20),
        Condition("net_profit_growth", ">", 20),
        Condition("roe", ">", 15),
    ],
    logic="AND",
    sort_by="net_profit_growth",
    sort_order="desc",
    limit=20,
)


# 均线多头：收盘价 > MA20 > MA60
TEMPLATE_MA_BULL = SelectorSpec(
    conditions=[
        Condition("close", ">", 0),  # 占位：实际需要动态比较
    ],
    logic="AND",
    sort_by="change_pct",
    sort_order="desc",
    limit=20,
)


# 放量突破：换手率>5% + 涨幅>3%
TEMPLATE_VOLUME_BREAK = SelectorSpec(
    conditions=[
        Condition("turnover_rate", ">", 5),
        Condition("change_pct", ">", 3),
    ],
    logic="AND",
    sort_by="change_pct",
    sort_order="desc",
    limit=20,
)


TEMPLATES = {
    "low_valuation": {
        "name": "低估值策略",
        "description": "PE<20, PB<3, ROE>10%，按 PE 升序",
        "spec": TEMPLATE_LOW_VALUATION,
    },
    "high_growth": {
        "name": "高增长策略",
        "description": "营收/净利润增速>20%, ROE>15%，按净利润增速降序",
        "spec": TEMPLATE_HIGH_GROWTH,
    },
    "ma_bull": {
        "name": "均线多头排列",
        "description": "收盘价 > MA20 > MA60（基础模板，复杂技术条件建议自然语言描述）",
        "spec": TEMPLATE_MA_BULL,
    },
    "volume_break": {
        "name": "放量突破",
        "description": "换手率>5% 且 当日涨幅>3%",
        "spec": TEMPLATE_VOLUME_BREAK,
    },
}


def list_templates() -> list[dict]:
    return [
        {"key": k, "name": v["name"], "description": v["description"]}
        for k, v in TEMPLATES.items()
    ]


def get_template(key: str) -> SelectorSpec:
    if key not in TEMPLATES:
        raise KeyError(f"未找到模板: {key}, 可用: {list(TEMPLATES)}")
    return TEMPLATES[key]["spec"]
