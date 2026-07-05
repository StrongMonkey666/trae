"""选股条件结构定义。

与 LLM 解析出的 JSON 一一对应，并提供校验/序列化能力。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..utils.exceptions import QuantPlatformError


VALID_OPERATORS = (">", "<", ">=", "<=", "==", "between")
VALID_LOGIC = ("AND", "OR")
VALID_SORT_ORDER = ("asc", "desc")


@dataclass
class Condition:
    field: str
    operator: str
    value: float
    value2: Optional[float] = None  # 仅 between
    compare_field: Optional[str] = None  # 跨字段比较：field OP row[compare_field]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"field": self.field, "operator": self.operator, "value": self.value}
        if self.operator == "between":
            d["value2"] = self.value2
        if self.compare_field is not None:
            d["compare_field"] = self.compare_field
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Condition":
        if "field" not in d or "operator" not in d or "value" not in d:
            raise QuantPlatformError("Condition 缺少必要字段")
        op = d["operator"]
        if op not in VALID_OPERATORS:
            raise QuantPlatformError(f"非法运算符: {op}")
        raw_value = d["value"]
        compare_field: Optional[str] = None
        # 跨字段简写：value 是字符串 → 视为 compare_field
        if isinstance(raw_value, str):
            compare_field = raw_value
            value = 0.0
        else:
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as e:
                raise QuantPlatformError(f"value 必须为数值或字段名: {e}") from e
        value2 = None
        if op == "between":
            if "value2" not in d:
                raise QuantPlatformError("between 运算符需要 value2")
            try:
                value2 = float(d["value2"])
            except (TypeError, ValueError) as e:
                raise QuantPlatformError(f"value2 必须为数值: {e}") from e
        if compare_field is None:
            compare_field = d.get("compare_field")
        if compare_field is not None and op == "between":
            raise QuantPlatformError("between 不支持跨字段比较")
        return cls(
            field=d["field"], operator=op, value=value,
            value2=value2, compare_field=compare_field,
        )

    def matches(self, row_value: Any) -> bool:
        if row_value is None or row_value != row_value:  # NaN
            return False
        v = float(row_value)
        if self.operator == ">":
            return v > self.value
        if self.operator == "<":
            return v < self.value
        if self.operator == ">=":
            return v >= self.value
        if self.operator == "<=":
            return v <= self.value
        if self.operator == "==":
            return v == self.value
        if self.operator == "between":
            lo, hi = sorted([self.value, self.value2 or self.value])
            return lo <= v <= hi
        return False


@dataclass
class SelectorSpec:
    """一个完整的选股方案。"""

    conditions: List[Condition] = field(default_factory=list)
    logic: str = "AND"
    sort_by: Optional[str] = None
    sort_order: str = "desc"
    limit: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "conditions": [c.to_dict() for c in self.conditions],
            "logic": self.logic,
        }
        if self.sort_by:
            d["sort_by"] = self.sort_by
            d["sort_order"] = self.sort_order
        if self.limit:
            d["limit"] = self.limit
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SelectorSpec":
        if not isinstance(d, dict):
            raise QuantPlatformError("SelectorSpec 必须是 dict")
        logic = d.get("logic", "AND")
        if logic not in VALID_LOGIC:
            raise QuantPlatformError(f"logic 非法: {logic}")
        conds = [Condition.from_dict(c) for c in d.get("conditions", [])]
        sort_by = d.get("sort_by")
        sort_order = d.get("sort_order", "desc")
        if sort_order not in VALID_SORT_ORDER:
            raise QuantPlatformError(f"sort_order 非法: {sort_order}")
        limit = d.get("limit")
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise QuantPlatformError("limit 必须是正整数")
        return cls(
            conditions=conds, logic=logic,
            sort_by=sort_by, sort_order=sort_order, limit=limit,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "SelectorSpec":
        return cls.from_dict(json.loads(s))

    def __bool__(self) -> bool:
        return bool(self.conditions) or bool(self.sort_by) or bool(self.limit)

    def is_empty(self) -> bool:
        return not self.conditions and not self.sort_by and not self.limit

    def with_relaxed_condition(self, idx: int, new_value: float) -> "SelectorSpec":
        """返回一个新 spec，把第 idx 个条件的 value 改为 new_value。"""
        if idx < 0 or idx >= len(self.conditions):
            raise QuantPlatformError(f"条件索引越界: {idx}")
        new_conds = []
        for i, c in enumerate(self.conditions):
            if i == idx:
                new_conds.append(Condition(
                    field=c.field, operator=c.operator,
                    value=new_value, value2=c.value2,
                ))
            else:
                new_conds.append(c)
        return SelectorSpec(
            conditions=new_conds, logic=self.logic,
            sort_by=self.sort_by, sort_order=self.sort_order,
            limit=self.limit,
        )

    def without_condition(self, idx: int) -> "SelectorSpec":
        """返回一个新 spec，去掉第 idx 个条件。"""
        if idx < 0 or idx >= len(self.conditions):
            raise QuantPlatformError(f"条件索引越界: {idx}")
        return SelectorSpec(
            conditions=[c for i, c in enumerate(self.conditions) if i != idx],
            logic=self.logic, sort_by=self.sort_by, sort_order=self.sort_order,
            limit=self.limit,
        )


@dataclass
class RelaxSuggestion:
    """单个放宽建议。

    - kind='drop'       建议去掉该条件
    - kind='loosen'     建议调整阈值为 new_value
    """
    kind: str                # 'drop' or 'loosen'
    condition_idx: int        # 在 spec.conditions 中的索引（-1 表示 drop 整条）
    field: str               # 字段名
    operator: str            # 运算符
    current_value: Optional[float] = None
    new_value: Optional[float] = None
    expected_count: int = 0  # 应用此建议后预计能选中的股票数
    description: str = ""    # 给 UI 展示的中文描述

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "condition_idx": self.condition_idx,
            "field": self.field,
            "operator": self.operator,
            "current_value": self.current_value,
            "new_value": self.new_value,
            "expected_count": self.expected_count,
            "description": self.description,
        }
