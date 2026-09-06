"""Versioned, fail-safe company universe management.

The database snapshot is the authority used by a run.  Network synchronization builds a
complete candidate first and only then flips the active flag, so a provider outage can
never invalidate an existing universe or an in-flight run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import UniverseArtifactRow, UniverseSnapshotRow
from .storage import ObjectStore

logger = logging.getLogger(__name__)

NASDAQ_SOURCE = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
NASDAQ_PAGE = "https://www.nasdaq.com/products/global-indexes/nasdaq-100/companies"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
TWSE_SOURCE = "https://www.taifex.com.tw/cht/2/weightedPropertion"
TWSE_COMPANIES = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

ALIASES = {"nvidia": "NVDA", "tsmc": "2330.TW"}
_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_TW = re.compile(r"^\d{4}(?:\.TW)?$", re.IGNORECASE)

# Offline bootstrap data is deliberately a snapshot rather than a permissive fallback.
# The first successful official synchronization replaces it atomically.
_NASDAQ_BOOTSTRAP = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "AVGO", "GOOGL", "GOOG", "TSLA", "COST", "NFLX", "AMD", "ASML", "CSCO", "PEP", "TMUS", "LIN", "AZN", "ADBE", "QCOM", "TXN", "INTU", "AMGN", "ISRG", "BKNG", "AMAT", "HON", "CMCSA", "VRTX", "LRCX", "ADP", "SBUX", "GILD", "MU", "MDLZ", "PANW", "REGN", "MELI", "ADI", "KLAC", "SNPS", "CDNS", "CRWD", "PYPL", "MAR", "ABNB", "CEG", "DASH", "ORLY", "CSX", "PCAR", "MNST", "ROP", "NXPI", "MCHP", "FTNT", "CPRT", "WDAY", "ADSK", "ODFL", "PAYX", "KDP", "FAST", "AEP", "EXC", "XEL", "IDXX", "FANG", "BKR", "CCEP", "GEHC", "KHC", "WBD", "TTWO", "AXON", "ROST", "DXCM", "BIIB", "ILMN", "MRVL", "TEAM", "ZS", "DDOG", "MDB", "ANSS", "ON", "GFS", "ARM", "PLTR", "APP", "MSTR", "SHOP", "PDD", "JD", "LCID", "RIVN", "WBA", "SIRI", "DLTR", "EA", "CTAS"]

_TWSE_BOOTSTRAP = ["2330", "2317", "2454", "2308", "2881", "2382", "2891", "2882", "2303", "3711", "2412", "2886", "2884", "1216", "2885", "2892", "1301", "1303", "2002", "5880", "3008", "3034", "2357", "2379", "3045", "2880", "2883", "5871", "3037", "6669", "2887", "2890", "3231", "2327", "2376", "2345", "2912", "4904", "2603", "2615", "1101", "1590", "5876", "4938", "6505", "2207", "2395", "2408", "3661", "3017", "2618", "2105", "2801", "1402", "9910", "2301", "2383", "2356", "2409", "2609", "1476", "2474", "3481", "3443", "9904", "6415", "2344", "2360", "2606", "8454", "2324", "2834", "2888", "3023", "2377", "1605", "2027", "1504", "2542", "2610", "1802", "2633", "2201", "3533", "9914", "3702", "2498", "6239", "8046", "3653", "4958", "1229", "1722", "9921", "2404", "2101", "9945", "1102", "2059", "4763"]


def normalize_ticker(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("ticker is required")
    alias = ALIASES.get(value.lower())
    if alias:
        return alias
    upper = value.upper()
    if _TW.fullmatch(upper):
        return upper if upper.endswith(".TW") else f"{upper}.TW"
    if not _TICKER.fullmatch(upper):
        raise ValueError("invalid ticker format")
    return upper


def _bootstrap_members(universe: str) -> list[dict[str, Any]]:
    if universe == "nasdaq100":
        tickers: list[tuple[str, int, str]] = []
        issuer_ranks: dict[str, int] = {}
        for ticker in dict.fromkeys(_NASDAQ_BOOTSTRAP):
            issuer = "alphabet" if ticker in {"GOOG", "GOOGL"} else ticker
            if issuer not in issuer_ranks:
                if len(issuer_ranks) >= 100:
                    continue
                issuer_ranks[issuer] = len(issuer_ranks) + 1
            tickers.append((ticker, issuer_ranks[issuer], issuer))
        return [
            {
                "ticker": ticker,
                "name": "NVIDIA Corporation" if ticker == "NVDA" else ("Apple Inc." if ticker == "AAPL" else ticker),
                "market": "US",
                "exchange": "Nasdaq",
                "rank": rank,
                "universe": universe,
                "aliases": ["nvidia"] if ticker == "NVDA" else [],
                "issuer_id": issuer,
            }
            for ticker, rank, issuer in tickers
        ]
    return [
        {
            "ticker": f"{ticker}.TW",
            "name": "台灣積體電路製造股份有限公司" if ticker == "2330" else ticker,
            "market": "TW",
            "exchange": "TWSE",
            "rank": rank,
            "universe": universe,
            "aliases": ["tsmc", "2330"] if ticker == "2330" else [ticker],
            "issuer_id": ticker,
        }
        for rank, ticker in enumerate(_TWSE_BOOTSTRAP[:100], 1)
    ]


def _digest(members: list[dict[str, Any]]) -> str:
    raw = json.dumps(members, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def ensure_bootstrap_universe(session: AsyncSession) -> None:
    stamp = datetime.now(UTC)
    today = stamp.date().isoformat()
    for universe, source in (("nasdaq100", NASDAQ_PAGE), ("twse100", TWSE_SOURCE)):
        active = await session.scalar(select(UniverseSnapshotRow).where(UniverseSnapshotRow.universe == universe, UniverseSnapshotRow.active == 1))
        if active:
            issuer_count = len({member.get("issuer_id", member["ticker"]) for member in active.members})
            if issuer_count == 100:
                continue
            if not active.version.startswith("bootstrap-"):
                continue
        members = _bootstrap_members(universe)
        digest = _digest(members)
        await session.execute(update(UniverseSnapshotRow).where(UniverseSnapshotRow.universe == universe).values(active=0))
        session.add(
            UniverseSnapshotRow(
                id=str(uuid4()),
                universe=universe,
                version=f"bootstrap-{today}-{digest[:10]}",
                as_of_date=today,
                source_url=source,
                content_hash=digest,
                members_json=json.dumps(members, ensure_ascii=False),
                active=1,
                created_at=stamp,
            )
        )
    await session.commit()


async def active_snapshots(session: AsyncSession) -> list[UniverseSnapshotRow]:
    await ensure_bootstrap_universe(session)
    result = await session.execute(
        select(UniverseSnapshotRow)
        .where(UniverseSnapshotRow.active == 1)
        .order_by(UniverseSnapshotRow.universe)
    )
    return list(result.scalars())


async def search_companies(session: AsyncSession, query: str, limit: int = 12) -> list[dict[str, Any]]:
    query = query.strip().lower()
    if not query:
        return []
    output: list[dict[str, Any]] = []
    for snapshot in await active_snapshots(session):
        for member in snapshot.members:
            haystack = " ".join(
                [member["ticker"], member["name"], *member.get("aliases", [])]
            ).lower()
            if query in haystack:
                matched_security = next(
                    (
                        alias
                        for alias in member.get("aliases", [])
                        if _TICKER.fullmatch(str(alias).upper())
                        and str(alias).lower().startswith(query)
                    ),
                    None,
                )
                output.append(
                    {
                        **member,
                        "ticker": str(matched_security).upper() if matched_security else member["ticker"],
                        "universe_as_of": snapshot.as_of_date,
                        "universe_version_id": snapshot.id,
                        "source_url": snapshot.source_url,
                        "content_hash": snapshot.content_hash,
                    }
                )
    output.sort(key=lambda item: (not item["ticker"].lower().startswith(query), item["rank"]))
    return output[: max(1, min(limit, 25))]


async def resolve_company(session: AsyncSession, value: str) -> dict[str, Any] | None:
    ticker = normalize_ticker(value)
    for snapshot in await active_snapshots(session):
        for member in snapshot.members:
            aliases = {str(x).lower() for x in member.get("aliases", [])}
            if member["ticker"] == ticker or value.lower() in aliases:
                return {
                    **member,
                    "ticker": ticker if ticker in {member["ticker"], *member.get("aliases", [])} else member["ticker"],
                    "universe_as_of": snapshot.as_of_date,
                    "universe_version_id": snapshot.id,
                    "source_url": snapshot.source_url,
                    "content_hash": snapshot.content_hash,
                }
    return None


def _parse_nasdaq(payload: dict[str, Any], sec_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data", {}).get("data", {}).get("rows", [])
    fields = sec_payload.get("fields", [])
    cik_by_ticker: dict[str, str] = {}
    if fields and "ticker" in fields and "cik" in fields:
        ti, ci = fields.index("ticker"), fields.index("cik")
        cik_by_ticker = {str(row[ti]).upper(): str(row[ci]) for row in sec_payload.get("data", [])}
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = normalize_ticker(str(row.get("symbol", "")))
        issuer = cik_by_ticker.get(ticker, "alphabet" if ticker in {"GOOG", "GOOGL"} else ticker)
        cap = int(re.sub(r"\D", "", str(row.get("marketCap", "0"))) or 0)
        item = grouped.setdefault(
            issuer,
            {"ticker": ticker, "name": row.get("companyName") or ticker, "market_cap": cap, "aliases": [], "issuer_id": issuer},
        )
        if ticker != item["ticker"]:
            item["aliases"].append(ticker)
        item["market_cap"] = max(item["market_cap"], cap)
    issuers = sorted(grouped.values(), key=lambda item: item["market_cap"], reverse=True)[:100]
    if len(issuers) != 100:
        raise ValueError(f"Nasdaq universe has {len(issuers)} issuers; expected 100")
    return [
        {
            "ticker": item["ticker"],
            "name": item["name"],
            "market": "US",
            "exchange": "Nasdaq",
            "rank": rank,
            "universe": "nasdaq100",
            "aliases": item["aliases"] + (["nvidia"] if item["ticker"] == "NVDA" else []),
            "issuer_id": item["issuer_id"],
        }
        for rank, item in enumerate(issuers, 1)
    ]


def _parse_twse(html: str, companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {
        str(row.get("公司代號", "")).strip(): str(row.get("公司名稱", row.get("公司簡稱", ""))).strip()
        for row in companies
    }
    ranked: dict[int, str] = {}
    for tr in BeautifulSoup(html, "html.parser").select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.select("th,td")]
        if len(cells) < 2 or not re.fullmatch(r"\d{1,3}", cells[0].replace(",", "")):
            continue
        rank = int(cells[0].replace(",", ""))
        ticker = next((m.group(0) for cell in cells[1:3] for m in [re.search(r"(?<!\d)\d{4}(?!\d)", cell)] if m), None)
        if 1 <= rank <= 100 and ticker:
            ranked[rank] = ticker
    if len(ranked) < 100:
        raise ValueError(f"TWSE ranking has {len(ranked)} ranked symbols; expected 100")
    found = [ranked[rank] for rank in range(1, 101)]
    return [
        {
            "ticker": f"{ticker}.TW",
            "name": names.get(ticker) or ticker,
            "market": "TW",
            "exchange": "TWSE",
            "rank": rank,
            "universe": "twse100",
            "aliases": [ticker] + (["tsmc"] if ticker == "2330" else []),
            "issuer_id": ticker,
        }
        for rank, ticker in enumerate(found[:100], 1)
    ]


def _twse_as_of(html: str) -> str | None:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = re.search(r"資料日期\s*[:：]\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, tzinfo=UTC).date().isoformat()
    except ValueError:
        return None


async def _fetch_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    import asyncio

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
    assert last_error is not None
    raise last_error


async def sync_universes(session: AsyncSession) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.sec_user_agent:
        raise ValueError("SEC_USER_AGENT must be configured before universe synchronization")
    headers = {"User-Agent": settings.sec_user_agent, "Accept": "application/json,text/html"}
    async with httpx.AsyncClient(timeout=settings.fetch_timeout_seconds, headers=headers, follow_redirects=True) as client:
        nasdaq_response, sec_response, tw_response, companies_response = await __import__("asyncio").gather(
            _fetch_with_retry(client, NASDAQ_SOURCE, {"User-Agent": "Mozilla/5.0 SignalForge/1.0", "Accept": "application/json"}),
            _fetch_with_retry(client, SEC_TICKERS),
            _fetch_with_retry(client, TWSE_SOURCE),
            _fetch_with_retry(client, TWSE_COMPANIES),
        )
        candidates = {
            "nasdaq100": (_parse_nasdaq(nasdaq_response.json(), sec_response.json()), NASDAQ_PAGE, None),
            "twse100": (_parse_twse(tw_response.text, companies_response.json()), TWSE_SOURCE, _twse_as_of(tw_response.text)),
        }
        raw_sources = {
            "nasdaq100": [(NASDAQ_SOURCE, nasdaq_response.content), (SEC_TICKERS, sec_response.content)],
            "twse100": [(TWSE_SOURCE, tw_response.content), (TWSE_COMPANIES, companies_response.content)],
        }
    stamp = datetime.now(UTC)
    result: list[dict[str, Any]] = []
    for universe, (members, source, source_as_of) in candidates.items():
        digest = _digest(members)
        current = await session.scalar(
            select(UniverseSnapshotRow).where(
                UniverseSnapshotRow.universe == universe,
                UniverseSnapshotRow.content_hash == digest,
                UniverseSnapshotRow.version.like(f"{stamp:%Y-%m}-%"),
            )
        )
        if current:
            reactivated = not bool(current.active)
            if reactivated:
                await session.execute(
                    update(UniverseSnapshotRow)
                    .where(UniverseSnapshotRow.universe == universe)
                    .values(active=0)
                )
                current.active = 1
            current.sync_error = None
            result.append(
                {
                    "universe": universe,
                    "version": current.version,
                    "changed": reactivated,
                    "count": len(members),
                }
            )
            continue
        await session.execute(update(UniverseSnapshotRow).where(UniverseSnapshotRow.universe == universe).values(active=0))
        row = UniverseSnapshotRow(
            id=str(uuid4()), universe=universe, version=f"{stamp:%Y-%m}-{digest[:10]}",
            as_of_date=source_as_of or stamp.date().isoformat(), source_url=source, content_hash=digest,
            members_json=json.dumps(members, ensure_ascii=False), active=1, created_at=stamp,
        )
        session.add(row)
        store = ObjectStore()
        for index, (url, content) in enumerate(raw_sources[universe], 1):
            object_key, source_hash = store.put(f"universes/{row.id}/source-{index}.raw", content)
            session.add(
                UniverseArtifactRow(
                    id=str(uuid4()), snapshot_id=row.id, source_url=url, object_key=object_key,
                    content_hash=source_hash, fetched_at=stamp,
                )
            )
        result.append({"universe": universe, "version": row.version, "changed": True, "count": len(members)})
    await session.commit()
    return result


async def record_sync_failure(session: AsyncSession, error: Exception | str) -> None:
    message = str(error)
    if isinstance(error, Exception):
        message = f"{type(error).__name__}: {error}"
    await session.execute(
        update(UniverseSnapshotRow)
        .where(UniverseSnapshotRow.active == 1)
        .values(sync_error=message[:1000])
    )
    await session.commit()


def seconds_until_sync(now: datetime, hour_utc: int) -> float:
    """Return the delay to the next configured UTC synchronization window."""
    hour = max(0, min(int(hour_utc), 23))
    current = now.astimezone(UTC)
    target = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return max(1.0, (target - current).total_seconds())


async def automatic_sync_loop() -> None:
    """Check daily; unchanged data creates at most one immutable snapshot per month."""
    import asyncio

    from .db import SessionLocal

    await asyncio.sleep(5)
    while True:
        try:
            async with SessionLocal() as session:
                await sync_universes(session)
        except Exception as exc:
            # A failed candidate never flips active flags; the next daily check retries.
            logger.exception("Automatic universe synchronization failed; retaining active snapshots")
            try:
                async with SessionLocal() as failure_session:
                    await record_sync_failure(failure_session, exc)
            except Exception:
                logger.exception("Failed to persist universe synchronization status")
        await asyncio.sleep(
            seconds_until_sync(datetime.now(UTC), get_settings().universe_sync_hour_utc)
        )
