"""选股引擎：把 SelectorSpec 应用到股票特征表上。

特征表是一个 DataFrame，行为股票，列至少包含 ['code', 'name'] + 各种字段
（pe_ttm, roe, ma_20, change_pct 等）。所有需要 join 的数据由调用方准备。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..utils.logger import get_logger
from .schema import SelectorSpec

logger = get_logger(__name__)


class SelectorEngine:
    """无状态选股引擎：run(spec, features) -> DataFrame。"""

    def run(
        self,
        spec: SelectorSpec,
        features: pd.DataFrame,
        exclude_codes: Optional[set[str]] = None,
    ) -> pd.DataFrame:
        if features is None or features.empty:
            return pd.DataFrame()
        if spec.is_empty():
            return features.copy()

        df = features.copy()
        # 1) 排除当前持仓
        if exclude_codes:
            df = df[~df["code"].astype(str).str.zfill(6).isin(exclude_codes)]

        if not spec.conditions:
            return df

        # 2) 条件筛选
        masks = []
        for c in spec.conditions:
            if c.field not in df.columns:
                logger.warning("条件字段 %s 不在特征表中，跳过", c.field)
                masks.append(pd.Series([False] * len(df), index=df.index))
                continue
            col = df[c.field]
            masks.append(col.apply(c.matches))
        if spec.logic == "AND":
            mask = masks[0]
            for m in masks[1:]:
                mask = mask & m
        else:  # OR
            mask = masks[0]
            for m in masks[1:]:
                mask = mask | m
        df = df[mask].copy()

        # 3) 排序
        if spec.sort_by and spec.sort_by in df.columns:
            df = df.sort_values(
                spec.sort_by, ascending=(spec.sort_order == "asc"), na_position="last"
            )

        # 4) 截断
        if spec.limit:
            df = df.head(spec.limit)

        return df.reset_index(drop=True)
