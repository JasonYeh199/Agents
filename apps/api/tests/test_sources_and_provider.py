from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.providers import OpenAIProvider
from app.source_adapters import _pick_fact, _twse_row_for_period, load_source_bundle
from app.tools import ToolError
from app.universe import _parse_nasdaq, _parse_twse, _twse_as_of


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://"])
def test_render_postgres_url_uses_asyncpg_driver(scheme):
    settings = Settings(database_url=f"{scheme}user:secret@host/database")
    assert settings.database_url == "postgresql+asyncpg://user:secret@host/database"


def test_existing_async_database_url_is_preserved():
    value = "postgresql+asyncpg://user:secret@host/database"
    assert Settings(database_url=value).database_url == value


def test_nasdaq_parser_merges_multiple_share_classes_to_100_issuers():
    rows = [
        {"symbol": f"T{index}", "companyName": f"Company {index}", "marketCap": str(1_000_000 - index)}
        for index in range(99)
    ] + [
        {"symbol": "GOOGL", "companyName": "Alphabet A", "marketCap": "2000000"},
        {"symbol": "GOOG", "companyName": "Alphabet C", "marketCap": "1900000"},
    ]
    sec_rows = [[f"T{index}", index, f"Company {index}"] for index in range(99)] + [["GOOGL", 999, "Alphabet"], ["GOOG", 999, "Alphabet"]]
    parsed = _parse_nasdaq(
        {"data": {"data": {"rows": rows}}},
        {"fields": ["ticker", "cik", "name"], "data": sec_rows},
    )
    assert len(parsed) == 100
    alphabet = next(item for item in parsed if item["issuer_id"] == "999")
    assert {alphabet["ticker"], *alphabet["aliases"]} == {"GOOG", "GOOGL"}


def test_twse_parser_strictly_takes_ranked_first_100():
    html = "<table>" + "".join(f"<tr><td>{rank}</td><td>{2000 + rank}</td><td>Company</td></tr>" for rank in range(1, 102)) + "</table>"
    companies = [{"公司代號": str(2000 + rank), "公司名稱": f"公司 {rank}"} for rank in range(1, 102)]
    parsed = _parse_twse(html, companies)
    assert len(parsed) == 100
    assert parsed[0]["ticker"] == "2001.TW"
    assert parsed[-1]["rank"] == 100


def test_twse_snapshot_uses_the_official_source_date():
    assert _twse_as_of("<p>資料日期： 2026/8/31</p>") == "2026-08-31"
    assert _twse_as_of("<p>no date</p>") is None


def test_missing_financial_concept_is_a_nonfatal_unavailable_result():
    assert _pick_fact({}, ("Revenue",), "FY2025-Q1") is None


def test_twse_period_selection_never_substitutes_a_different_quarter():
    rows = [{"年度": "115", "季別": "2"}]
    assert _twse_row_for_period(rows, "FY2026-Q2") is rows[0]
    with pytest.raises(ToolError, match="do not contain FY2026-Q1"):
        _twse_row_for_period(rows, "FY2026-Q1")


@pytest.mark.asyncio
async def test_fixture_mode_never_constructs_network_client(monkeypatch):
    async def forbidden():
        raise AssertionError("network client must not be used for fixtures")

    monkeypatch.setattr("app.source_adapters._client", forbidden)
    bundle = await load_source_bundle("NVDA", "FY2025-Q4")
    assert bundle["_fixture"] is True
    assert bundle["facts"]


class Output(BaseModel):
    answer: str


class FakeStream:
    def __init__(self):
        self.events = [
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Checked evidence. "),
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Kept cited facts."),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __aiter__(self):
        async def iterate():
            for event in self.events:
                yield event

        return iterate()

    async def get_final_response(self):
        return SimpleNamespace(
            output_parsed=Output(answer="done"),
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            id="response-1",
        )


@pytest.mark.asyncio
async def test_openai_provider_streams_supported_reasoning_summary_and_usage():
    captured = {}

    class Responses:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return FakeStream()

    provider = object.__new__(OpenAIProvider)
    provider.settings = Settings(openai_api_key="test", reasoning_effort="high", max_output_tokens=900)
    provider.settings = provider.settings.model_copy(update={"reasoning_summary": "concise"})
    provider.client = SimpleNamespace(responses=Responses())
    deltas = []
    output, usage = await provider.generate_structured("instructions", {"value": 1}, Output, deltas.append)
    assert output.answer == "done"
    assert usage.input_tokens == 11 and usage.output_tokens == 7
    assert usage.reasoning_summaries == ["Checked evidence. Kept cited facts."]
    assert deltas == ["Checked evidence. ", "Kept cited facts."]
    assert captured["reasoning"] == {"effort": "high", "summary": "concise"}
    assert captured["max_output_tokens"] == 900


@pytest.mark.asyncio
async def test_openai_provider_retries_without_unsupported_summary_field():
    class BadRequestError(Exception):
        pass

    calls = []

    class Responses:
        def stream(self, **kwargs):
            calls.append(kwargs["reasoning"])
            if "summary" in kwargs["reasoning"]:
                raise BadRequestError("summary is unsupported")
            stream = FakeStream()
            stream.events = []
            return stream

    provider = object.__new__(OpenAIProvider)
    provider.settings = Settings(openai_api_key="test", reasoning_effort="medium")
    provider.settings = provider.settings.model_copy(update={"reasoning_summary": "auto"})
    provider.client = SimpleNamespace(responses=Responses())
    output, usage = await provider.generate_structured("instructions", {}, Output)
    assert output.answer == "done"
    assert usage.reasoning_summaries == []
    assert calls == [{"effort": "medium", "summary": "auto"}, {"effort": "medium"}]
