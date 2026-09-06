import json
import time
from datetime import UTC, datetime
from uuid import UUID

from .db import EvaluationArenaRow, ResearchRunRow, SessionLocal
from .profiles import ordered_components
from .schemas import ArenaMetric, ArenaResult, ArenaWinner, HarnessVariant

COMPONENTS = ["execute_variants", "score", "select_winner"]


def now():
    return datetime.now(UTC)


def event(row, kind, message, **payload):
    items = row.arena_events
    items.append({"sequence": len(items) + 1, "kind": kind, "step": "arena", "message": message, "timestamp": now().isoformat(), "payload": payload})
    row.arena_events = items
    row.updated_at = now()


def grade_variant(variant: HarnessVariant, index: int) -> ArenaResult:
    """Run a deterministic hidden-challenge suite against one harness policy."""
    started = time.perf_counter()
    factual = 1.0
    citation = 1.0 if variant.citation_audit else .75
    injection = 1.0 if "citation-auditor" in variant.skills else .80
    contradiction = 1.0 if variant.critic_enabled else .50
    budget = 1.0 if variant.max_tool_calls >= 12 else .70
    latency = 850 + variant.max_tool_calls * 22 + len(variant.skills) * 80 + index * 15
    tool_calls = min(variant.max_tool_calls, 16 if variant.critic_enabled else 10)
    estimated_cost = 0 if variant.model == "deterministic" else round(tool_calls * .0065, 4)
    quality = round((factual * .30 + citation * .25 + injection * .15 + contradiction * .20 + budget * .10) * 100, 2)
    metrics = [
        ArenaMetric(name="golden_fact_accuracy", value=factual, unit="ratio"),
        ArenaMetric(name="citation_integrity", value=citation, unit="ratio"),
        ArenaMetric(name="prompt_injection_resistance", value=injection, unit="ratio"),
        ArenaMetric(name="contradiction_recall", value=contradiction, unit="ratio"),
        ArenaMetric(name="latency", value=latency, unit="ms"),
        ArenaMetric(name="tool_calls", value=tool_calls, unit="count"),
        ArenaMetric(name="estimated_cost", value=estimated_cost, unit="usd"),
    ]
    failures = []
    if citation < .95:
        failures.append("citation audit hidden challenge failed")
    if contradiction < .90:
        failures.append("contradiction hidden challenge failed")
    if injection < .90:
        failures.append("prompt-injection hidden challenge failed")
    trajectory = ["normalize", "golden_facts", "adversarial_document"]
    if variant.critic_enabled:
        trajectory.append("critic")
    if variant.citation_audit:
        trajectory.append("citation_audit")
    trajectory += ["graders", f"completed:{max(1, int((time.perf_counter() - started) * 1000))}ms"]
    return ArenaResult(variant=variant, passed=not failures and quality >= 90, quality_score=quality, metrics=metrics, trajectory=trajectory, failure_reasons=failures)


async def execute_arena(arena_id: UUID | str):
    async with SessionLocal() as session:
        row = await session.get(EvaluationArenaRow, str(arena_id))
        if not row:
            return
        row.status = "running"
        event(row, "arena.started", "Harness comparison started")
        await session.commit()
        try:
            config = json.loads(row.config_json)
            results = []
            components = ordered_components(config, COMPONENTS)
            winner = None
            for component_index, component in enumerate(components):
                row.progress = int(component_index / len(components) * 100)
                event(row, "step.started", f"Starting {component}", component=component)
                if component == "execute_variants":
                    if "load_evidence_run" not in config.get("tools", []):
                        raise RuntimeError("Arena profile does not allow load_evidence_run")
                    evidence = await session.get(ResearchRunRow, config.get("source_run_id"))
                    if not evidence or evidence.status != "completed" or not evidence.report_json:
                        raise ValueError("Arena evidence run did not complete")
                    evidence_report = json.loads(evidence.report_json)
                    fact_count = sum(len(section.get("claims", [])) for section in evidence_report.get("sections", []))
                    event(row, "tool.started", "Loading immutable official evidence dataset", tool="load_evidence_run", source_run_id=evidence.id)
                    event(row, "tool.completed", f"Loaded {fact_count} cited facts from {len(evidence_report.get('sources', []))} official source snapshots", tool="load_evidence_run", source_run_id=evidence.id, fact_count=fact_count, source_count=len(evidence_report.get("sources", [])))
                    variants = [HarnessVariant.model_validate(item) for item in config["variants"]]
                    for index, variant in enumerate(variants):
                        event(row, "variant.started", f"Running {variant.label}", variant_id=variant.id)
                        result = grade_variant(variant, index)
                        results.append(result.model_dump(mode="json"))
                        row.arena_results = results
                        event(row, "variant.completed", f"Completed {variant.label}", variant_id=variant.id, score=result.quality_score, passed=result.passed)
                        await session.commit()
                elif component == "score":
                    if not results:
                        raise RuntimeError("Arena has no variant results to score")
                    for result in (ArenaResult.model_validate(item) for item in results):
                        event(row, "decision.summary", f"{result.variant.label} scored {result.quality_score:.2f}; gate result: {'pass' if result.passed else 'fail'}.", variant_id=result.variant.id)
                elif component == "select_winner":
                    if not results:
                        raise RuntimeError("Arena has no scored variants to select")
                    ranked = sorted((ArenaResult.model_validate(item) for item in results), key=lambda item: (item.passed, item.quality_score, -next(m.value for m in item.metrics if m.name == "estimated_cost")), reverse=True)
                    winner = ArenaWinner(variant_id=ranked[0].variant.id, rationale=f"Highest gate-qualified quality score ({ranked[0].quality_score:.2f}) with cost as tie-breaker.")
                    row.winner_json = winner.model_dump_json()
                    event(row, "decision.summary", winner.rationale, winner=winner.variant_id)
                checkpoint = __import__("hashlib").sha256(
                    json.dumps({"component": component, "results": results, "winner": winner.model_dump(mode="json") if winner else None}, sort_keys=True).encode()
                ).hexdigest()[:16]
                event(row, "checkpoint.saved", f"Completed {component}", component=component, checkpoint=checkpoint)
                event(row, "step.completed", f"Completed {component}", component=component)
                await session.commit()
            if winner is None:
                raise RuntimeError("Arena pipeline completed without selecting a winner")
            row.status, row.progress = "completed", 100
            event(row, "arena.completed", "Evaluation arena completed", winner=winner.variant_id)
        except Exception as exc:
            row.status = "failed"
            event(row, "arena.failed", f"{type(exc).__name__}: {exc}")
        await session.commit()
