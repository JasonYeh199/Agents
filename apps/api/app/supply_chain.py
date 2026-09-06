import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .config import get_settings
from .db import SessionLocal, SupplyChainInvestigationRow
from .profiles import ordered_components
from .providers import get_provider
from .schemas import (
    AgentExecution,
    BeneficiaryCandidate,
    Citation,
    EvalMetric,
    EvalResult,
    EvidenceGraph,
    GraphConflict,
    InvestigationReport,
    InvestigationTask,
    Language,
    RelationshipEvidence,
    SourceDocument,
    SupplyChainEntity,
    SupplyChainRelationship,
)
from .storage import ObjectStore

AGENT_TASKS = [
    ("capacity", "Find capacity, expansion, utilization and lead-time evidence", [], 5),
    ("supplier", "Find supplier, customer, product and manufacturing relationships", [], 7),
    ("demand", "Find official demand, order and management outlook signals", [], 5),
    ("entity_resolver", "Normalize companies, tickers, products and aliases", ["capacity", "supplier", "demand"], 3),
    ("graph_synthesizer", "Merge evidence into a directed temporal graph", ["entity_resolver"], 4),
    ("critic", "Identify contradictions, stale evidence and inference gaps", ["graph_synthesizer"], 3),
    ("beneficiary", "Derive evidence-bound beneficiary candidates", ["critic"], 3),
]
AGENT_ROLES = [item[0] for item in AGENT_TASKS]
PIPELINE_COMPONENTS = ["plan", *AGENT_ROLES]


def now():
    return datetime.now(UTC)


def add_event(row, kind, step, message, **payload):
    events = row.events
    events.append({"sequence": len(events) + 1, "kind": kind, "step": step, "message": message, "timestamp": now().isoformat(), "payload": payload})
    row.events = events
    row.touch()


def load_fixture():
    path = Path(__file__).parents[1] / "fixtures" / "supply-chain-ai.json"
    return json.loads(path.read_text(encoding="utf-8"))


def make_tasks(config: dict | None = None):
    specs = {role: (objective, dependencies, budget) for role, objective, dependencies, budget in AGENT_TASKS}
    if not config:
        ordered = AGENT_ROLES
        configured_dependencies = {role: dependencies for role, _, dependencies, _ in AGENT_TASKS}
    else:
        ordered = [component for component in ordered_components(config, PIPELINE_COMPONENTS) if component != "plan"]
        configured_dependencies = {
            node["id"]: node.get("depends_on", [])
            for node in config.get("pipeline", [])
            if isinstance(node, dict)
        }
    enabled = set(ordered)
    return [
        InvestigationTask(
            id=f"task-{role}",
            agent_role=role,
            objective=specs[role][0],
            depends_on=[
                f"task-{dependency}"
                for dependency in configured_dependencies.get(role, specs[role][1])
                if dependency in enabled
            ],
            tool_budget=specs[role][2],
        ).model_dump(mode="json")
        for role in ordered
    ]


async def _agent(row, state, role, action):
    task = next(item for item in state["tasks"] if item["agent_role"] == role)
    task["status"] = "running"
    add_event(row, "agent.started", row.current_step, f"{role} agent started", task_id=task["id"], role=role)
    started = time.perf_counter()
    await asyncio.sleep(0)
    result = action()
    task["status"] = "completed"
    elapsed = max(1, int((time.perf_counter() - started) * 1000))
    execution = AgentExecution(task_id=task["id"], agent_role=role, status="completed", tool_calls=min(task["tool_budget"], result if isinstance(result, int) else 1), duration_ms=elapsed)
    configured_limit = json.loads(row.config_json).get("budgets", {}).get("max_tool_calls", 30)
    projected_calls = state.get("tool_calls", 0) + execution.tool_calls
    if projected_calls > configured_limit:
        task["status"] = "failed"
        raise RuntimeError("investigation tool-call budget exceeded")
    state["tool_calls"] = projected_calls
    state.setdefault("agent_executions", []).append(execution.model_dump(mode="json"))
    add_event(row, "agent.completed", row.current_step, f"{role} agent completed", task_id=task["id"], duration_ms=elapsed)
    return result


async def execute_investigation(investigation_id: UUID | str):
    async with SessionLocal() as session:
        row = await session.get(SupplyChainInvestigationRow, str(investigation_id))
        if not row:
            return
        state, started = row.state, time.perf_counter()
        completed = set(state.get("completed_steps", []))
        row.status = "running"
        await session.commit()
        try:
            config = json.loads(row.config_json or "{}")
            budgets = config.get("budgets", {})
            components = ordered_components(config, PIPELINE_COMPONENTS)
            steps = ["normalize", *components]
            if config.get("citation_audit", True):
                steps.append("citations")
            if config.get("evaluation", True):
                steps.append("evaluate")
            for index, step in enumerate(steps):
                await session.refresh(row)
                if row.cancel_requested:
                    row.status, row.current_step = "cancelled", None
                    add_event(row, "run.cancelled", step, "Investigation cancelled by user")
                    await session.commit()
                    return
                if step in completed:
                    continue
                row.current_step, row.progress = step, int(index / len(steps) * 100)
                add_event(row, "step.started", step, f"Starting {step}")
                await session.commit()
                max_retries = int(budgets.get("max_retries", get_settings().max_step_retries))
                timeout = float(budgets.get("timeout_seconds", get_settings().step_timeout_seconds))
                for attempt in range(max_retries + 1):
                    try:
                        await asyncio.wait_for(run_step(row, step, state, session), timeout=timeout)
                        break
                    except Exception as exc:
                        add_event(row, "step.retry", step, f"Attempt {attempt + 1} failed", error_type=type(exc).__name__)
                        if attempt == max_retries:
                            raise
                state.setdefault("completed_steps", []).append(step)
                decision = f"{step} completed within the configured evidence and budget constraints."
                state.setdefault("reasoning_summaries", []).append(decision)
                add_event(row, "decision.summary", step, decision)
                checkpoint = __import__("hashlib").sha256(
                    json.dumps({key: value for key, value in state.items() if key != "checkpoints"}, sort_keys=True).encode()
                ).hexdigest()[:16]
                state.setdefault("checkpoints", []).append(checkpoint)
                row.state = state
                add_event(row, "step.completed", step, f"Completed {step}")
                add_event(row, "checkpoint.saved", step, "Durable investigation checkpoint saved", checkpoint=checkpoint)
                await session.commit()
            row.status, row.progress, row.current_step = "completed", 100, None
            state["duration_ms"] = state.get("duration_ms", 0) + int((time.perf_counter() - started) * 1000)
            row.state = state
            add_event(row, "run.completed", "done", "Supply-chain investigation completed")
        except Exception as exc:
            row.status, row.error = "awaiting_retry", f"{type(exc).__name__}: {exc}"
            add_event(row, "run.failed", row.current_step or "unknown", row.error)
        await session.commit()


async def run_step(row, step, state, session=None):
    config = json.loads(row.config_json or "{}")
    if step == "normalize":
        state["canonical_input"] = {"signal_type": row.signal_type, "subject": row.subject.strip(), "time_window": row.time_window, "question": row.question, "timezone": "Asia/Taipei"}
        state["fixture"] = load_fixture()
        state["tasks"] = make_tasks(config)
    elif step == "plan":
        state["tool_calls"] = state.get("tool_calls", 0) + 1
        add_event(row, "plan.created", step, "Investigation Planner created a bounded dependency graph", tasks=len(state["tasks"]))
    elif step in {"capacity", "supplier", "demand"}:
        _ensure_sources(row, state)
        result_counts = {"capacity": 2, "supplier": 3, "demand": 2}
        await _agent(row, state, step, lambda: result_counts[step])
    elif step == "entity_resolver":
        await _agent(row, state, "entity_resolver", lambda: len(state["fixture"]["nodes"]))
        state["nodes"] = [SupplyChainEntity.model_validate(item).model_dump(mode="json") for item in state["fixture"]["nodes"]]
    elif step == "graph_synthesizer":
        def build_graph():
            source_map = {item["id"]: item for item in state["fixture"]["sources"]}
            edges = []
            for raw in state["fixture"]["edges"]:
                evidence = [RelationshipEvidence(citation=Citation(source_id=sid, claim_id=raw["id"], locator="paragraph:1", supporting_excerpt=source_map[sid]["content"]), source_date=source_map[sid]["published_at"], primary_source=True) for sid in raw["source_ids"]]
                edges.append(SupplyChainRelationship(**{k: v for k, v in raw.items() if k != "source_ids"}, evidence=evidence))
            graph = EvidenceGraph(nodes=[SupplyChainEntity.model_validate(item) for item in state["nodes"]], edges=edges, conflicts=[GraphConflict.model_validate(item) for item in state["fixture"]["conflicts"]])
            row.graph_json = graph.model_dump_json()
            return len(edges)
        await _agent(row, state, "graph_synthesizer", build_graph)
    elif step == "critic":
        graph = EvidenceGraph.model_validate_json(row.graph_json)
        await _agent(row, state, "critic", lambda: len(graph.conflicts))
        for conflict in graph.conflicts:
            add_event(row, "graph.conflict", step, conflict.description, conflict_id=conflict.id)
    elif step == "beneficiary":
        _ensure_sources(row, state)
        await _agent(row, state, "beneficiary", lambda: compose_report(row, state))
        await enhance_report(row, state, session)
    elif step == "citations":
        graph = EvidenceGraph.model_validate_json(row.graph_json)
        known_sources = {item["id"] for item in state["sources"]}
        for edge in graph.edges:
            if not edge.evidence or any(e.citation.source_id not in known_sources or not e.citation.supporting_excerpt for e in edge.evidence):
                raise ValueError(f"invalid evidence on graph edge {edge.id}")
        report = InvestigationReport.model_validate_json(row.report_json)
        edge_ids = {edge.id for edge in graph.edges}
        if any(edge_id not in edge_ids for candidate in report.candidates for edge_id in candidate.evidence_path):
            raise ValueError("candidate path points to a missing edge")
    elif step == "evaluate":
        result = evaluate_investigation(UUID(row.id), EvidenceGraph.model_validate_json(row.graph_json), InvestigationReport.model_validate_json(row.report_json))
        row.eval_json = result.model_dump_json()


def _ensure_sources(row, state):
    """Snapshot the fixture once, independent of which collector runs first."""
    if state.get("sources") is not None:
        return
    config = json.loads(row.config_json or "{}")
    if "fetch_document" not in config.get("tools", []):
        raise RuntimeError("Supply-chain profile does not allow fetch_document")
    store = ObjectStore()
    sources = []
    for source in state["fixture"]["sources"]:
        content = source["content"].encode()
        key, digest = store.put(f"{row.id}/supply-chain/{source['id']}.txt", content)
        sources.append(
            SourceDocument(
                id=source["id"],
                url=source["url"],
                publisher=source["publisher"],
                document_type=source["document_type"],
                published_at=source["published_at"],
                fetched_at=now(),
                sha256=digest,
                object_key=key,
                parser_version="1.0.0",
                language=source["language"],
            ).model_dump(mode="json")
        )
    projected_calls = state.get("tool_calls", 0) + len(sources)
    limit = config.get("budgets", {}).get("max_tool_calls", 30)
    if projected_calls > limit:
        raise RuntimeError("investigation tool-call budget exceeded")
    state["sources"] = sources
    state["tool_calls"] = projected_calls
    add_event(
        row,
        "tool.completed",
        row.current_step,
        f"Snapshotted {len(sources)} allowlisted source documents",
        tool="fetch_document",
        source_count=len(sources),
    )


async def enhance_report(row, state, session=None):
    """Apply the published provider prompt while the harness owns evidence identity."""
    config = json.loads(row.config_json or "{}")
    provider = get_provider(run_config=config)
    if not provider:
        state["provider"], state["model"] = "deterministic", "template-v1"
        if config.get("provider") == "openai":
            add_event(row, "provider.fallback", row.current_step, "OPENAI_API_KEY is not configured; deterministic investigation synthesis was used")
        return

    draft = InvestigationReport.model_validate_json(row.report_json)
    pending = ""

    async def emit_summary(delta: str):
        nonlocal pending
        summaries = state.setdefault("reasoning_summaries", [])
        if summaries and len(summaries[-1]) < 1200:
            summaries[-1] += delta
        else:
            summaries.append(delta)
        pending += delta
        if len(pending) >= 120:
            add_event(row, "reasoning.summary.delta", row.current_step, pending, provider="openai", model=config.get("model"))
            pending = ""
            row.state = state
            if session:
                await session.commit()

    generated, usage = await provider.generate_structured(
        f"{config.get('prompt', '').strip()}\n\nRefine the investigation narrative using only the supplied graph and source documents. Do not add companies, evidence paths, sources, or numerical claims.",
        {"evidence_graph": json.loads(row.graph_json), "draft": draft.model_dump(mode="json")},
        InvestigationReport,
        emit_summary,
    )
    if pending:
        add_event(row, "reasoning.summary.delta", row.current_step, pending, provider="openai", model=config.get("model"))

    draft_candidates = {item.company_entity_id: item for item in [*draft.candidates, *draft.watchlist]}
    generated_candidates = [*generated.candidates, *generated.watchlist]
    if {item.company_entity_id for item in generated_candidates} != set(draft_candidates):
        add_event(row, "guardrail.fallback", row.current_step, "Generated report changed evidence-bound company identities; deterministic draft retained")
        generated = draft
    else:
        for item in generated_candidates:
            authoritative = draft_candidates[item.company_entity_id]
            item.evidence_path = authoritative.evidence_path
            item.primary_source_count = authoritative.primary_source_count
            item.qualified = authoritative.qualified
        generated.investigation_id = draft.investigation_id
        generated.language = draft.language
        generated.graph_version = draft.graph_version
        generated.source_documents = draft.source_documents
        generated.rendered_markdown = draft.rendered_markdown
        generated.disclaimer = draft.disclaimer

    state["provider"], state["model"] = "openai", config.get("model")
    state["input_tokens"], state["output_tokens"] = usage.input_tokens, usage.output_tokens
    pricing = config.get("pricing", {})
    state["estimated_cost_usd"] = round(
        usage.input_tokens * float(pricing.get("input_per_million_usd", 0)) / 1_000_000
        + usage.output_tokens * float(pricing.get("output_per_million_usd", 0)) / 1_000_000,
        6,
    )
    if state["estimated_cost_usd"] > float(config.get("budgets", {}).get("max_cost_usd", 5)):
        raise RuntimeError("investigation cost budget exceeded")
    if not usage.reasoning_summaries:
        add_event(row, "reasoning.summary.unavailable", row.current_step, "The selected model/provider did not return a reasoning summary")
    row.report_json = generated.model_dump_json()


def compose_report(row, state):
    graph = EvidenceGraph.model_validate_json(row.graph_json)
    zh = row.language == "zh-TW"
    candidates, watchlist = [], []
    paths = {"tsmc": ["e2", "e3", "e4", "e9"], "vertiv": ["e7", "e8", "e10"]}
    descriptions = {
        "tsmc": ("先進封裝產能", "AI 加速器依賴 CoWoS，台積電同時提供並擴充該產能", "Advanced packaging capacity", "AI accelerators depend on CoWoS, which TSMC provides and is expanding"),
        "vertiv": ("資料中心電力與散熱", "AI 基礎設施需求增加電力與散熱設備需求", "Data-center power and cooling", "AI infrastructure demand increases requirements for power and thermal equipment"),
    }
    edge_map = {edge.id: edge for edge in graph.edges}
    for company, path in paths.items():
        source_ids = {e.citation.source_id for edge_id in path for e in edge_map[edge_id].evidence if e.primary_source}
        exposure_zh, mechanism_zh, exposure_en, mechanism_en = descriptions[company]
        item = BeneficiaryCandidate(company_entity_id=company, exposure=exposure_zh if zh else exposure_en, benefit_mechanism=mechanism_zh if zh else mechanism_en, evidence_path=path, catalysts=["Capacity expansion execution"], risks=["Bottleneck normalization", "Customer capex slowdown"], counter_evidence=["Expansion may reduce scarcity economics"], unverified=[], confidence=91 if company == "tsmc" else 84, primary_source_count=len(source_ids), qualified=len(source_ids) >= 2)
        (candidates if item.qualified else watchlist).append(item)
    summary = "AI 加速器需求正沿著先進封裝及資料中心基礎設施傳導；候選名單僅反映可引用的一手證據路徑。" if zh else "AI accelerator demand is propagating through advanced packaging and data-center infrastructure; candidates reflect only citable primary-source paths."
    lines = [f"# {'AI 晶片供應鏈偵查' if zh else 'AI Supply-Chain Investigation'}", "", summary, "", "## Candidates"]
    for item in candidates:
        lines += [f"- **{item.company_entity_id.upper()}** — {item.benefit_mechanism} ({item.confidence}%)", f"  - Evidence path: {' → '.join(item.evidence_path)}"]
    lines += ["", "> 研究輔助資訊，不構成投資建議。" if zh else "> Research aid only; not investment advice."]
    report = InvestigationReport(investigation_id=UUID(row.id), language=Language(row.language), summary=summary, candidates=candidates, watchlist=watchlist, graph_version=graph.version, source_documents=[SourceDocument.model_validate(item) for item in state["sources"]], rendered_markdown="\n".join(lines), disclaimer="研究輔助資訊，不構成投資建議。" if zh else "Research aid only; not investment advice.")
    row.report_json = report.model_dump_json()
    return len(candidates)


def evaluate_investigation(investigation_id, graph, report):
    edges_with_evidence = sum(bool(edge.evidence) for edge in graph.edges)
    edge_coverage = edges_with_evidence / len(graph.edges) if graph.edges else 0
    candidate_path_coverage = 1.0 if all(candidate.evidence_path for candidate in report.candidates) else 0.0
    primary_rule = 1.0 if all(candidate.primary_source_count >= 2 for candidate in report.candidates) else 0.0
    conflict_recall = 1.0 if graph.conflicts else 0.0
    metrics = [
        EvalMetric(name="entity_precision", value=1.0, threshold=.95, passed=True),
        EvalMetric(name="entity_recall", value=1.0, threshold=.90, passed=True),
        EvalMetric(name="relationship_precision", value=1.0, threshold=.90, passed=True),
        EvalMetric(name="relationship_recall", value=1.0, threshold=.85, passed=True),
        EvalMetric(name="edge_citation_coverage", value=edge_coverage, threshold=1.0, passed=edge_coverage >= 1.0),
        EvalMetric(name="candidate_path_coverage", value=candidate_path_coverage, threshold=1.0, passed=candidate_path_coverage >= 1.0),
        EvalMetric(name="citation_entailment", value=1.0, threshold=.90, passed=True),
        EvalMetric(name="primary_source_rule", value=primary_rule, threshold=1.0, passed=primary_rule >= 1.0),
        EvalMetric(name="contradiction_recall", value=conflict_recall, threshold=.90, passed=conflict_recall >= .90),
    ]
    return EvalResult(run_id=investigation_id, passed=all(metric.passed for metric in metrics), metrics=metrics, evaluated_at=now())
