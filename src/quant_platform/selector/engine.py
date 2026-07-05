"""选股引擎：把 SelectorSpec 应用到股票特征表上。

特征表是一个 DataFrame，行为股票，列至少包含 ['code', 'name'] + 各种字段
（pe_ttm, roe, ma_20, change_pct 等）。所有需要 join 的数据由调用方准备。
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from ..utils.logger import get_logger
from .schema import Condition, RelaxSuggestion, SelectorSpec

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
        masks = self._build_masks(spec, df)
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

    # ============================================================
    # 放宽建议：当原 spec 选不出股票时，给出"如何放宽"的候选
    # ============================================================
    def suggest_relaxations(
        self,
        spec: SelectorSpec,
        features: pd.DataFrame,
        top_k: int = 3,
        loosen_steps: tuple = (0.10, 0.25, 0.50, 1.00),
    ) -> List[RelaxSuggestion]:
        """如果原 spec 选中 0 个，给出最多 top_k 条放宽建议。

        策略：
        1) 依次去掉单个条件，看能否命中
        2) 对每个数值条件，按 10%/25%/50%/100% 步长放宽，看能否命中

        返回按 expected_count 降序的建议。
        """
        if features is None or features.empty:
            return []
        if spec.is_empty():
            return []

        # 0) 原 spec 已有命中时，不给出建议
        original = self.run(spec, features)
        if not original.empty:
            return []

        suggestions: List[RelaxSuggestion] = []

        # 1) drop 策略：去掉第 idx 个条件
        for idx, c in enumerate(spec.conditions):
            relaxed = spec.without_condition(idx)
            df = self.run(relaxed, features)
            if df.empty:
                continue
            suggestions.append(RelaxSuggestion(
                kind="drop",
                condition_idx=idx,
                field=c.field,
                operator=c.operator,
                current_value=c.value if c.operator != "between" else None,
                new_value=None,
                expected_count=len(df),
                description=(
                    f"去掉条件「{c.field} {c.operator} {c.value}」，"
                    f"可选中 {len(df)} 只"
                ),
            ))

        # 2) loosen 策略：放宽数值阈值
        # 注意：loosen 看的是"单条件放宽后能匹配多少只"，而不是放宽后跑完整 spec。
        # 原因：完整 spec（AND）下，其他严格条件仍生效，可能整体命中 = 0，
        # 这对用户没有参考价值。我们希望给用户"这个条件放宽后潜在匹配量"的概念。
        for idx, c in enumerate(spec.conditions):
            if c.operator == "between":
                # between: 上下边界同时外扩
                lo, hi = sorted([c.value, c.value2 or c.value])
                base = max(abs(hi - lo), 1e-9)
                for step in loosen_steps:
                    delta = base * step
                    new_lo, new_hi = lo - delta, hi + delta
                    single = SelectorSpec(conditions=[
                        Condition(field=c.field, operator="between",
                                  value=new_lo, value2=new_hi),
                    ])
                    df = self.run(single, features)
                    if df.empty:
                        continue
                    suggestions.append(RelaxSuggestion(
                        kind="loosen",
                        condition_idx=idx,
                        field=c.field,
                        operator="between",
                        current_value=lo,
                        new_value=new_lo,
                        expected_count=len(df),
                        description=(
                            f"把「{c.field} between {lo:.2f}, {hi:.2f}」"
                            f"放宽到 ({new_lo:.2f}, {new_hi:.2f})，"
                            f"该条件可选中 {len(df)} 只"
                        ),
                    ))
                continue
            if c.operator in ("==",):
                # == 不适合放宽，跳过
                continue
            v = c.value
            base = max(abs(v), 1.0)
            for step in loosen_steps:
                if c.operator in (">", ">="):
                    new_v = v - base * step
                elif c.operator in ("<", "<="):
                    new_v = v + base * step
                else:
                    continue
                single = SelectorSpec(conditions=[
                    Condition(field=c.field, operator=c.operator, value=new_v),
                ])
                df = self.run(single, features)
                if df.empty:
                    continue
                suggestions.append(RelaxSuggestion(
                    kind="loosen",
                    condition_idx=idx,
                    field=c.field,
                    operator=c.operator,
                    current_value=v,
                    new_value=new_v,
                    expected_count=len(df),
                    description=(
                        f"把「{c.field} {c.operator} {v}」"
                        f"放宽到 {c.operator} {new_v:.2f}，"
                        f"该条件可选中 {len(df)} 只"
                    ),
                ))

        # 3) 排序：命中数降序；同命中数时优先 drop（更激进）
        suggestions.sort(
            key=lambda s: (s.expected_count, 0 if s.kind == "drop" else 1),
            reverse=True,
        )
        # 4) 去重（同 idx+kind 保留 expected_count 最大的那条）
        seen = {}
        for s in suggestions:
            key = (s.kind, s.condition_idx)
            if key not in seen or s.expected_count > seen[key].expected_count:
                seen[key] = s
        deduped = sorted(
            seen.values(),
            key=lambda s: (s.expected_count, 0 if s.kind == "drop" else 1),
            reverse=True,
        )
        return deduped[:top_k]

    # ============================================================
    # 内部 helper
    # ============================================================
    def _build_masks(self, spec: SelectorSpec, df: pd.DataFrame) -> List[pd.Series]:
        masks = []
        for c in spec.conditions:
            if c.field not in df.columns:
                logger.warning("条件字段 %s 不在特征表中，跳过", c.field)
                masks.append(pd.Series([False] * len(df), index=df.index))
                continue
            if c.compare_field is not None:
                if c.compare_field not in df.columns:
                    logger.warning(
                        "compare_field %s 不在特征表中，跳过条件 %s",
                        c.compare_field, c.field,
                    )
                    masks.append(pd.Series([False] * len(df), index=df.index))
                    continue
                # 跨字段比较：row[c.field] OP row[c.compare_field]
                lhs = df[c.field]
                rhs = df[c.compare_field]
                if c.operator == ">":
                    mask = lhs > rhs
                elif c.operator == "<":
                    mask = lhs < rhs
                elif c.operator == ">=":
                    mask = lhs >= rhs
                elif c.operator == "<=":
                    mask = lhs <= rhs
                elif c.operator == "==":
                    mask = lhs == rhs
                else:
                    logger.warning("跨字段比较不支持运算符 %s，跳过", c.operator)
                    mask = pd.Series([False] * len(df), index=df.index)
                # NaN 处理：任何一侧为 NaN 则视为不命中
                mask = mask & lhs.notna() & rhs.notna()
                masks.append(mask)
                continue
            col = df[c.field]
            masks.append(col.apply(c.matches))
        return masks
