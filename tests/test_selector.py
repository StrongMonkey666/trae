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
