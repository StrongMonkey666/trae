"""资金分配模型。"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def equal_weight(
    target_codes: List[str], available_cash: float, **kwargs
) -> Dict[str, float]:
    """等权重：可用现金平均分。"""
    if not target_codes or available_cash <= 0:
        return {}
    amount = available_cash / len(target_codes)
    return {c: amount for c in target_codes}


def fixed_amount(
    target_codes: List[str], available_cash: float, **kwargs
) -> Dict[str, float]:
    """每只固定金额。"""
    amount = float(kwargs.get("fixed_amount", 10_000))
    return {c: min(amount, available_cash) for c in target_codes}


def score_weight(
    target_codes: List[str],
    available_cash: float,
    features: Optional[pd.DataFrame] = None,
    sort_by: Optional[str] = None,
    **kwargs,
) -> Dict[str, float]:
    """按评分权重：分数越高，分配越多。

    评分 = 1 / rank（rank 从 1 开始）。如果 features/sort_by 缺失则退化为等权。
    """
    if not target_codes or available_cash <= 0:
        return {}
    if features is None or not sort_by or sort_by not in features.columns:
        return equal_weight(target_codes, available_cash)

    df = features[features["code"].astype(str).str.zfill(6).isin(target_codes)].copy()
    df = df.sort_values(sort_by, ascending=False, na_position="last").reset_index(drop=True)
    if df.empty:
        return {}
    # 权重与排名成反比：rank 1 权重最大
    weights = 1.0 / np.arange(1, len(df) + 1, dtype=float)
    total = float(weights.sum())
    out: Dict[str, float] = {}
    for i, (_, row) in enumerate(df.iterrows()):
        code = str(row["code"]).zfill(6)
        out[code] = float(available_cash * float(weights[i]) / total)
    return out


def kelly(
    target_codes: List[str],
    available_cash: float,
    win_rate: float = 0.55,
    avg_win: float = 0.10,
    avg_loss: float = 0.05,
    fraction: float = 0.5,
    **kwargs,
) -> Dict[str, float]:
    """凯利公式：f* = (p*b - q) / b，b = avg_win/avg_loss。

    由于 p/b 在回测中难以准确估计，提供的是简化版本。
    """
    if not target_codes or available_cash <= 0:
        return {}
    b = avg_win / avg_loss if avg_loss > 0 else 1.0
    f = (win_rate * b - (1 - win_rate)) / b
    f = max(0.0, min(f, 1.0)) * fraction
    # 每只股票最多投入 f*cash，并按等权再分
    per = (available_cash * f) / len(target_codes)
    return {c: per for c in target_codes}


ALLOCATORS = {
    "equal_weight": equal_weight,
    "fixed_amount": fixed_amount,
    "score_weight": score_weight,
    "kelly": kelly,
}


def allocate(
    model: str,
    target_codes: List[str],
    available_cash: float,
    **kwargs,
) -> Dict[str, float]:
    fn = ALLOCATORS.get(model)
    if fn is None:
        raise ValueError(f"未知资金分配模型: {model}")
    return fn(target_codes, available_cash, **kwargs)
