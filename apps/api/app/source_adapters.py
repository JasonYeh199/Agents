"""Official SEC and TWSE financial-source adapters.

Adapters return the same normalized bundle used by the deterministic fixtures.  A
missing accounting concept is recorded as unavailable instead of failing the run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .tools import ToolError, load_fixture

SEC_MAP = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
TWSE_INCOME_ENDPOINTS = (
    "t187ap06_L_ci", "t187ap06_L_basi", "t187ap06_L_bd",
    "t187ap06_L_fh", "t187ap06_L_ins", "t187ap06_L_mim",
)
TWSE_BASE = "https://openapi.twse.com.tw/v1/opendata/{endpoint}"


def _fixture_name(ticker: str) -> str | None:
    return {"NVDA": "nvidia", "nvidia": "nvidia", "2330.TW": "tsmc", "tsmc": "tsmc"}.get(ticker)


def maybe_fixture(ticker: str, period: str) -> dict[str, Any] | None:
    name = _fixture_name(ticker)
    if not name:
        return None
    try:
        return load_fixture(name, period)
    except ToolError as exc:
        if exc.code == "fixture_not_found":
            return None
        raise


def available_fixture_periods(ticker: str) -> list[str]:
    name = _fixture_name(ticker)
    if not name:
        return []
    root = Path(__file__).parents[1] / "fixtures"
    return sorted((path.stem.removeprefix(f"{name}-") for path in root.glob(f"{name}-FY*-Q*.json")), reverse=True)


async def _client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        timeout=settings.fetch_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": settings.sec_user_agent or "SignalForge/1.0", "Accept": "application/json"},
    )


async def _sec_identity(client: httpx.AsyncClient, ticker: str) -> tuple[str, str]:
    if not get_settings().sec_user_agent:
        raise ToolError("sec_user_agent_required", "SEC_USER_AGENT must identify the application and include contact information")
    response = await client.get(SEC_MAP)
    response.raise_for_status()
    payload = response.json()
    fields = payload["fields"]
    ti, ci, ni = fields.index("ticker"), fields.index("cik"), fields.index("name")
    for row in payload["data"]:
        if str(row[ti]).upper() == ticker:
            return str(row[ci]).zfill(10), str(row[ni])
    raise ToolError("sec_ticker_not_found", f"SEC ticker mapping does not contain {ticker}")


def _period_for_unit(unit: dict[str, Any]) -> str | None:
    fy, fp = unit.get("fy"), str(unit.get("fp", ""))
    if not fy:
        return None
    if fp in {"Q1", "Q2", "Q3"}:
        return f"FY{fy}-{fp}"
    if fp in {"FY", "Q4"}:
        return f"FY{fy}-Q4"
    return None


def _pick_fact(facts: dict[str, Any], concepts: tuple[str, ...], period: str) -> tuple[str, str, dict[str, Any]] | None:
    for concept in concepts:
        fact = facts.get(concept)
        if not fact:
            continue
        units = fact.get("units", {})
        ordered_units = sorted(units, key=lambda unit: (unit not in {"USD", "USD/shares"}, unit))
        for unit in ordered_units:
            candidates = units[unit]
            matched = [item for item in candidates if _period_for_unit(item) == period and item.get("form") in {"10-Q", "10-K", "20-F", "40-F", "6-K"}]
            if matched:
                return concept, unit, max(matched, key=lambda item: item.get("filed", ""))
    return None


def _format_value(item: dict[str, Any], per_share: bool = False, unit: str = "USD") -> str:
    value = item.get("val")
    if not isinstance(value, (int, float)):
        return "unavailable"
    currency = {"USD": "US$", "USD/shares": "US$", "EUR": "€", "EUR/shares": "€", "TWD": "NT$"}.get(unit, f"{unit} ")
    if per_share:
        return f"{currency}{value:,.2f}"
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{currency}{value / 1_000_000_000:,.2f} billion"
    if absolute >= 1_000_000:
        return f"{currency}{value / 1_000_000:,.2f} million"
    return f"{currency}{value:,.0f}"


async def sec_periods(ticker: str) -> list[str]:
    async with await _client() as client:
        cik, _ = await _sec_identity(client, ticker)
        response = await client.get(SEC_FACTS.format(cik=cik))
        response.raise_for_status()
        periods = {
            period
            for namespace in response.json().get("facts", {}).values()
            for fact in namespace.values()
            for values in fact.get("units", {}).values()
            for item in values
            if (period := _period_for_unit(item))
        }
    return sorted(periods, reverse=True)


async def load_sec_bundle(ticker: str, period: str) -> dict[str, Any]:
    async with await _client() as client:
        cik, name = await _sec_identity(client, ticker)
        facts_response, submissions_response = await __import__("asyncio").gather(
            client.get(SEC_FACTS.format(cik=cik)), client.get(SEC_SUBMISSIONS.format(cik=cik))
        )
        facts_response.raise_for_status()
        submissions_response.raise_for_status()
        payload = facts_response.json()
        submissions = submissions_response.json()
        namespaces = payload.get("facts", {})
        gaap = {**namespaces.get("ifrs-full", {}), **namespaces.get("us-gaap", {})}
    source_id = f"sec-facts-{cik}-{period.lower()}"
    sources = [{
        "id": source_id,
        "url": SEC_FACTS.format(cik=cik),
        "publisher": "U.S. Securities and Exchange Commission",
        "document_type": "SEC Company Facts",
        "published_at": datetime.now(UTC).isoformat(),
        "language": "en",
        "content": json.dumps(payload, ensure_ascii=False),
    }, {
        "id": f"sec-submissions-{cik}",
        "url": SEC_SUBMISSIONS.format(cik=cik),
        "publisher": "U.S. Securities and Exchange Commission",
        "document_type": "SEC Submissions",
        "published_at": datetime.now(UTC).isoformat(),
        "language": "en",
        "content": json.dumps(submissions, ensure_ascii=False),
    }]
    definitions = (
        ("revenue", "metrics", "Revenue", "營收", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"), False),
        ("gross-profit", "metrics", "Gross profit", "毛利", ("GrossProfit",), False),
        ("operating-income", "metrics", "Operating income", "營業利益", ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"), False),
        ("net-income", "metrics", "Net income", "淨利", ("NetIncomeLoss", "ProfitLoss"), False),
        ("diluted-eps", "metrics", "Diluted EPS", "稀釋每股盈餘", ("EarningsPerShareDiluted", "DilutedEarningsLossPerShare"), True),
    )
    facts: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for fact_id, category, label_en, label_zh, concepts, per_share in definitions:
        selected = _pick_fact(gaap, concepts, period)
        if not selected:
            unavailable.append(label_en)
            continue
        concept, unit, item = selected
        excerpt = f"{concept}: {_format_value(item, per_share, unit)}; filed {item.get('filed', 'unknown')} on {item.get('form', 'filing')}"
        facts.append({
            "id": fact_id, "category": category, "label_en": label_en, "label_zh": label_zh,
            "value": _format_value(item, per_share, unit), "period": period, "source_id": source_id,
            "locator": f"us-gaap:{concept};accession:{item.get('accn', 'unavailable')}", "excerpt": excerpt,
        })
    accessions = [fact["locator"].rsplit(":", 1)[-1] for fact in facts]
    recent = submissions.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    accession = next((item for item in accessions if item in accession_numbers), None)
    if accession:
        index = accession_numbers.index(accession)
        primary = recent.get("primaryDocument", [])[index]
        form = recent.get("form", ["filing"] * len(accession_numbers))[index]
        filed = recent.get("filingDate", [datetime.now(UTC).date().isoformat()] * len(accession_numbers))[index]
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary}"
        async with await _client() as client:
            filing_response = await client.get(filing_url)
            filing_response.raise_for_status()
        if len(filing_response.content) <= get_settings().fetch_max_bytes:
            filing_id = f"sec-filing-{accession}"
            sources.append({
                "id": filing_id, "url": filing_url,
                "publisher": "U.S. Securities and Exchange Commission",
                "document_type": form, "published_at": f"{filed}T00:00:00+00:00",
                "language": "en", "content": filing_response.text,
            })
            for fact in facts:
                if accession in fact["locator"]:
                    fact["source_id"] = filing_id
    return {
        "summary_en": f"{name} official filing facts for {period}. Missing concepts are explicitly marked unavailable.",
        "summary_zh": f"{name} {period} 官方申報數據摘要；缺少的會計項目已明確標示為無法取得。",
        "catalysts_en": [], "catalysts_zh": [], "risks_en": ["Only official filing data is included."],
        "risks_zh": ["本報告僅納入官方申報資料。"], "sources": sources, "facts": facts,
        "unavailable": unavailable, "periods": await sec_periods(ticker),
    }


def _tw_period(row: dict[str, Any]) -> str | None:
    year = next((row.get(key) for key in ("年度", "年", "Year") if row.get(key)), None)
    quarter = next((row.get(key) for key in ("季別", "季", "Quarter") if row.get(key)), None)
    if year and quarter:
        digits = "".join(ch for ch in str(quarter) if ch.isdigit())
        western = int(year) + 1911 if int(year) < 1911 else int(year)
        if digits and 1 <= int(digits) <= 4:
            return f"FY{western}-Q{int(digits)}"
    return None


async def _twse_rows(ticker: str) -> tuple[list[dict[str, Any]], str]:
    base = ticker.removesuffix(".TW")
    async with await _client() as client:
        for endpoint in TWSE_INCOME_ENDPOINTS:
            response = await client.get(TWSE_BASE.format(endpoint=endpoint))
            response.raise_for_status()
            rows = [row for row in response.json() if str(row.get("公司代號", row.get("公司代碼", ""))).strip() == base]
            if rows:
                return rows, endpoint
    raise ToolError("twse_company_not_found", f"TWSE financial statements do not contain {ticker}")


async def twse_periods(ticker: str) -> list[str]:
    rows, _ = await _twse_rows(ticker)
    return sorted({period for row in rows if (period := _tw_period(row))}, reverse=True)


def _tw_value(row: dict[str, Any], names: tuple[str, ...]) -> tuple[str, str] | None:
    for key, value in row.items():
        if any(name in key for name in names) and value not in (None, "", "--"):
            return key, str(value)
    return None


def _twse_row_for_period(rows: list[dict[str, Any]], period: str) -> dict[str, Any]:
    row = next((item for item in rows if _tw_period(item) == period), None)
    if row is None:
        raise ToolError("period_not_found", f"TWSE financial statements do not contain {period}")
    return row


async def load_twse_bundle(ticker: str, period: str) -> dict[str, Any]:
    rows, endpoint = await _twse_rows(ticker)
    row = _twse_row_for_period(rows, period)
    actual_period = _tw_period(row)
    assert actual_period is not None
    source_id = f"twse-{ticker.removesuffix('.TW')}-{actual_period.lower()}"
    definitions = (
        ("revenue", "營業收入", "營業收入", ("營業收入", "收益")),
        ("gross-profit", "Gross profit", "營業毛利", ("營業毛利",)),
        ("operating-income", "Operating income", "營業利益", ("營業利益", "營業淨利")),
        ("net-income", "Net income", "本期淨利", ("本期淨利", "本期稅後淨利", "淨利（損）")),
        ("eps", "EPS", "每股盈餘", ("基本每股盈餘", "每股盈餘")),
    )
    facts, unavailable = [], []
    for fact_id, label_en, label_zh, names in definitions:
        selected = _tw_value(row, names)
        if not selected:
            unavailable.append(label_en)
            continue
        key, value = selected
        facts.append({"id": fact_id, "category": "metrics", "label_en": label_en, "label_zh": label_zh,
                      "value": value, "period": actual_period, "source_id": source_id,
                      "locator": f"OpenAPI:{endpoint};field:{key}", "excerpt": f"{key}: {value}"})
    source_url = TWSE_BASE.format(endpoint=endpoint)
    company_name = str(row.get("公司名稱", row.get("公司簡稱", ticker)))
    return {
        "summary_en": f"{company_name} official TWSE financial statement data for {actual_period}.",
        "summary_zh": f"{company_name} {actual_period} TWSE 官方財務申報數據摘要。",
        "catalysts_en": [], "catalysts_zh": [], "risks_en": ["Only official regulatory data is included."],
        "risks_zh": ["本報告僅納入官方監管資料。"],
        "sources": [{"id": source_id, "url": source_url, "publisher": "Taiwan Stock Exchange",
                     "document_type": "financial_statement", "published_at": datetime.now(UTC).isoformat(),
                     "language": "zh-TW", "content": json.dumps(row, ensure_ascii=False)}],
        "facts": facts, "unavailable": unavailable,
        "periods": sorted({p for item in rows if (p := _tw_period(item))}, reverse=True),
    }


async def available_periods(ticker: str) -> list[str]:
    fixtures = available_fixture_periods(ticker) if get_settings().fixture_mode else []
    if fixtures:
        return fixtures
    return await (twse_periods(ticker) if ticker.endswith(".TW") else sec_periods(ticker))


async def load_source_bundle(ticker: str, period: str) -> dict[str, Any]:
    fixture = maybe_fixture(ticker, period) if get_settings().fixture_mode else None
    if fixture:
        fixture["_fixture"] = True
        fixture.setdefault("unavailable", [])
        fixture.setdefault("periods", available_fixture_periods(ticker))
        return fixture
    from .universe import normalize_ticker

    ticker = normalize_ticker(ticker)
    return await (load_twse_bundle(ticker, period) if ticker.endswith(".TW") else load_sec_bundle(ticker, period))
