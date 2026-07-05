"""LLM 输出解析器：将模型返回的文本解析为结构化条件。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from ..utils.exceptions import QuantPlatformError
from ..utils.logger import get_logger
from .prompt import SYSTEM_PROMPT, build_user_prompt

logger = get_logger(__name__)


_ALLOWED_FIELDS = {
    # 基本面
    "pe_ttm", "pb", "ps_ttm", "roe", "roa",
    "revenue", "net_profit", "revenue_growth", "net_profit_growth",
    "debt_ratio", "gross_margin", "market_cap",
    # 技术面
    "close", "ma_5", "ma_10", "ma_20", "ma_60",
    "macd", "macd_cross",
    "boll_upper", "boll_mid", "boll_lower",
    "turnover_rate", "volume_ratio",
    # 行情面
    "change_pct", "amount", "volume", "high_52w", "low_52w",
}

_ALLOWED_OPS = {">", "<", ">=", "<=", "==", "between"}
_ALLOWED_LOGIC = {"AND", "OR"}


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 包裹。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def parse_selector_json(text: str) -> Dict[str, Any]:
    """解析 LLM 返回文本为条件 dict。

    严格校验字段名/运算符，失败时抛 QuantPlatformError。
    """
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise QuantPlatformError(f"LLM 返回非合法 JSON: {cleaned[:200]}...") from e

    if not isinstance(data, dict):
        raise QuantPlatformError("LLM JSON 根节点必须是 object")

    conds = data.get("conditions", [])
    if not isinstance(conds, list):
        raise QuantPlatformError("conditions 必须是 list")

    for i, c in enumerate(conds):
        if not isinstance(c, dict):
            raise QuantPlatformError(f"conditions[{i}] 必须是 object")
        field = c.get("field")
        op = c.get("operator")
        if field not in _ALLOWED_FIELDS:
            raise QuantPlatformError(f"未知字段: {field}")
        if op not in _ALLOWED_OPS:
            raise QuantPlatformError(f"不支持的运算符: {op}")
        if op == "between":
            if "value" not in c or "value2" not in c:
                raise QuantPlatformError("between 运算符需要 value 与 value2")
        else:
            if "value" not in c:
                raise QuantPlatformError(f"conditions[{i}] 缺少 value")
        raw_value = c.get("value")
        # 跨字段简写：value 是字符串字段名 → 视为 compare_field
        if isinstance(raw_value, str):
            if raw_value not in _ALLOWED_FIELDS:
                raise QuantPlatformError(
                    f"conditions[{i}].value='{raw_value}' 不是已知字段"
                )
            if op == "between":
                raise QuantPlatformError("between 不支持跨字段比较")
        else:
            try:
                if op == "between":
                    float(c["value"]); float(c["value2"])
                else:
                    float(c["value"])
            except (TypeError, ValueError) as e:
                raise QuantPlatformError(f"conditions[{i}] value 不是数值: {e}")

    logic = data.get("logic", "AND")
    if logic not in _ALLOWED_LOGIC:
        raise QuantPlatformError(f"logic 必须是 AND 或 OR: {logic}")

    sort_by = data.get("sort_by")
    if sort_by is not None and sort_by not in _ALLOWED_FIELDS:
        raise QuantPlatformError(f"sort_by 字段未知: {sort_by}")
    sort_order = data.get("sort_order", "desc")
    if sort_order not in ("asc", "desc"):
        raise QuantPlatformError(f"sort_order 非法: {sort_order}")
    limit = data.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        raise QuantPlatformError("limit 必须是正整数")

    return data


def natural_language_to_spec(
    llm_client,
    natural_language: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """调用 LLM 把自然语言解析为条件 dict，带重试。"""
    from .base import LLMMessage  # 延迟导入避免循环

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        messages = [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(role="user", content=build_user_prompt(natural_language)),
        ]
        try:
            resp = llm_client.chat(messages)
            return parse_selector_json(resp.content)
        except QuantPlatformError as e:
            last_err = e
            logger.warning("LLM 解析第 %d 次失败: %s", attempt + 1, e)
    raise QuantPlatformError(
        f"LLM 解析失败（重试 {max_retries} 次仍不合法）: {last_err}"
    )
