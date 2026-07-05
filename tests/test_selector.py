"""选股系统测试。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_platform.selector.schema import Condition, SelectorSpec
from quant_platform.selector.engine import SelectorEngine
from quant_platform.selector.templates import list_templates, get_template
from quant_platform.selector.history import SelectorHistory
from quant_platform.llm.parser import parse_selector_json, _strip_code_fence
from quant_platform.llm.base import LLMClient, LLMMessage, LLMResponse


# ============================================================
# schema
# ============================================================
def test_condition_matches_operators():
    assert Condition("pe_ttm", "<", 20).matches(15) is True
    assert Condition("pe_ttm", "<", 20).matches(20) is False
    assert Condition("pe_ttm", "between", 10, value2=20).matches(15) is True
    assert Condition("pe_ttm", "between", 10, value2=20).matches(9) is False
    # None / NaN
    assert Condition("pe_ttm", "<", 20).matches(None) is False
    assert Condition("pe_ttm", "<", 20).matches(float("nan")) is False


def test_selector_spec_roundtrip():
    spec = SelectorSpec(
        conditions=[
            Condition("pe_ttm", "<", 20),
            Condition("roe", ">", 10),
        ],
        logic="AND",
        sort_by="pe_ttm", sort_order="asc", limit=10,
    )
    s = spec.to_json()
    spec2 = SelectorSpec.from_json(s)
    assert spec2.logic == "AND"
    assert len(spec2.conditions) == 2
    assert spec2.conditions[0].field == "pe_ttm"
    assert spec2.conditions[1].operator == ">"


def test_selector_spec_validates_operator():
    with pytest.raises(Exception):
        Condition.from_dict({"field": "x", "operator": "??", "value": 1})


# ============================================================
# engine
# ============================================================
def test_engine_filters_and_sorts():
    df = pd.DataFrame({
        "code": ["600000", "600001", "600002"],
        "name": ["A", "B", "C"],
        "pe_ttm": [10, 30, 15],
        "roe": [12, 5, 20],
    })
    spec = SelectorSpec(
        conditions=[Condition("pe_ttm", "<", 20), Condition("roe", ">", 10)],
        logic="AND", sort_by="pe_ttm", sort_order="asc", limit=2,
    )
    out = SelectorEngine().run(spec, df)
    assert list(out["code"]) == ["600000", "600002"]


def test_engine_excludes_codes():
    df = pd.DataFrame({"code": ["600000", "600001"], "pe_ttm": [10, 12]})
    spec = SelectorSpec(conditions=[Condition("pe_ttm", "<", 20)])
    out = SelectorEngine().run(spec, df, exclude_codes={"600000"})
    assert list(out["code"]) == ["600001"]


def test_engine_or_logic():
    df = pd.DataFrame({
        "code": ["a", "b", "c"],
        "pe_ttm": [5, 50, 50],
        "roe": [5, 5, 25],
    })
    spec = SelectorSpec(
        conditions=[Condition("pe_ttm", "<", 10), Condition("roe", ">", 20)],
        logic="OR",
    )
    out = SelectorEngine().run(spec, df)
    assert set(out["code"]) == {"a", "c"}


def test_engine_empty_spec_returns_all():
    df = pd.DataFrame({"code": ["a", "b"]})
    out = SelectorEngine().run(SelectorSpec(), df)
    assert len(out) == 2


# ============================================================
# suggest_relaxations：选股为空时的"放宽建议"
# ============================================================
class TestSuggestRelaxations:
    def _df(self):
        return pd.DataFrame({
            "code": [f"{600000 + i:06d}" for i in range(6)],
            "name": [f"S{i}" for i in range(6)],
            "pe_ttm": [10.0, 18.0, 25.0, 30.0, 50.0, 80.0],
            "roe":    [5.0, 12.0, 8.0, 20.0, 3.0, 15.0],
            "pb":     [1.0, 2.0, 5.0, 3.0, 4.0, 6.0],
        })

    def test_no_suggestion_when_result_nonempty(self):
        spec = SelectorSpec(conditions=[Condition("pe_ttm", "<", 50)])
        out = SelectorEngine().suggest_relaxations(spec, self._df())
        assert out == []

    def test_drop_suggestion(self):
        spec = SelectorSpec(conditions=[
            Condition("pe_ttm", "<", 15),
            Condition("roe", ">", 20),
        ])
        out = SelectorEngine().suggest_relaxations(spec, self._df(), top_k=3)
        assert len(out) >= 1
        assert any(s.kind == "drop" for s in out)
        for s in out:
            assert s.expected_count > 0
            assert s.condition_idx in (0, 1)

    def test_loosen_suggestion(self):
        spec = SelectorSpec(conditions=[
            Condition("pe_ttm", "<", 10),
            Condition("roe", ">", 10),
        ])
        out = SelectorEngine().suggest_relaxations(spec, self._df(), top_k=5)
        assert any(s.kind == "loosen" for s in out)
        if len(out) >= 2:
            assert out[0].expected_count >= out[1].expected_count

    def test_top_k_limit(self):
        spec = SelectorSpec(conditions=[
            Condition("pe_ttm", "<", 1),
            Condition("roe", ">", 100),
            Condition("pb", "<", 0.1),
        ])
        out = SelectorEngine().suggest_relaxations(spec, self._df(), top_k=2)
        assert len(out) <= 2

    def test_empty_features(self):
        spec = SelectorSpec(conditions=[Condition("pe_ttm", "<", 0)])
        out = SelectorEngine().suggest_relaxations(spec, pd.DataFrame())
        assert out == []

    def test_relaxed_spec_runs_correctly(self):
        spec = SelectorSpec(conditions=[
            Condition("pe_ttm", "<", 10),
            Condition("roe", ">", 10),
        ])
        new_spec = spec.with_relaxed_condition(0, 50)
        out = SelectorEngine().run(new_spec, self._df())
        # pe<50 AND roe>10: 600001(pe=18,roe=12), 600003(pe=30,roe=20), 600005(pe=80,roe=15✗)
        # pe<50 命中的：600000(10), 600001(18), 600002(25), 600003(30), 600004(50✗)
        # 其中 roe>10：600001(12), 600003(20) = 2 只
        assert len(out) == 2

    def test_with_relaxed_condition_preserves_logic_sort(self):
        spec = SelectorSpec(
            conditions=[Condition("pe_ttm", "<", 10), Condition("roe", ">", 5)],
            logic="AND", sort_by="pe_ttm", sort_order="asc", limit=20,
        )
        new_spec = spec.with_relaxed_condition(0, 100)
        assert new_spec.logic == "AND"
        assert new_spec.sort_by == "pe_ttm"
        assert new_spec.sort_order == "asc"
        assert new_spec.limit == 20
        assert new_spec.conditions[0].value == 100
        assert new_spec.conditions[1].value == 5

    def test_without_condition_removes_correct_one(self):
        spec = SelectorSpec(
            conditions=[Condition("pe_ttm", "<", 10), Condition("roe", ">", 5)],
        )
        new_spec = spec.without_condition(0)
        assert len(new_spec.conditions) == 1
        assert new_spec.conditions[0].field == "roe"


def test_engine_missing_field_warns():
    df = pd.DataFrame({"code": ["a"], "pe_ttm": [5]})
    spec = SelectorSpec(conditions=[Condition("roe", ">", 10)])
    out = SelectorEngine().run(spec, df)
    assert out.empty


# ============================================================
# templates
# ============================================================
def test_templates_load():
    tpl = list_templates()
    assert len(tpl) >= 3
    spec = get_template("low_valuation")
    assert isinstance(spec, SelectorSpec)
    assert len(spec.conditions) >= 2


# ============================================================
# LLM parser
# ============================================================
def test_parser_strips_code_fence():
    assert _strip_code_fence("```json\n{\"a\":1}\n```") == '{"a":1}'
    assert _strip_code_fence("  {\"a\":1}  ") == '{"a":1}'


def test_parser_valid_input():
    raw = json.dumps({
        "conditions": [{"field": "pe_ttm", "operator": "<", "value": 20}],
        "logic": "AND",
        "limit": 5,
    })
    out = parse_selector_json(raw)
    assert out["limit"] == 5


def test_parser_rejects_unknown_field():
    raw = json.dumps({"conditions": [{"field": "xxx", "operator": "<", "value": 1}]})
    with pytest.raises(Exception):
        parse_selector_json(raw)


def test_parser_rejects_bad_json():
    with pytest.raises(Exception):
        parse_selector_json("not json at all")


# ============================================================
# LLM 客户端（fake）
# ============================================================
class _FakeLLM(LLMClient):
    name = "fake"
    def __init__(self, response_text: str):
        super().__init__(model="fake")
        self._text = response_text

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return LLMResponse(content=self._text, model="fake")


def test_natural_language_to_spec_via_fake_llm():
    from quant_platform.llm.parser import natural_language_to_spec
    raw = json.dumps({
        "conditions": [{"field": "pe_ttm", "operator": "<", "value": 15}],
        "logic": "AND", "limit": 5,
    })
    client = _FakeLLM(raw)
    out = natural_language_to_spec(client, "PE 低于 15 的票")
    assert out["limit"] == 5


def test_natural_language_retries_on_bad_json():
    from quant_platform.llm.parser import natural_language_to_spec
    client = _FakeLLM("not json")
    with pytest.raises(Exception):
        natural_language_to_spec(client, "any", max_retries=1)


# ============================================================
# 跨字段比较：close > ma_20 > ma_60（均线多头）
# ============================================================
class TestCrossFieldComparison:
    """测试 Condition.compare_field 跨字段比较与 MA_BULL 模板。"""

    def _features(self) -> pd.DataFrame:
        # 5 只股票：A=多头排列(close=12, ma20=10, ma60=8) 命中
        #         B=仅 close>ma20(close=11, ma20=10, ma60=11) 第二个条件不满足
        #         C=都不满足(close=5, ma20=6, ma60=7)
        #         D=完全多头(close=20, ma20=15, ma60=10) 命中
        #         E=close==ma20(close=10, ma20=10, ma60=5) 严格 > 不命中
        return pd.DataFrame({
            "code": ["600000", "600001", "600002", "600003", "600004"],
            "name": ["A", "B", "C", "D", "E"],
            "close": [12.0, 11.0, 5.0, 20.0, 10.0],
            "ma_20": [10.0, 10.0, 6.0, 15.0, 10.0],
            "ma_60": [8.0, 11.0, 7.0, 10.0, 5.0],
        })

    def test_condition_from_dict_string_value_creates_compare_field(self):
        c = Condition.from_dict(
            {"field": "close", "operator": ">", "value": "ma_20"}
        )
        assert c.compare_field == "ma_20"
        assert c.field == "close"
        assert c.operator == ">"

    def test_condition_from_dict_explicit_compare_field(self):
        c = Condition.from_dict(
            {"field": "close", "operator": ">", "value": 0, "compare_field": "ma_20"}
        )
        assert c.compare_field == "ma_20"

    def test_condition_to_dict_round_trip_with_compare_field(self):
        original = Condition("close", ">", 0, compare_field="ma_20")
        d = original.to_dict()
        restored = Condition.from_dict(d)
        assert restored.compare_field == "ma_20"
        assert restored.field == "close"
        assert restored.operator == ">"

    def test_between_does_not_allow_compare_field(self):
        with pytest.raises(Exception):
            Condition.from_dict({
                "field": "close", "operator": "between",
                "value": 0, "value2": 100, "compare_field": "ma_20",
            })

    def test_ma_bull_template_uses_cross_field(self):
        spec = get_template("ma_bull")
        assert len(spec.conditions) == 2
        assert spec.conditions[0].field == "close"
        assert spec.conditions[0].compare_field == "ma_20"
        assert spec.conditions[1].field == "ma_20"
        assert spec.conditions[1].compare_field == "ma_60"

    def test_ma_bull_picks_only_strict_bull_arrangement(self):
        spec = get_template("ma_bull")
        engine = SelectorEngine()
        result = engine.run(spec, self._features())
        # 期望：A (12>10 && 10>8) 与 D (20>15 && 15>10) 命中
        codes = sorted(result["code"].tolist())
        assert codes == ["600000", "600003"]

    def test_ma_bull_drops_rows_with_nan_ma(self):
        spec = get_template("ma_bull")
        features = self._features()
        # 让 D 的 ma_20 变 NaN
        features.loc[features["code"] == "600003", "ma_20"] = float("nan")
        engine = SelectorEngine()
        result = engine.run(spec, features)
        codes = sorted(result["code"].tolist())
        assert codes == ["600000"]  # 只剩 A

    def test_ma_bull_missing_field_skips_condition(self):
        """特征表里没有 ma_20 → 整条 spec 应跳过（mask 全 False → 结果空）"""
        spec = get_template("ma_bull")
        engine = SelectorEngine()
        df = pd.DataFrame({
            "code": ["600000"],
            "name": ["A"],
            "close": [12.0],
            # 没有 ma_20 / ma_60
        })
        result = engine.run(spec, df)
        assert result.empty


class TestLlmParserCrossField:
    """LLM parser 应接受字符串 value（视为 compare_field）。"""

    def test_string_value_passes_validation(self):
        text = json.dumps({
            "conditions": [
                {"field": "close", "operator": ">", "value": "ma_20"},
                {"field": "ma_20", "operator": ">", "value": "ma_60"},
            ],
            "logic": "AND",
        })
        data = parse_selector_json(text)
        assert data["conditions"][0]["value"] == "ma_20"

    def test_string_value_must_be_known_field(self):
        text = json.dumps({
            "conditions": [
                {"field": "close", "operator": ">", "value": "unknown_field"},
            ],
        })
        from quant_platform.utils.exceptions import QuantPlatformError
        with pytest.raises(QuantPlatformError):
            parse_selector_json(text)

    def test_string_value_with_between_rejected(self):
        text = json.dumps({
            "conditions": [
                {"field": "close", "operator": "between",
                 "value": "ma_20", "value2": 100},
            ],
        })
        from quant_platform.utils.exceptions import QuantPlatformError
        with pytest.raises(QuantPlatformError):
            parse_selector_json(text)


# ============================================================
# history
# ============================================================
def test_history_save_and_list(tmp_path: Path):
    h = SelectorHistory(tmp_path / "s.db")
    rid = h.save(
        name="run1",
        spec=SelectorSpec(conditions=[Condition("pe_ttm", "<", 20)]),
        result_codes=["600000", "600001"],
    )
    rows = h.list_recent()
    assert rows[0]["result_count"] == 2
    assert h.get(rid)["name"] == "run1"
