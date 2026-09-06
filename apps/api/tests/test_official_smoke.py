import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.source_adapters import (
    _client,
    load_sec_bundle,
    load_twse_bundle,
    sec_periods,
    twse_periods,
)
from app.universe import (
    NASDAQ_SOURCE,
    SEC_TICKERS,
    TWSE_COMPANIES,
    TWSE_SOURCE,
    _fetch_with_retry,
    _parse_nasdaq,
    _parse_twse,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(os.getenv("SIGNALFORGE_NETWORK_TESTS") != "1", reason="set SIGNALFORGE_NETWORK_TESTS=1 for official-network smoke tests"),
]


@pytest.mark.asyncio
async def test_sec_company_periods_and_deterministic_bundle():
    periods = await sec_periods("AAPL")
    assert periods
    bundle = await load_sec_bundle("AAPL", periods[0])
    assert bundle["sources"]
    assert all(source["url"].startswith("https://") for source in bundle["sources"])


@pytest.mark.asyncio
async def test_twse_company_periods_and_deterministic_bundle():
    periods = await twse_periods("2330.TW")
    assert periods
    bundle = await load_twse_bundle("2330.TW", periods[0])
    assert bundle["sources"]
    assert all(source["url"].startswith("https://") for source in bundle["sources"])


@pytest.mark.asyncio
async def test_official_universe_sources_validate_before_activation():
    async with await _client() as client:
        nasdaq_response = await _fetch_with_retry(client, NASDAQ_SOURCE, {"User-Agent": "Mozilla/5.0 SignalForge/1.0", "Accept": "application/json"})
        sec_response = await _fetch_with_retry(client, SEC_TICKERS)
        twse_response = await _fetch_with_retry(client, TWSE_SOURCE)
        companies_response = await _fetch_with_retry(client, TWSE_COMPANIES)
    nasdaq = _parse_nasdaq(nasdaq_response.json(), sec_response.json())
    twse = _parse_twse(twse_response.text, companies_response.json())
    assert len({item["issuer_id"] for item in nasdaq}) == 100
    assert len(twse) == 100


def test_no_key_deterministic_api_completes_aapl_and_2330_from_live_official_data():
    settings = get_settings()
    previous_fixture_mode = settings.fixture_mode
    settings.fixture_mode = False
    try:
        with TestClient(app) as client:
            for ticker in ("AAPL", "2330.TW"):
                period_response = client.get(f"/api/v1/companies/{ticker}/periods")
                assert period_response.status_code == 200
                period = period_response.json()["default_period"]
                created = client.post("/api/v1/research-runs", json={"ticker": ticker, "fiscal_period": period, "output_language": "en"})
                assert created.status_code == 202
                run = client.get(f"/api/v1/research-runs/{created.json()['id']}").json()
                assert run["status"] == "completed", run.get("error")
                report = client.get(f"/api/v1/research-runs/{run['id']}/report").json()
                assert report["sources"]
    finally:
        settings.fixture_mode = previous_fixture_mode
