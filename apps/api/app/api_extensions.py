from __future__ import annotations

import asyncio
import difflib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .admin import (
    COOKIE_NAME,
    configured,
    create_session_cookie,
    redact,
    require_admin,
    verify_origin,
    verify_token,
)
from .config import get_settings
from .db import (
    AgentProfileVersionRow,
    AuditLogRow,
    AutonomousProjectRow,
    EvaluationArenaRow,
    ResearchDebateRow,
    ResearchRunRow,
    SessionLocal,
    SupplyChainInvestigationRow,
    UniverseArtifactRow,
    get_session,
)
from .profiles import COMPONENTS, SKILLS, TOOLS, create_draft, seed_profiles, validate_profile
from .source_adapters import available_periods
from .universe import (
    active_snapshots,
    record_sync_failure,
    resolve_company,
    search_companies,
    sync_universes,
)

router = APIRouter(prefix="/api/v1")


async def audit(session: AsyncSession, action: str, target_type: str, target_id: str | None = None, detail: dict | None = None):
    session.add(AuditLogRow(
        id=str(uuid4()), action=action, actor="admin", target_type=target_type,
        target_id=target_id, detail_json=json.dumps(redact(detail or {}), ensure_ascii=False),
        created_at=datetime.now(UTC),
    ))


def profile_json(row: AgentProfileVersionRow) -> dict:
    return {
        "id": row.id, "poc_type": row.poc_type, "name": row.name, "version": row.version,
        "status": row.status, "config": redact(row.config), "validation_errors": row.validation_errors,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.get("/companies/search")
async def companies_search(q: str = Query(min_length=1), limit: int = Query(12, ge=1, le=25), session: AsyncSession = Depends(get_session)):
    return await search_companies(session, q, limit)


@router.get("/companies/{ticker}/periods")
async def company_periods(ticker: str, session: AsyncSession = Depends(get_session)):
    company = await resolve_company(session, ticker)
    if not company:
        raise HTTPException(404, f"{ticker} is not a current supported-universe member")
    try:
        periods = await available_periods(company["ticker"])
    except Exception as exc:
        raise HTTPException(502, f"Official filing-period lookup failed: {type(exc).__name__}") from exc
    return {"ticker": company["ticker"], "periods": periods, "default_period": periods[0] if periods else None}


EXECUTION_ROWS = {
    "earnings": (ResearchRunRow, "events"),
    "research": (ResearchRunRow, "events"),
    "autonomous": (AutonomousProjectRow, "project_events"),
    "arena": (EvaluationArenaRow, "arena_events"),
    "debate": (ResearchDebateRow, "debate_events"),
    "supply-chain": (SupplyChainInvestigationRow, "events"),
}


def execution_events(row, attribute: str) -> list[dict]:
    return getattr(row, attribute)


def execution_state(row) -> dict:
    if isinstance(row, ResearchDebateRow):
        return row.trace
    if isinstance(row, EvaluationArenaRow):
        return {}
    return row.state


@router.get("/executions/{execution_type}/{execution_id}/events")
async def unified_events(execution_type: str, execution_id: UUID, request: Request, after: int = 0, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    definition = EXECUTION_ROWS.get(execution_type)
    if not definition:
        raise HTTPException(404, "Unknown execution type")
    row_type, attribute = definition
    try:
        initial = max(after, int(last_event_id or 0))
    except ValueError:
        initial = after

    async def stream():
        sent, idle = initial, 0
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                return
            async with SessionLocal() as session:
                row = await session.get(row_type, str(execution_id))
                if not row:
                    payload = {"sequence": sent + 1, "kind": "error", "message": "not found"}
                    yield f"id: {sent + 1}\nevent: error\ndata: {json.dumps(payload)}\n\n"
                    return
                items = execution_events(row, attribute)
                for index, item in enumerate(items, 1):
                    sequence = int(item.get("sequence") or index)
                    if sequence <= sent:
                        continue
                    safe = redact(item)
                    yield f"id: {sequence}\ndata: {json.dumps(safe, ensure_ascii=False)}\n\n"
                    sent = sequence
                    idle = 0
                if row.status in {"completed", "failed", "awaiting_retry", "cancelled"}:
                    terminal = "complete" if row.status == "completed" else "failed"
                    yield f"event: {terminal}\ndata: {json.dumps({'status': row.status, 'last_sequence': sent})}\n\n"
                    return
            idle += 1
            if idle % 30 == 0:
                yield f": heartbeat {datetime.now(UTC).isoformat()}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/executions/{execution_type}/{execution_id}/trace", dependencies=[Depends(require_admin)])
async def unified_trace(execution_type: str, execution_id: UUID, session: AsyncSession = Depends(get_session)):
    definition = EXECUTION_ROWS.get(execution_type)
    if not definition:
        raise HTTPException(404, "Unknown execution type")
    row_type, attribute = definition
    row = await session.get(row_type, str(execution_id))
    if not row:
        raise HTTPException(404, "Execution not found")
    state = execution_state(row)
    config = json.loads(getattr(row, "config_json", "{}") or "{}")
    if not config and isinstance(state.get("config_snapshot"), dict):
        config = state["config_snapshot"]
    events = execution_events(row, attribute)
    tools = [event for event in events if str(event.get("kind", "")).startswith("tool.")]
    agents = state.get("agent_executions", [])
    if not agents and config.get("pipeline"):
        completed = set(state.get("completed_steps", []))
        agents = [
            {"task_id": node["id"], "agent_role": node["id"], "status": "completed" if row.status == "completed" or node["id"] in completed else ("running" if getattr(row, "current_step", None) == node["id"] else "pending"), "depends_on": node.get("depends_on", [])}
            for node in config["pipeline"] if node.get("enabled", True)
        ]
    sources = []
    if getattr(row, "report_json", None):
        try:
            sources = json.loads(row.report_json).get("sources", json.loads(row.report_json).get("source_documents", []))
        except (TypeError, json.JSONDecodeError):
            sources = []
    source_run_id = config.get("source_run_id") or state.get("source_run_id")
    if not sources and source_run_id:
        source_run = await session.get(ResearchRunRow, str(source_run_id))
        if source_run and source_run.report_json:
            try:
                sources = json.loads(source_run.report_json).get("sources", [])
            except (TypeError, json.JSONDecodeError):
                sources = []
    reasoning_summaries = state.get("reasoning_summaries", []) or [
        event.get("message", "")
        for event in events
        if "summary" in str(event.get("kind", "")) and event.get("message")
    ]
    checkpoints = state.get("checkpoints", []) or [
        event.get("payload", {}).get("checkpoint")
        for event in events
        if str(event.get("kind", "")).startswith("checkpoint.")
        and event.get("payload", {}).get("checkpoint")
    ]
    return redact({
        "execution_type": execution_type, "id": row.id, "status": row.status,
        "ticker": getattr(row, "company", None), "events": events,
        "provider": state.get("provider", config.get("provider")),
        "model": state.get("model", config.get("model")),
        "agents": agents, "tool_calls": tools, "sources": sources,
        "reasoning_summaries": reasoning_summaries,
        "checkpoints": checkpoints,
        "error": getattr(row, "error", None),
        "company_name": config.get("company_name"),
        "market": config.get("market"),
        "profile_version_id": config.get("profile_version_id"),
        "universe_version_id": config.get("universe_version_id"),
        "usage": {"input_tokens": state.get("input_tokens", 0), "output_tokens": state.get("output_tokens", 0),
                  "tool_calls": state.get("tool_calls", len(tools)), "estimated_cost_usd": state.get("estimated_cost_usd", 0),
                  "duration_ms": state.get("duration_ms", 0)},
        "config_snapshot": config,
    })


@router.post("/admin/session")
async def admin_login(body: dict, response: Response, request: Request):
    verify_origin(request)
    if not configured():
        raise HTTPException(503, "ADMIN_TOKEN and ADMIN_SESSION_SECRET must be configured")
    if not verify_token(str(body.get("token", ""))):
        raise HTTPException(401, "Invalid admin token")
    settings = get_settings()
    response.set_cookie(COOKIE_NAME, create_session_cookie(), httponly=True, secure=settings.admin_cookie_secure, samesite="strict", max_age=settings.admin_session_hours * 3600, path="/")
    return {"authenticated": True}


@router.delete("/admin/session", dependencies=[Depends(require_admin)])
async def admin_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


@router.get("/admin/status")
async def admin_status(request: Request):
    from .admin import verify_cookie
    settings = get_settings()
    return {
        "authenticated": verify_cookie(request.cookies.get(COOKIE_NAME)),
        "configured": configured(), "openai_configured": bool(settings.openai_api_key),
        "sec_user_agent_configured": bool(settings.sec_user_agent),
        "session_signing_secret_configured": bool(settings.admin_session_secret),
    }


@router.get("/admin/profiles", dependencies=[Depends(require_admin)])
async def profiles(session: AsyncSession = Depends(get_session)):
    await seed_profiles(session)
    rows = (await session.execute(select(AgentProfileVersionRow).order_by(AgentProfileVersionRow.poc_type, AgentProfileVersionRow.version.desc()))).scalars()
    return [profile_json(row) for row in rows]


@router.post("/admin/profiles/{poc_type}/drafts", dependencies=[Depends(require_admin)])
async def profile_draft(poc_type: str, body: dict, session: AsyncSession = Depends(get_session)):
    if poc_type not in COMPONENTS:
        raise HTTPException(404, "Unknown PoC type")
    row = await create_draft(session, poc_type, body.get("config"), body.get("name"))
    await audit(session, "profile.draft.created", "profile", row.id, {"version": row.version})
    await session.commit()
    return profile_json(row)


@router.post("/admin/profiles/{profile_id}/validate", dependencies=[Depends(require_admin)])
async def profile_validate(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AgentProfileVersionRow, str(profile_id))
    if not row:
        raise HTTPException(404, "Profile not found")
    errors = validate_profile(row.poc_type, row.config)
    row.validation_errors_json = json.dumps(errors, ensure_ascii=False)
    row.updated_at = datetime.now(UTC)
    await audit(session, "profile.validated", "profile", row.id, {"errors": errors})
    await session.commit()
    return {"valid": not errors, "errors": errors, "profile": profile_json(row)}


@router.post("/admin/profiles/{profile_id}/publish", dependencies=[Depends(require_admin)])
async def profile_publish(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AgentProfileVersionRow, str(profile_id))
    if not row:
        raise HTTPException(404, "Profile not found")
    if row.status != "draft":
        raise HTTPException(409, "Only a draft can be published")
    errors = validate_profile(row.poc_type, row.config)
    if errors:
        raise HTTPException(422, {"errors": errors})
    await session.execute(update(AgentProfileVersionRow).where(AgentProfileVersionRow.poc_type == row.poc_type, AgentProfileVersionRow.status == "published").values(status="archived", updated_at=datetime.now(UTC)))
    row.status, row.validation_errors_json, row.updated_at = "published", "[]", datetime.now(UTC)
    await audit(session, "profile.published", "profile", row.id, {"version": row.version})
    await session.commit()
    return profile_json(row)


@router.post("/admin/profiles/{profile_id}/rollback", dependencies=[Depends(require_admin)])
async def profile_rollback(profile_id: UUID, session: AsyncSession = Depends(get_session)):
    source = await session.get(AgentProfileVersionRow, str(profile_id))
    if not source:
        raise HTTPException(404, "Profile not found")
    draft = await create_draft(session, source.poc_type, source.config, f"Rollback to v{source.version}")
    if draft.validation_errors:
        raise HTTPException(422, {"errors": draft.validation_errors})
    await session.execute(update(AgentProfileVersionRow).where(AgentProfileVersionRow.poc_type == source.poc_type, AgentProfileVersionRow.status == "published").values(status="archived", updated_at=datetime.now(UTC)))
    draft.status = "published"
    await audit(session, "profile.rolled_back", "profile", draft.id, {"source_version": source.version})
    await session.commit()
    return profile_json(draft)


@router.get("/admin/profiles/{profile_id}/diff", dependencies=[Depends(require_admin)])
async def profile_diff(profile_id: UUID, other_id: UUID, session: AsyncSession = Depends(get_session)):
    left, right = await session.get(AgentProfileVersionRow, str(profile_id)), await session.get(AgentProfileVersionRow, str(other_id))
    if not left or not right:
        raise HTTPException(404, "Profile not found")
    a = json.dumps(left.config, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    b = json.dumps(right.config, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    return {"diff": "\n".join(difflib.unified_diff(a, b, fromfile=f"v{left.version}", tofile=f"v{right.version}", lineterm=""))}


@router.get("/admin/universe", dependencies=[Depends(require_admin)])
async def universe_status(session: AsyncSession = Depends(get_session)):
    snapshots = await active_snapshots(session)
    output = []
    for row in snapshots:
        artifacts = list((await session.execute(select(UniverseArtifactRow).where(UniverseArtifactRow.snapshot_id == row.id))).scalars())
        output.append({"id": row.id, "universe": row.universe, "version": row.version, "as_of_date": row.as_of_date,
                       "source_url": row.source_url, "content_hash": row.content_hash, "member_count": len(row.members),
                       "issuer_count": len({member.get('issuer_id', member['ticker']) for member in row.members}),
                       "sync_error": row.sync_error, "source_status": "verified" if artifacts else "bootstrap",
                       "artifacts": [{"source_url": item.source_url, "object_key": item.object_key, "content_hash": item.content_hash} for item in artifacts],
                       "created_at": row.created_at})
    return output


@router.post("/admin/universe/sync", dependencies=[Depends(require_admin)])
async def universe_sync(background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    try:
        result = await sync_universes(session)
    except Exception as exc:
        await session.rollback()
        await record_sync_failure(session, exc)
        await audit(session, "universe.sync.failed", "universe", detail={"error": f"{type(exc).__name__}: {exc}"})
        await session.commit()
        raise HTTPException(502, "Universe sync failed; previous active snapshots were retained") from exc
    await audit(session, "universe.synced", "universe", detail={"result": result})
    await session.commit()
    return result


@router.get("/admin/executions", dependencies=[Depends(require_admin)])
async def admin_executions(session: AsyncSession = Depends(get_session)):
    output = []
    for kind, row_type in (("earnings", ResearchRunRow), ("autonomous", AutonomousProjectRow), ("arena", EvaluationArenaRow), ("debate", ResearchDebateRow), ("supply-chain", SupplyChainInvestigationRow)):
        rows = (await session.execute(select(row_type).order_by(row_type.created_at.desc()).limit(30))).scalars()
        output.extend({"type": kind, "id": row.id, "status": row.status, "ticker": getattr(row, "company", None),
                       "label": getattr(row, "name", getattr(row, "question", getattr(row, "topic", getattr(row, "subject", kind)))),
                       "created_at": row.created_at, "updated_at": row.updated_at} for row in rows)
    return sorted(output, key=lambda item: item["created_at"], reverse=True)[:100]


@router.get("/admin/registry", dependencies=[Depends(require_admin)])
async def admin_registry():
    settings = get_settings()
    return {"models": [{"provider": "deterministic", "model": "template-v1", "configured": True},
                       {"provider": "openai", "model": settings.openai_model, "configured": bool(settings.openai_api_key)}],
            "components": COMPONENTS, "tools": TOOLS, "skills": SKILLS}


@router.get("/admin/audit-log", dependencies=[Depends(require_admin)])
async def audit_log(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AuditLogRow).order_by(AuditLogRow.created_at.desc()).limit(200))).scalars()
    return [{"id": row.id, "action": row.action, "actor": row.actor, "target_type": row.target_type,
             "target_id": row.target_id, "detail": redact(row.detail), "created_at": row.created_at} for row in rows]
