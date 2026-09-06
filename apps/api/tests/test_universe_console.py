
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.admin import redact
from app.config import REPOSITORY_ENV
from app.main import app
from app.profiles import SKILLS, default_profile, validate_profile
from app.universe import _bootstrap_members, normalize_ticker, seconds_until_sync


def test_ticker_normalization_and_bootstrap_issuer_counts():
    assert normalize_ticker("nvda") == "NVDA"
    assert normalize_ticker("NVIDIA") == "NVDA"
    assert normalize_ticker("2330") == "2330.TW"
    assert normalize_ticker("2330.tw") == "2330.TW"
    assert normalize_ticker("tsmc") == "2330.TW"
    nasdaq = _bootstrap_members("nasdaq100")
    twse = _bootstrap_members("twse100")
    assert len({item["issuer_id"] for item in nasdaq}) == 100
    assert len({item["issuer_id"] for item in twse}) == 100


def test_trace_redaction_hides_secrets_but_keeps_usage_tokens():
    value = redact({"api_key": "secret", "authorization": "Bearer abc.def", "output_tokens": 42})
    assert value["api_key"] == "[REDACTED]"
    assert value["authorization"] == "[REDACTED]"
    assert value["output_tokens"] == 42


def test_profile_validator_rejects_cycles_unknown_tools_and_missing_terminal():
    config = default_profile("earnings")
    config["pipeline"][0]["depends_on"] = ["evaluate"]
    config["tools"].append("shell_anything")
    config["pipeline"][-1]["enabled"] = False
    errors = validate_profile("earnings", config)
    assert any("cycle" in error for error in errors)
    assert any("unknown tools" in error for error in errors)
    assert any("terminal" in error for error in errors)


def test_profile_registry_uses_real_versioned_skills_and_rejects_secret_fields():
    config = default_profile("earnings")
    assert "citation-auditor" in config["skills"]
    assert set(config["skills"]) <= set(SKILLS)
    config["api_key"] = "must-not-be-stored"
    errors = validate_profile("earnings", config)
    assert any("environment-only" in error for error in errors)


def test_profile_validator_reports_malformed_capability_lists_without_crashing():
    config = default_profile("earnings")
    config["tools"] = 42
    config["skills"] = "citation-auditor"
    errors = validate_profile("earnings", config)
    assert any("tools must be a list" in error for error in errors)
    assert any("skills must be a list" in error for error in errors)


def test_repository_env_and_daily_sync_window_are_stable():
    assert REPOSITORY_ENV.name == ".env"
    now = datetime(2026, 9, 6, 17, 30, tzinfo=UTC)
    assert seconds_until_sync(now, 18) == 30 * 60
    assert seconds_until_sync(now, 17) == 23.5 * 60 * 60


def test_search_periods_strict_universe_and_canonical_run():
    with TestClient(app) as client:
        found = client.get("/api/v1/companies/search", params={"q": "Apple"})
        assert found.status_code == 200
        assert found.json()[0]["ticker"] == "AAPL"
        assert {"market", "rank", "universe_as_of", "aliases"} <= found.json()[0].keys()

        periods = client.get("/api/v1/companies/2330/periods")
        assert periods.status_code == 200
        assert periods.json()["ticker"] == "2330.TW"
        assert periods.json()["default_period"] == "FY2024-Q4"

        rejected = client.post("/api/v1/research-runs", json={"ticker": "ZZZZ", "fiscal_period": "FY2025-Q4"})
        assert rejected.status_code == 422

        created = client.post("/api/v1/research-runs", json={"company": "nvidia", "fiscal_period": "FY2025-Q4"})
        assert created.status_code == 202
        body = created.json()
        assert body["ticker"] == "NVDA"
        assert body["company"] == "NVDA"
        assert body["universe_version_id"]
        assert body["profile_version_id"]


def test_unified_sse_resume_and_admin_trace_authentication():
    with TestClient(app) as client:
        run = client.post("/api/v1/research-runs", json={"ticker": "NVDA", "fiscal_period": "FY2025-Q4"}).json()
        stream = client.get(f"/api/v1/executions/earnings/{run['id']}/events", headers={"Last-Event-ID": "2"})
        ids = [int(line.removeprefix("id: ")) for line in stream.text.splitlines() if line.startswith("id: ")]
        assert ids and min(ids) >= 3
        assert "event: complete" in stream.text
        assert client.get(f"/api/v1/executions/earnings/{run['id']}/trace").status_code == 401

        blocked_login = client.post(
            "/api/v1/admin/session",
            json={"token": "test-admin-token"},
            headers={"Origin": "https://attacker.example"},
        )
        assert blocked_login.status_code == 403
        login = client.post("/api/v1/admin/session", json={"token": "test-admin-token"})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=strict" in login.headers["set-cookie"]
        trace = client.get(f"/api/v1/executions/earnings/{run['id']}/trace")
        assert trace.status_code == 200
        assert trace.json()["config_snapshot"]["universe_content_hash"]
        assert "reasoning_summaries" in trace.json()
        assert trace.json()["checkpoints"]
        blocked = client.post(
            "/api/v1/admin/profiles/earnings/drafts",
            json={},
            headers={"Origin": "https://attacker.example"},
        )
        assert blocked.status_code == 403


def test_profile_publish_is_versioned_and_only_affects_new_runs():
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/session", json={"token": "test-admin-token"}).status_code == 200
        profiles = client.get("/api/v1/admin/profiles").json()
        current = next(item for item in profiles if item["poc_type"] == "earnings" and item["status"] == "published")
        first = client.post("/api/v1/research-runs", json={"ticker": "NVDA", "fiscal_period": "FY2025-Q4"}).json()
        config = current["config"]
        config["prompt"] = f"Versioned test prompt from v{current['version']}"
        draft = client.post("/api/v1/admin/profiles/earnings/drafts", json={"config": config, "name": "Versioned earnings"})
        assert draft.status_code == 200
        draft_body = draft.json()
        validated = client.post(f"/api/v1/admin/profiles/{draft_body['id']}/validate")
        assert validated.json()["valid"] is True
        assert client.post(f"/api/v1/admin/profiles/{draft_body['id']}/publish").status_code == 200
        second = client.post("/api/v1/research-runs", json={"ticker": "NVDA", "fiscal_period": "FY2025-Q4"}).json()
        first_trace = client.get(f"/api/v1/executions/earnings/{first['id']}/trace").json()
        second_trace = client.get(f"/api/v1/executions/earnings/{second['id']}/trace").json()
        assert first_trace["config_snapshot"]["profile_version_id"] == current["id"]
        assert second_trace["config_snapshot"]["profile_version_id"] == draft_body["id"]
        assert first_trace["config_snapshot"]["prompt"] != second_trace["config_snapshot"]["prompt"]
        assert client.get("/api/v1/admin/audit-log").json()


def test_failed_universe_sync_rolls_back_and_retains_active_snapshots(monkeypatch):
    from sqlalchemy import update

    from app.db import UniverseSnapshotRow

    async def fail_after_staging(session):
        await session.execute(update(UniverseSnapshotRow).values(active=0))
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr("app.api_extensions.sync_universes", fail_after_staging)
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/session", json={"token": "test-admin-token"}).status_code == 200
        before = client.get("/api/v1/admin/universe").json()
        failed = client.post("/api/v1/admin/universe/sync")
        after = client.get("/api/v1/admin/universe").json()
        assert failed.status_code == 502
        assert {(item["id"], item["content_hash"]) for item in before} == {
            (item["id"], item["content_hash"]) for item in after
        }


def test_openai_profile_without_key_falls_back_to_deterministic_template():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/research-runs",
            json={"ticker": "NVDA", "fiscal_period": "FY2025-Q4", "config": {"provider": "openai"}},
        )
        assert created.status_code == 202
        trace = client.get(f"/api/v1/research-runs/{created.json()['id']}/trace").json()
        assert trace["provider"] == "deterministic"
        assert any(event["kind"] == "provider.fallback" for event in trace["events"])


def test_arena_uses_one_immutable_official_evidence_run_for_all_variants():
    variants = [
        {"id": "strict", "label": "Evidence first", "skills": ["citation-auditor"]},
        {"id": "fast", "label": "Fast baseline", "citation_audit": False, "critic_enabled": False},
    ]
    with TestClient(app) as client:
        arena = client.post(
            "/api/v1/evaluation-arenas",
            json={"name": "Shared evidence", "ticker": "NVDA", "fiscal_period": "FY2025-Q4", "variants": variants},
        ).json()
        arena = client.get(f"/api/v1/evaluation-arenas/{arena['id']}").json()
        assert arena["status"] == "completed"
        assert client.post("/api/v1/admin/session", json={"token": "test-admin-token"}).status_code == 200
        trace = client.get(f"/api/v1/executions/arena/{arena['id']}/trace").json()
        assert trace["config_snapshot"]["source_run_id"]
        assert trace["sources"]
        assert any(event["kind"] == "tool.completed" for event in trace["events"])
