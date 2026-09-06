import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .arena import execute_arena
from .autonomous import evaluate_project, execute_project
from .config import get_settings
from .db import (
    AutonomousProjectRow,
    EvaluationArenaRow,
    InvestmentThesisRow,
    ResearchDebateRow,
    ResearchRunRow,
    SupplyChainInvestigationRow,
    get_run,
    get_session,
    init_db,
)
from .debate import evaluate_debate, execute_debate
from .evals import evaluate_report
from .harness import execute_run
from .profiles import resolved_run_config, seed_profiles
from .schemas import (
    AgentExecution,
    ArenaView,
    ArenaWinner,
    AutonomousProjectView,
    AutonomousReport,
    AutonomousTrace,
    CreateArena,
    CreateAutonomousProject,
    CreateDebate,
    CreateInvestigation,
    CreateRun,
    CreateThesis,
    DebateTrace,
    DebateVerdict,
    DebateView,
    EarningsReport,
    EvalResult,
    EvidenceGraph,
    InvestigationReport,
    InvestigationTask,
    InvestigationTrace,
    InvestigationView,
    RunEvent,
    RunView,
    ThesisSnapshot,
    ThesisView,
    TraceView,
    UpdateThesis,
)
from .source_adapters import available_periods
from .supply_chain import evaluate_investigation, execute_investigation
from .thesis import create_thesis_from_run, update_thesis_from_run
from .universe import (
    automatic_sync_loop,
    ensure_bootstrap_universe,
    normalize_ticker,
    resolve_company,
)


@asynccontextmanager
async def lifespan(app):
    await init_db()
    # Repair the single legacy smoke-test title that was submitted by Windows
    # PowerShell using its non-UTF-8 request-body encoding.
    async with __import__("app.db", fromlist=["SessionLocal"]).SessionLocal() as session:
        await ensure_bootstrap_universe(session)
        await seed_profiles(session)
        rows = (await session.execute(select(ResearchDebateRow))).scalars()
        for row in rows:
            if row.language == "zh-TW" and row.topic.count("?") >= 3:
                row.topic = "目前法說證據是否支持 AI 成長論點？"
                row.updated_at = datetime.now(UTC)
        await session.commit()
    sync_task = None
    settings = get_settings()
    if settings.universe_auto_sync and ":memory:" not in settings.database_url:
        sync_task = asyncio.create_task(automatic_sync_loop())
    yield
    if sync_task:
        sync_task.cancel()


app = FastAPI(title="SignalForge Research API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .api_extensions import router as extension_router

app.include_router(extension_router)


def view(row):
    config = json.loads(row.config_json or "{}")
    ticker = normalize_ticker(row.company)
    return RunView(
        id=UUID(row.id),
        company=ticker,
        fiscal_period=row.fiscal_period,
        output_language=row.output_language,
        status=row.status,
        current_step=row.current_step,
        progress=row.progress,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        ticker=ticker,
        company_name=config.get("company_name"),
        market=config.get("market"),
        universe_version_id=config.get("universe_version_id"),
        profile_version_id=config.get("profile_version_id"),
    )


async def validated_company_period(session: AsyncSession, ticker: str, period: str) -> dict:
    company = await resolve_company(session, ticker)
    if not company:
        raise HTTPException(422, f"{ticker} is not a current supported-universe member")
    try:
        periods = await available_periods(company["ticker"])
    except Exception as exc:
        raise HTTPException(502, f"Official filing-period lookup failed: {type(exc).__name__}") from exc
    if period not in periods:
        raise HTTPException(422, f"{period} is not an available filing period for {company['ticker']}")
    return company


@app.get("/health")
async def health():
    return {"status": "ok", "poc": 6, "features": ["earnings_research", "investment_thesis", "supply_chain_detective", "research_debate", "autonomous_analyst", "evaluation_arena"]}


@app.get("/api/v1/research-runs", response_model=list[RunView])
async def list_runs(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(ResearchRunRow).order_by(ResearchRunRow.created_at.desc()).limit(50)
    )
    return [view(row) for row in result.scalars()]


@app.post("/api/v1/research-runs", response_model=RunView, status_code=202)
async def create_run(
    request: CreateRun, background: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    company = await validated_company_period(session, request.ticker, request.fiscal_period)
    overrides = request.config.model_dump(exclude_unset=True)
    config, _ = await resolved_run_config(session, "earnings", overrides)
    config.update({
        "ticker": company["ticker"], "company_name": company["name"], "market": company["market"],
        "universe_version_id": company["universe_version_id"], "universe": company["universe"],
        "universe_as_of": company["universe_as_of"], "universe_source_url": company["source_url"],
        "universe_content_hash": company["content_hash"],
    })
    stamp = datetime.now(UTC)
    pipeline_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": config.get("profile_version_id"), "provider": config.get("provider"), "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"), "reasoning_summary": config.get("reasoning_summary"), "prompt_version": config.get("prompt_version"), "budgets": config.get("budgets", {}), "universe_version_id": config.get("universe_version_id"), "pipeline": config.get("pipeline", [])}}
    row = ResearchRunRow(
        id=str(uuid4()),
        company=company["ticker"],
        fiscal_period=request.fiscal_period,
        output_language=request.output_language.value,
        config_json=json.dumps(config, ensure_ascii=False),
        events_json=json.dumps([pipeline_event], ensure_ascii=False),
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    background.add_task(execute_run, row.id)
    return view(row)


@app.get("/api/v1/research-runs/{run_id}", response_model=RunView)
async def read_run(run_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_run(session, run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    return view(row)


@app.get("/api/v1/research-runs/{run_id}/report", response_model=EarningsReport)
async def read_report(run_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_run(session, run_id)
    if not row or not row.report_json:
        raise HTTPException(404, "Report not ready")
    report = EarningsReport.model_validate_json(row.report_json)
    report.ticker = normalize_ticker(report.company)
    report.company = report.ticker
    config = json.loads(row.config_json or "{}")
    report.company_name = report.company_name or config.get("company_name")
    report.market = report.market or config.get("market")
    report.universe_version_id = report.universe_version_id or config.get("universe_version_id")
    report.profile_version_id = report.profile_version_id or config.get("profile_version_id")
    return report


@app.get("/api/v1/research-runs/{run_id}/trace", response_model=TraceView)
async def read_trace(run_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_run(session, run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    state = row.state
    settings = get_settings()
    config = json.loads(row.config_json or "{}")
    return TraceView(
        run_id=run_id,
        events=[RunEvent.model_validate({**e, "payload": {}}) for e in row.events],
        model=state.get("model") or config.get("model") or settings.openai_model,
        provider=state.get("provider") or config.get("provider") or settings.model_provider,
        input_tokens=state.get("input_tokens", 0),
        output_tokens=state.get("output_tokens", 0),
        estimated_cost_usd=state.get("estimated_cost_usd", 0),
        tool_calls=state.get("tool_calls", 0),
        duration_ms=state.get("duration_ms", 0),
        reasoning_summaries=[{"text": item} for item in state.get("reasoning_summaries", [])],
        agents=[{"id": item.get("id"), "status": "completed" if item.get("id") in state.get("completed_steps", []) else "pending", "depends_on": item.get("depends_on", [])} for item in config.get("pipeline", []) if item.get("enabled", True)],
        config_snapshot={
            "profile_version_id": config.get("profile_version_id"),
            "universe_version_id": config.get("universe_version_id"),
        },
        profile_version_id=config.get("profile_version_id"),
    )


@app.get("/api/v1/research-runs/{run_id}/events")
async def events(run_id: UUID, request: Request, after: int = 0, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    from .api_extensions import unified_events

    return await unified_events("earnings", run_id, request, after, last_event_id)


@app.post("/api/v1/research-runs/{run_id}/retry", response_model=RunView, status_code=202)
async def retry(
    run_id: UUID, background: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    row = await get_run(session, run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    if row.status not in {"awaiting_retry", "failed"}:
        raise HTTPException(409, "Run is not retryable")
    row.status, row.error = "queued", None
    await session.commit()
    background.add_task(execute_run, row.id)
    return view(row)


@app.post("/api/v1/research-runs/{run_id}/evaluate", response_model=EvalResult)
async def evaluate(run_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_run(session, run_id)
    if not row or not row.report_json:
        raise HTTPException(409, "Report not ready")
    result = evaluate_report(run_id, EarningsReport.model_validate_json(row.report_json))
    row.eval_json = result.model_dump_json()
    await session.commit()
    return result


def thesis_view(row: InvestmentThesisRow) -> ThesisView:
    snapshot = ThesisSnapshot.model_validate(row.thesis)
    snapshot.company = normalize_ticker(snapshot.company)
    snapshot.source_run_ids = row.source_run_ids
    versions = [ThesisSnapshot.model_validate(item) for item in row.versions]
    for index, version in enumerate(versions, start=1):
        version.company = normalize_ticker(version.company)
        version.source_run_ids = row.source_run_ids[:index]
    return ThesisView(
        id=UUID(row.id),
        status=row.status,
        snapshot=snapshot,
        versions=versions,
        events=[RunEvent.model_validate(item) for item in row.thesis_events],
    )


@app.get("/api/v1/theses", response_model=list[ThesisView])
async def read_theses(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(InvestmentThesisRow).order_by(InvestmentThesisRow.updated_at.desc())
    )
    return [thesis_view(row) for row in result.scalars()]


@app.post("/api/v1/theses", response_model=ThesisView, status_code=201)
async def create_thesis(request: CreateThesis, session: AsyncSession = Depends(get_session)):
    try:
        thesis_id = await create_thesis_from_run(request.source_run_id, request.title)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    row = await session.get(InvestmentThesisRow, str(thesis_id))
    return thesis_view(row)


@app.get("/api/v1/theses/{thesis_id}", response_model=ThesisView)
async def read_thesis(thesis_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(InvestmentThesisRow, str(thesis_id))
    if not row:
        raise HTTPException(404, "Thesis not found")
    return thesis_view(row)


@app.post("/api/v1/theses/{thesis_id}/updates", response_model=ThesisView)
async def update_thesis(
    thesis_id: UUID, request: UpdateThesis, session: AsyncSession = Depends(get_session)
):
    try:
        await update_thesis_from_run(thesis_id, request.source_run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.expire_all()
    row = await session.get(InvestmentThesisRow, str(thesis_id))
    return thesis_view(row)


def investigation_view(row: SupplyChainInvestigationRow) -> InvestigationView:
    return InvestigationView(
        id=UUID(row.id), signal_type=row.signal_type, subject=row.subject,
        time_window=row.time_window, question=row.question, language=row.language,
        status=row.status, current_step=row.current_step, progress=row.progress,
        error=row.error,
        tasks=[InvestigationTask.model_validate(item) for item in row.state.get("tasks", [])],
        created_at=row.created_at, updated_at=row.updated_at,
    )


@app.get("/api/v1/supply-chain-investigations", response_model=list[InvestigationView])
async def list_investigations(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(SupplyChainInvestigationRow).order_by(SupplyChainInvestigationRow.created_at.desc()).limit(50))
    return [investigation_view(row) for row in result.scalars()]


@app.post("/api/v1/supply-chain-investigations", response_model=InvestigationView, status_code=202)
async def create_investigation(request: CreateInvestigation, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    stamp = datetime.now(UTC)
    config, _ = await resolved_run_config(session, "supply-chain", request.config.model_dump(exclude_unset=True))
    pipeline_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": config.get("profile_version_id"), "provider": config.get("provider"), "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"), "reasoning_summary": config.get("reasoning_summary"), "prompt_version": config.get("prompt_version"), "budgets": config.get("budgets", {}), "pipeline": config.get("pipeline", [])}}
    row = SupplyChainInvestigationRow(
        id=str(uuid4()), signal_type=request.signal_type, subject=request.subject,
        time_window=request.time_window, question=request.question,
        language=request.language.value, config_json=json.dumps(config, ensure_ascii=False),
        events_json=json.dumps([pipeline_event], ensure_ascii=False),
        created_at=stamp, updated_at=stamp,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    background.add_task(execute_investigation, row.id)
    return investigation_view(row)


async def get_investigation_or_404(session, investigation_id):
    row = await session.get(SupplyChainInvestigationRow, str(investigation_id))
    if not row:
        raise HTTPException(404, "Investigation not found")
    return row


@app.get("/api/v1/supply-chain-investigations/{investigation_id}", response_model=InvestigationView)
async def read_investigation(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    return investigation_view(await get_investigation_or_404(session, investigation_id))


@app.get("/api/v1/supply-chain-investigations/{investigation_id}/graph", response_model=EvidenceGraph)
async def read_investigation_graph(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    if not row.graph_json:
        raise HTTPException(404, "Graph not ready")
    return EvidenceGraph.model_validate_json(row.graph_json)


@app.get("/api/v1/supply-chain-investigations/{investigation_id}/report", response_model=InvestigationReport)
async def read_investigation_report(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    if not row.report_json:
        raise HTTPException(404, "Report not ready")
    return InvestigationReport.model_validate_json(row.report_json)


@app.get("/api/v1/supply-chain-investigations/{investigation_id}/trace", response_model=InvestigationTrace)
async def read_investigation_trace(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    state, settings = row.state, get_settings()
    config = json.loads(row.config_json or "{}")
    return InvestigationTrace(
        investigation_id=investigation_id,
        events=[RunEvent.model_validate(item) for item in row.events],
        agents=[AgentExecution.model_validate(item) for item in state.get("agent_executions", [])],
        model=config.get("model") or settings.openai_model, provider=config.get("provider") or settings.model_provider,
        input_tokens=state.get("input_tokens", 0), output_tokens=state.get("output_tokens", 0),
        estimated_cost_usd=state.get("estimated_cost_usd", 0),
        tool_calls=state.get("tool_calls", 0), duration_ms=state.get("duration_ms", 0),
    )


@app.get("/api/v1/supply-chain-investigations/{investigation_id}/events")
async def investigation_events(investigation_id: UUID, request: Request, after: int = 0, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    from .api_extensions import unified_events

    return await unified_events("supply-chain", investigation_id, request, after, last_event_id)


@app.post("/api/v1/supply-chain-investigations/{investigation_id}/cancel", response_model=InvestigationView)
async def cancel_investigation(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    if row.status not in {"queued", "running"}:
        raise HTTPException(409, "Investigation cannot be cancelled")
    row.cancel_requested = 1
    await session.commit()
    return investigation_view(row)


@app.post("/api/v1/supply-chain-investigations/{investigation_id}/retry", response_model=InvestigationView, status_code=202)
async def retry_investigation(investigation_id: UUID, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    if row.status not in {"awaiting_retry", "failed", "cancelled"}:
        raise HTTPException(409, "Investigation is not retryable")
    row.status, row.error, row.cancel_requested = "queued", None, 0
    await session.commit()
    background.add_task(execute_investigation, row.id)
    return investigation_view(row)


@app.post("/api/v1/supply-chain-investigations/{investigation_id}/evaluate", response_model=EvalResult)
async def evaluate_supply_chain(investigation_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await get_investigation_or_404(session, investigation_id)
    if not row.graph_json or not row.report_json:
        raise HTTPException(409, "Investigation artifacts not ready")
    result = evaluate_investigation(investigation_id, EvidenceGraph.model_validate_json(row.graph_json), InvestigationReport.model_validate_json(row.report_json))
    row.eval_json = result.model_dump_json()
    await session.commit()
    return result


def debate_view(row: ResearchDebateRow) -> DebateView:
    config = row.trace.get("config_snapshot", {})
    ticker = normalize_ticker(row.company)
    return DebateView(
        id=UUID(row.id), topic=row.topic, company=ticker, language=row.language,
        status=row.status, current_round=row.current_round, progress=row.progress,
        source_ids=row.source_ids,
        transcript=row.transcript,
        verdict=DebateVerdict.model_validate_json(row.verdict_json) if row.verdict_json else None,
        events=[RunEvent.model_validate(item) for item in row.debate_events],
        created_at=row.created_at, updated_at=row.updated_at,
        ticker=ticker, company_name=config.get("company_name"), market=config.get("market"),
        universe_version_id=config.get("universe_version_id"),
        profile_version_id=config.get("profile_version_id"),
    )


@app.get("/api/v1/research-debates", response_model=list[DebateView])
async def list_debates(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ResearchDebateRow).order_by(ResearchDebateRow.created_at.desc()).limit(50))
    return [debate_view(row) for row in result.scalars()]


@app.post("/api/v1/research-debates", response_model=DebateView, status_code=202)
async def create_debate(request: CreateDebate, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    source = await get_run(session, request.source_run_id)
    if not source or source.status != "completed" or not source.report_json:
        raise HTTPException(409, "A completed earnings run is required")
    source_ids = [str(request.source_run_id)]
    if request.thesis_id:
        if not await session.get(InvestmentThesisRow, str(request.thesis_id)):
            raise HTTPException(409, "Thesis not found")
        source_ids.append(str(request.thesis_id))
    if request.investigation_id:
        investigation = await session.get(SupplyChainInvestigationRow, str(request.investigation_id))
        if not investigation or investigation.status != "completed":
            raise HTTPException(409, "Completed supply-chain investigation not found")
        source_ids.append(str(request.investigation_id))
    stamp = datetime.now(UTC)
    config, _ = await resolved_run_config(session, "debate")
    source_config = json.loads(source.config_json or "{}")
    for key in ("ticker", "company_name", "market", "universe_version_id", "universe", "universe_as_of", "universe_source_url", "universe_content_hash"):
        if key in source_config:
            config[key] = source_config[key]
    pipeline_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": config.get("profile_version_id"), "provider": config.get("provider"), "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"), "reasoning_summary": config.get("reasoning_summary"), "prompt_version": config.get("prompt_version"), "budgets": config.get("budgets", {}), "universe_version_id": config.get("universe_version_id"), "pipeline": config.get("pipeline", [])}}
    row = ResearchDebateRow(id=str(uuid4()), topic=request.topic, company=source.company, language=request.language.value, source_ids_json=json.dumps(source_ids), events_json=json.dumps([pipeline_event], ensure_ascii=False), created_at=stamp, updated_at=stamp)
    row.trace = {"config_snapshot": config, "profile_version_id": config.get("profile_version_id")}
    session.add(row)
    await session.commit()
    await session.refresh(row)
    background.add_task(execute_debate, row.id, request.rebuttal_rounds)
    return debate_view(row)


@app.get("/api/v1/research-debates/{debate_id}", response_model=DebateView)
async def read_debate(debate_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(ResearchDebateRow, str(debate_id))
    if not row:
        raise HTTPException(404, "Debate not found")
    return debate_view(row)


@app.get("/api/v1/research-debates/{debate_id}/events")
async def debate_events(debate_id: UUID, request: Request, after: int = 0, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    from .api_extensions import unified_events

    return await unified_events("debate", debate_id, request, after, last_event_id)


@app.get("/api/v1/research-debates/{debate_id}/trace", response_model=DebateTrace)
async def debate_trace(debate_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(ResearchDebateRow, str(debate_id))
    if not row:
        raise HTTPException(404, "Debate not found")
    trace = row.trace
    return DebateTrace(debate_id=debate_id, agents=[AgentExecution.model_validate(item) for item in trace.get("agents", [])], events=[RunEvent.model_validate(item) for item in row.debate_events], input_tokens=trace.get("input_tokens", 0), output_tokens=trace.get("output_tokens", 0), tool_calls=trace.get("tool_calls", 0), duration_ms=trace.get("duration_ms", 0), estimated_cost_usd=trace.get("estimated_cost_usd", 0))


@app.post("/api/v1/research-debates/{debate_id}/evaluate", response_model=EvalResult)
async def evaluate_research_debate(debate_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(ResearchDebateRow, str(debate_id))
    if not row or not row.verdict_json:
        raise HTTPException(409, "Debate verdict not ready")
    result = evaluate_debate(debate_id, row.transcript, DebateVerdict.model_validate_json(row.verdict_json))
    row.eval_json = result.model_dump_json()
    await session.commit()
    return result


def project_view(row: AutonomousProjectRow) -> AutonomousProjectView:
    config, state = json.loads(row.config_json), row.state
    budgets = config.get("budgets", {})
    ticker = normalize_ticker(row.company)
    return AutonomousProjectView(id=UUID(row.id), question=row.question, company=ticker, language=row.language, status=row.status, current_step=row.current_step, progress=row.progress, error=row.error, plan=row.project_plan, budget={"max_tool_calls": budgets.get("max_tool_calls", 40), "used_tool_calls": state.get("tool_calls", 0), "max_cost_usd": budgets.get("max_cost_usd", 5), "estimated_cost_usd": state.get("estimated_cost_usd", 0)}, created_at=row.created_at, updated_at=row.updated_at, ticker=ticker, company_name=config.get("company_name"), market=config.get("market"), universe_version_id=config.get("universe_version_id"), profile_version_id=config.get("profile_version_id"))


@app.get("/api/v1/autonomous-projects", response_model=list[AutonomousProjectView])
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(AutonomousProjectRow).order_by(AutonomousProjectRow.created_at.desc()).limit(50))
    return [project_view(row) for row in result.scalars()]


@app.post("/api/v1/autonomous-projects", response_model=AutonomousProjectView, status_code=202)
async def create_project(request: CreateAutonomousProject, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    company = await validated_company_period(session, request.ticker, request.fiscal_period)
    stamp, project_id = datetime.now(UTC), uuid4()
    state = {"fiscal_period": request.fiscal_period, "completed_steps": [], "checkpoints": [], "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0}
    request_config = request.config.model_dump(exclude_unset=True)
    config, _ = await resolved_run_config(session, "autonomous", request_config)
    config.update({
        "max_steps": request_config.get("max_steps", config.get("max_steps", 10)),
        "checkpoint_each_step": request_config.get(
            "checkpoint_each_step", config.get("checkpoint", True)
        ),
    })
    config.update({"ticker": company["ticker"], "company_name": company["name"], "market": company["market"], "universe_version_id": company["universe_version_id"], "universe_as_of": company["universe_as_of"], "universe_source_url": company["source_url"], "universe_content_hash": company["content_hash"]})
    pipeline_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": config.get("profile_version_id"), "provider": config.get("provider"), "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"), "reasoning_summary": config.get("reasoning_summary"), "prompt_version": config.get("prompt_version"), "budgets": config.get("budgets", {}), "universe_version_id": config.get("universe_version_id"), "pipeline": config.get("pipeline", [])}}
    row = AutonomousProjectRow(id=str(project_id), question=request.question, company=company["ticker"], language=request.language.value, config_json=json.dumps(config, ensure_ascii=False), state_json=json.dumps(state), plan_json=json.dumps(__import__("app.autonomous", fromlist=["make_plan"]).make_plan(config)), events_json=json.dumps([pipeline_event], ensure_ascii=False), created_at=stamp, updated_at=stamp)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    background.add_task(execute_project, row.id)
    return project_view(row)


@app.get("/api/v1/autonomous-projects/{project_id}", response_model=AutonomousProjectView)
async def read_project(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row:
        raise HTTPException(404, "Project not found")
    return project_view(row)


@app.get("/api/v1/autonomous-projects/{project_id}/report", response_model=AutonomousReport)
async def read_project_report(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row or not row.report_json:
        raise HTTPException(404, "Project report not ready")
    report = AutonomousReport.model_validate_json(row.report_json)
    report.ticker = normalize_ticker(report.company)
    report.company = report.ticker
    config = json.loads(row.config_json or "{}")
    report.company_name = report.company_name or config.get("company_name")
    report.market = report.market or config.get("market")
    report.universe_version_id = report.universe_version_id or config.get("universe_version_id")
    report.profile_version_id = report.profile_version_id or config.get("profile_version_id")
    return report


@app.get("/api/v1/autonomous-projects/{project_id}/trace", response_model=AutonomousTrace)
async def project_trace(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row:
        raise HTTPException(404, "Project not found")
    state = row.state
    return AutonomousTrace(project_id=project_id, events=[RunEvent.model_validate(item) for item in row.project_events], checkpoints=state.get("checkpoints", []), completed_steps=state.get("completed_steps", []), tool_calls=state.get("tool_calls", 0), input_tokens=state.get("input_tokens", 0), output_tokens=state.get("output_tokens", 0), estimated_cost_usd=state.get("estimated_cost_usd", 0), duration_ms=state.get("duration_ms", 0))


@app.post("/api/v1/autonomous-projects/{project_id}/pause", response_model=AutonomousProjectView)
async def pause_project(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row or row.status not in {"queued", "running"}:
        raise HTTPException(409, "Project is not running")
    row.pause_requested = 1
    await session.commit()
    return project_view(row)


@app.post("/api/v1/autonomous-projects/{project_id}/resume", response_model=AutonomousProjectView, status_code=202)
async def resume_project(project_id: UUID, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row or row.status not in {"paused", "awaiting_retry", "failed"}:
        raise HTTPException(409, "Project is not resumable")
    row.pause_requested, row.status, row.error = 0, "queued", None
    await session.commit()
    background.add_task(execute_project, row.id)
    return project_view(row)


@app.post("/api/v1/autonomous-projects/{project_id}/cancel", response_model=AutonomousProjectView)
async def cancel_project(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row or row.status not in {"queued", "running", "paused"}:
        raise HTTPException(409, "Project cannot be cancelled")
    row.cancel_requested = 1
    if row.status == "paused":
        row.status = "cancelled"
    await session.commit()
    return project_view(row)


@app.post("/api/v1/autonomous-projects/{project_id}/evaluate", response_model=EvalResult)
async def evaluate_autonomous_project(project_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(AutonomousProjectRow, str(project_id))
    if not row or not row.report_json:
        raise HTTPException(409, "Project report not ready")
    result = evaluate_project(project_id, AutonomousReport.model_validate_json(row.report_json), row.state)
    row.eval_json = result.model_dump_json()
    await session.commit()
    return result


def arena_view(row: EvaluationArenaRow) -> ArenaView:
    config = json.loads(row.config_json or "{}")
    return ArenaView(id=UUID(row.id), name=row.name, dataset=row.dataset, status=row.status, progress=row.progress, results=row.arena_results, winner=ArenaWinner.model_validate_json(row.winner_json) if row.winner_json else None, events=[RunEvent.model_validate(item) for item in row.arena_events], created_at=row.created_at, updated_at=row.updated_at, ticker=config.get("ticker"), company_name=config.get("company_name"), market=config.get("market"), universe_version_id=config.get("universe_version_id"), profile_version_id=config.get("profile_version_id"))


@app.get("/api/v1/evaluation-arenas", response_model=list[ArenaView])
async def list_arenas(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(EvaluationArenaRow).order_by(EvaluationArenaRow.created_at.desc()).limit(50))
    return [arena_view(row) for row in result.scalars()]


@app.post("/api/v1/evaluation-arenas", response_model=ArenaView, status_code=202)
async def create_arena(request: CreateArena, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    company = await validated_company_period(session, request.ticker, request.fiscal_period)
    stamp = datetime.now(UTC)
    config, _ = await resolved_run_config(session, "arena")
    evidence_config, _ = await resolved_run_config(session, "earnings")
    universe_config = {"company": company["ticker"], "ticker": company["ticker"], "company_name": company["name"], "market": company["market"], "universe_version_id": company["universe_version_id"], "universe": company["universe"], "universe_as_of": company["universe_as_of"], "universe_source_url": company["source_url"], "universe_content_hash": company["content_hash"]}
    evidence_config.update(universe_config)
    evidence_run_id = str(uuid4())
    evidence_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved for Arena evidence", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": evidence_config.get("profile_version_id"), "provider": evidence_config.get("provider"), "model": evidence_config.get("model"), "reasoning_effort": evidence_config.get("reasoning_effort"), "reasoning_summary": evidence_config.get("reasoning_summary"), "prompt_version": evidence_config.get("prompt_version"), "budgets": evidence_config.get("budgets", {}), "universe_version_id": evidence_config.get("universe_version_id"), "pipeline": evidence_config.get("pipeline", [])}}
    evidence_run = ResearchRunRow(id=evidence_run_id, company=company["ticker"], fiscal_period=request.fiscal_period, output_language="en", config_json=json.dumps(evidence_config, ensure_ascii=False), events_json=json.dumps([evidence_event], ensure_ascii=False), created_at=stamp, updated_at=stamp)
    config.update({"company": company["ticker"], "ticker": company["ticker"], "company_name": company["name"], "market": company["market"], "universe_version_id": company["universe_version_id"], "universe_as_of": company["universe_as_of"], "universe_source_url": company["source_url"], "universe_content_hash": company["content_hash"], "fiscal_period": request.fiscal_period, "variants": [item.model_dump(mode="json") for item in request.variants]})
    config["source_run_id"] = evidence_run_id
    dataset_ticker = {"NVDA": "nvidia", "2330.TW": "tsmc"}.get(company["ticker"], company["ticker"])
    pipeline_event = {"sequence": 1, "kind": "pipeline.snapshot", "step": "orchestrator", "message": "Immutable Agent pipeline resolved", "timestamp": stamp.isoformat(), "payload": {"profile_version_id": config.get("profile_version_id"), "provider": config.get("provider"), "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"), "reasoning_summary": config.get("reasoning_summary"), "prompt_version": config.get("prompt_version"), "budgets": config.get("budgets", {}), "universe_version_id": config.get("universe_version_id"), "pipeline": config.get("pipeline", [])}}
    row = EvaluationArenaRow(id=str(uuid4()), name=request.name, dataset=f"{dataset_ticker}-{request.fiscal_period}", config_json=json.dumps(config, ensure_ascii=False), events_json=json.dumps([pipeline_event], ensure_ascii=False), created_at=stamp, updated_at=stamp)
    session.add_all([evidence_run, row])
    await session.commit()
    await session.refresh(row)
    background.add_task(execute_run, evidence_run.id)
    background.add_task(execute_arena, row.id)
    return arena_view(row)


@app.get("/api/v1/evaluation-arenas/{arena_id}", response_model=ArenaView)
async def read_arena(arena_id: UUID, session: AsyncSession = Depends(get_session)):
    row = await session.get(EvaluationArenaRow, str(arena_id))
    if not row:
        raise HTTPException(404, "Evaluation arena not found")
    return arena_view(row)
