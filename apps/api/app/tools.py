import ipaddress
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import get_settings

OFFICIAL_HOSTS = {
    "investor.nvidia.com",
    "nvidianews.nvidia.com",
    "sec.gov",
    "www.sec.gov",
    "investor.tsmc.com",
    "mops.twse.com.tw",
    "www.twse.com.tw",
    "www.tsmc.com",
    "pr.tsmc.com",
    "news.skhynix.com",
    "investor.micron.com",
    "investor.vertiv.com",
    "www.vertiv.com",
    "investor.supermicro.com",
    "www.commerce.gov",
}
ALLOWED_MIME = {"text/html", "text/plain", "application/pdf", "application/json"}


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_official_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in OFFICIAL_HOSTS:
        raise ToolError("host_not_allowed", "Only allowlisted official HTTPS sources are accepted")
    try:
        for result in socket.getaddrinfo(parsed.hostname, 443):
            address = ipaddress.ip_address(result[4][0])
            if address.is_private or address.is_loopback or address.is_link_local:
                raise ToolError("unsafe_address", "Private and local network targets are blocked")
    except socket.gaierror as exc:
        raise ToolError("dns_failure", str(exc)) from exc
    return url


async def fetch_document(url: str) -> tuple[bytes, str]:
    validate_official_url(url)
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=settings.fetch_timeout_seconds, follow_redirects=False
    ) as client:
        response = await client.get(
            url, headers={"User-Agent": "SignalForge-PoC/1.0 research@example.invalid"}
        )
        response.raise_for_status()
        mime = response.headers.get("content-type", "").split(";")[0].lower()
        if mime not in ALLOWED_MIME:
            raise ToolError("mime_not_allowed", mime)
        if len(response.content) > settings.fetch_max_bytes:
            raise ToolError("document_too_large", "Source exceeded configured size limit")
        return response.content, mime


def parse_document(content: bytes, mime: str) -> list[dict]:
    if mime in {"text/html", "text/plain", "application/json"}:
        text = (
            BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
            if mime == "text/html"
            else content.decode("utf-8")
        )
    else:
        from io import BytesIO

        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    # Treat all source text as inert evidence; never interpolate it into system instructions.
    return [
        {"locator": f"paragraph:{i + 1}", "text": line.strip()}
        for i, line in enumerate(text.splitlines())
        if line.strip()
    ]


def load_fixture(company: str, period: str) -> dict:
    path = Path(__file__).parents[1] / "fixtures" / f"{company}-{period}.json"
    if not path.exists():
        raise ToolError(
            "fixture_not_found", f"No deterministic source fixture for {company} {period}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_source(raw: dict, object_store, run_id: str):
    content = raw["content"].encode()
    key, digest = object_store.put(f"{run_id}/{raw['id']}.txt", content)
    return {
        "id": raw["id"],
        "url": raw["url"],
        "publisher": raw["publisher"],
        "document_type": raw["document_type"],
        "published_at": raw["published_at"],
        "fetched_at": datetime.now(UTC).isoformat(),
        "sha256": digest,
        "object_key": key,
        "parser_version": "1.0.0",
        "language": raw["language"],
    }
