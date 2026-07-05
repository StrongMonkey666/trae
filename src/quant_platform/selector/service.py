"""选股服务（高层接口）：自然语言 -> SelectorSpec -> 选股 -> 保存历史。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..data_service.unified_api import UnifiedDataService
from ..llm.base import LLMClient
from ..llm.parser import natural_language_to_spec
from ..utils.logger import get_logger
from .engine import SelectorEngine
from .history import SelectorHistory
from .schema import SelectorSpec
from .templates import TEMPLATES, get_template, list_templates

logger = get_logger(__name__)


class SelectorService:
    """整合 LLM + 选股引擎 + 历史记录的选股服务。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        data_service: Optional[UnifiedDataService] = None,
        history: Optional[SelectorHistory] = None,
    ) -> None:
        self.llm = llm_client
        self.data = data_service or UnifiedDataService()
        self.engine = SelectorEngine()
        self.history = history or SelectorHistory(
            self.data.store.sqlite.path
        )

    # ============================================================
    # 自然语言入口
    # ============================================================
    def from_natural_language(
        self,
        text: str,
        as_of: Optional[Any] = None,
        exclude_codes: Optional[set[str]] = None,
        save: bool = True,
    ) -> Dict[str, Any]:
        """自然语言 -> spec -> 选股 -> 返回 {'spec', 'result', 'record_id'}。"""
        if self.llm is None:
            raise RuntimeError("未配置 LLM 客户端，无法使用自然语言入口")
        spec_dict = natural_language_to_spec(self.llm, text)
        spec = SelectorSpec.from_dict(spec_dict)
        return self.run(
            spec, as_of=as_of, exclude_codes=exclude_codes,
            natural_lang=text, save=save,
        )

    # ============================================================
    # 结构化入口（回测时使用，不走 LLM）
    # ============================================================
    def run(
        self,
        spec: SelectorSpec,
        as_of: Optional[Any] = None,
        exclude_codes: Optional[set[str]] = None,
        natural_lang: str = "",
        name: str = "",
        save: bool = True,
    ) -> Dict[str, Any]:
        features = self._build_features(as_of=as_of)
        result = self.engine.run(spec, features, exclude_codes=exclude_codes)
        record_id = -1
        if save and not result.empty:
            record_id = self.history.save(
                name=name or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                spec=spec,
                result_codes=result["code"].astype(str).str.zfill(6).tolist(),
                natural_lang=natural_lang,
            )
        return {
            "spec": spec,
            "result": result,
            "record_id": record_id,
            "as_of": as_of,
        }

    # ============================================================
    # 模板入口
    # ============================================================
    def run_template(
        self, template_key: str, as_of: Optional[Any] = None,
        exclude_codes: Optional[set[str]] = None, save: bool = True,
    ) -> Dict[str, Any]:
        spec = get_template(template_key)
        return self.run(
            spec, as_of=as_of, exclude_codes=exclude_codes,
            natural_lang=TEMPLATES[template_key]["description"],
            name=f"template:{template_key}", save=save,
        )

    # ============================================================
    # 特征构建：把数据服务的数据 join 成一张特征表
    # ============================================================
    def _build_features(self, as_of: Optional[Any] = None) -> pd.DataFrame:
        """从 UnifiedDataService 拉取股票列表 + 实时行情，拼成特征表。

        as_of：当前未使用（实时模式）；后续扩展可传入日期做历史快照。
        """
        stocks = self.data.get_stock_list()
        if stocks.empty:
            return pd.DataFrame()
        codes = stocks["code"].astype(str).str.zfill(6).tolist()
        try:
            quotes = self.data.get_realtime_data(codes)
        except Exception as e:
            logger.warning("拉取实时行情失败，仅返回股票列表: %s", e)
            quotes = pd.DataFrame()
        if quotes.empty:
            return stocks[["code", "name"]]
        quotes["code"] = quotes["code"].astype(str).str.zfill(6)
        merged = stocks.merge(quotes, on="code", how="left", suffixes=("_s", ""))
        # 整理列
        keep = [
            "code", "name_s" if "name_s" in merged.columns else "name",
            "last", "open", "high", "low", "pre_close", "volume", "amount",
            "turnover_rate", "pe_ttm", "pb", "market_cap", "change_pct",
        ]
        keep = [c for c in keep if c in merged.columns]
        if "name" in merged.columns and "name_s" in merged.columns:
            merged["name"] = merged["name_s"].fillna(merged["name"])
        elif "name_s" in merged.columns:
            merged["name"] = merged["name_s"]
        cols = [c for c in keep if c != "name_s"]
        return merged[cols].rename(columns={"last": "close"})

    @staticmethod
    def list_builtin_templates() -> List[Dict[str, str]]:
        return list_templates()
