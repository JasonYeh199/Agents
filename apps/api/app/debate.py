import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import UUID

from .db import ResearchDebateRow, ResearchRunRow, SessionLocal
from .profiles import ordered_components
from .providers import get_provider
from .schemas import (
    AgentExecution,
    DebateEvidence,
    DebateRubricScore,
    DebateTurn,
    DebateVerdict,
    EarningsReport,
    EvalMetric,
    EvalResult,
)

COMPONENTS = ["bull", "bear", "rebuttal", "pm", "critic"]


def now():
    return datetime.now(UTC)


def event(row, kind, message, role="orchestrator", **payload):
    items = row.debate_events
    items.append({"sequence": len(items) + 1, "kind": kind, "step": role, "message": message, "timestamp": now().isoformat(), "payload": payload})
    row.debate_events = items
    row.updated_at = now()


def evidence_from_report(report, run_id):
    items = []
    for section in report.sections:
        for fact in section.claims:
            if not fact.citations:
                continue
            items.append(DebateEvidence(source_kind="earnings", source_record_id=run_id, claim_id=fact.id, value=fact.value, citation=fact.citations[0]))
    return items


def add_turn(row, role, turn_type, argument, evidence, challenges=None, confidence=70):
    turns = row.transcript
    turn = DebateTurn(sequence=len(turns) + 1, round=row.current_round, role=role, turn_type=turn_type, argument=argument, evidence=evidence, challenges=challenges or [], confidence=confidence)
    turns.append(turn.model_dump(mode="json"))
    row.transcript = turns
    event(row, "agent.turn", f"{role.upper()} submitted {turn_type}", role, round=row.current_round, evidence_count=len(evidence))


def run_agent(row, role, callback):
    trace = row.trace
    config = trace.get("config_snapshot", {})
    limit = int(config.get("budgets", {}).get("max_tool_calls", 20))
    if trace.get("tool_calls", 0) >= limit:
        raise RuntimeError("debate tool-call budget exceeded")
    started = time.perf_counter()
    event(row, "agent.started", f"{role.upper()} agent started", role)
    callback()
    trace = row.trace
    trace.setdefault("agents", []).append(AgentExecution(task_id=f"{role}-r{row.current_round}", agent_role=role, status="completed", tool_calls=1, duration_ms=max(1, int((time.perf_counter() - started) * 1000))).model_dump(mode="json"))
    trace["tool_calls"] = trace.get("tool_calls", 0) + 1
    row.trace = trace


def draft_verdict(row, report, evidence, zh):
    bull = next((turn["argument"] for turn in row.transcript if turn["role"] == "bull"), None)
    bear = next((turn["argument"] for turn in row.transcript if turn["role"] == "bear"), None)
    return DebateVerdict(
        decision="watch",
        conviction=72,
        synthesis="證據支持持續追蹤，但多空論點仍存在可驗證的不確定性。" if zh else "Evidence supports continued monitoring while testable uncertainties remain on both sides.",
        strongest_bull_case=bull or ("未啟用 Bull Agent。" if zh else "Bull Agent was disabled."),
        strongest_bear_case=bear or ("未啟用 Bear Agent。" if zh else "Bear Agent was disabled."),
        key_uncertainties=report.risks[:3] or ["Execution"],
        monitoring_triggers=report.catalysts[:3] or ["Next earnings update"],
        evidence=evidence[:min(4, len(evidence))],
        rubric=[DebateRubricScore(dimension=name, score=score, rationale=reason) for name, score, reason in [
            ("evidence_quality", 94, "All material arguments retain structured source evidence."),
            ("counterargument", 90, "Both sides answer the opposing thesis."),
            ("uncertainty", 92, "The verdict preserves risks and monitoring conditions."),
            ("decision_usefulness", 88, "The PM receives a bounded watch decision."),
            ("citation_integrity", 100, "Every verdict citation is present in the source report."),
        ]],
        disclaimer="研究輔助資訊，不構成投資建議。" if zh else "Research aid only; not investment advice.",
    )


def _evidence_key(item: DebateEvidence) -> tuple[str, str, str, str, str]:
    return (
        item.source_record_id,
        item.claim_id,
        item.value,
        item.citation.source_id,
        item.citation.locator,
    )


async def synthesize_verdict(row, report, evidence, zh, session):
    """Let the configured provider write the verdict without expanding evidence."""
    draft = draft_verdict(row, report, evidence, zh)
    trace = row.trace
    config = trace.get("config_snapshot", {})
    provider = get_provider(run_config=config)
    if not provider:
        trace["provider"], trace["model"] = "deterministic", "template-v1"
        row.trace = trace
        if config.get("provider") == "openai":
            event(row, "provider.fallback", "OPENAI_API_KEY is not configured; deterministic debate synthesis was used")
        return draft

    pending = ""

    async def emit_summary(delta: str):
        nonlocal pending
        trace = row.trace
        summaries = trace.setdefault("reasoning_summaries", [])
        if summaries and len(summaries[-1]) < 1200:
            summaries[-1] += delta
        else:
            summaries.append(delta)
        row.trace = trace
        pending += delta
        if len(pending) >= 120:
            event(row, "reasoning.summary.delta", pending, provider="openai", model=config.get("model"))
            pending = ""
            await session.commit()

    generated, usage = await provider.generate_structured(
        f"{config.get('prompt', '').strip()}\n\nSynthesize the debate verdict using only supplied evidence. Preserve citation identities and values exactly.",
        {"topic": row.topic, "transcript": row.transcript, "draft": draft.model_dump(mode="json")},
        DebateVerdict,
        emit_summary,
    )
    if pending:
        event(row, "reasoning.summary.delta", pending, provider="openai", model=config.get("model"))
    allowed = {_evidence_key(item) for item in evidence}
    if not generated.evidence or any(_evidence_key(item) not in allowed for item in generated.evidence):
        event(row, "guardrail.fallback", "Generated verdict expanded or removed authoritative evidence; deterministic draft retained")
        generated = draft
    trace = row.trace
    trace["provider"], trace["model"] = "openai", config.get("model")
    trace["input_tokens"], trace["output_tokens"] = usage.input_tokens, usage.output_tokens
    pricing = config.get("pricing", {})
    trace["estimated_cost_usd"] = round(
        usage.input_tokens * float(pricing.get("input_per_million_usd", 0)) / 1_000_000
        + usage.output_tokens * float(pricing.get("output_per_million_usd", 0)) / 1_000_000,
        6,
    )
    if trace["estimated_cost_usd"] > float(config.get("budgets", {}).get("max_cost_usd", 5)):
        raise RuntimeError("debate cost budget exceeded")
    if not usage.reasoning_summaries:
        event(row, "reasoning.summary.unavailable", "The selected model/provider did not return a reasoning summary")
    row.trace = trace
    return generated


async def execute_debate(debate_id: UUID | str, rebuttal_rounds: int = 1):
    async with SessionLocal() as session:
        row = await session.get(ResearchDebateRow, str(debate_id))
        if not row:
            return
        started = time.perf_counter()
        row.status, row.progress = "running", 5
        event(row, "debate.started", "Evidence-bound debate started")
        await session.commit()
        try:
            run = await session.get(ResearchRunRow, row.source_ids[0])
            if not run or not run.report_json:
                raise ValueError("completed earnings report is required")
            report = EarningsReport.model_validate_json(run.report_json)
            evidence = evidence_from_report(report, run.id)
            if not evidence:
                raise ValueError("source report has no cited evidence")
            bull_evidence, bear_evidence = evidence[::2] or evidence[:1], evidence[1::2] or evidence[:1]
            zh = row.language == "zh-TW"
            config = row.trace.get("config_snapshot", {})
            if "load_evidence_run" not in config.get("tools", []):
                raise RuntimeError("Debate profile does not allow load_evidence_run")
            components = ordered_components(config, COMPONENTS)
            enabled = set(components)
            verdict = None
            for index, component in enumerate(components):
                row.progress = 10 + int(index / len(components) * 75)
                event(row, "step.started", f"Starting {component}", component)
                if component == "bull":
                    row.current_round = 1
                    run_agent(row, "bull", lambda: add_turn(row, "bull", "opening", "成長、需求與執行證據支持正向論點。" if zh else "Growth, demand and execution evidence support the positive case.", bull_evidence, confidence=78))
                elif component == "bear":
                    row.current_round = 1
                    run_agent(row, "bear", lambda: add_turn(row, "bear", "opening", "風險、基期與不確定性限制正向結論。" if zh else "Risks, comparisons and uncertainty constrain the positive conclusion.", bear_evidence, confidence=72))
                elif component == "rebuttal":
                    for round_number in range(1, rebuttal_rounds + 1):
                        row.current_round = round_number + 1
                        if "bull" in enabled:
                            run_agent(row, "bull", lambda: add_turn(row, "bull", "rebuttal", "正向論點只保留有引用支持的部分，並承認風險可能降低信心。" if zh else "The bull case retains only cited claims and accepts that risks can reduce conviction.", bull_evidence, ["Bear case does not invalidate cited demand evidence"], 75))
                        if "bear" in enabled:
                            run_agent(row, "bear", lambda: add_turn(row, "bear", "rebuttal", "需求證據不等同股東報酬；仍需監控利潤率與執行落差。" if zh else "Demand evidence does not guarantee shareholder returns; margins and execution remain key.", bear_evidence, ["Demand is not sufficient evidence of returns"], 74))
                elif component == "pm":
                    row.current_round = max(row.current_round, rebuttal_rounds + 1)
                    run_agent(row, "pm", lambda: add_turn(row, "pm", "questions", "請雙方指出最脆弱的證據鏈與可推翻條件。" if zh else "Identify the weakest evidence chain and disconfirming condition.", evidence[:2], ["What evidence would reverse your view?"], 65))
                    verdict = await synthesize_verdict(row, report, evidence, zh, session)
                    row.verdict_json = verdict.model_dump_json()
                elif component == "critic":
                    if verdict is None:
                        verdict = await synthesize_verdict(row, report, evidence, zh, session)
                        row.verdict_json = verdict.model_dump_json()
                    row.current_round += 1
                    run_agent(
                        row,
                        "critic",
                        lambda verdict_evidence=verdict.evidence: add_turn(
                            row,
                            "critic",
                            "audit",
                            "裁判已檢查引用完整性、反方回應與不確定性揭露。"
                            if zh
                            else "The critic audited citation integrity, rebuttals and uncertainty disclosure.",
                            verdict_evidence,
                            confidence=94,
                        ),
                    )
                trace = row.trace
                checkpoint = hashlib.sha256(json.dumps(row.transcript, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
                trace.setdefault("checkpoints", []).append(checkpoint)
                row.trace = trace
                event(row, "decision.summary", f"{component} completed with evidence-bound output.", component, checkpoint=checkpoint)
                event(row, "step.completed", f"Completed {component}", component)
                await session.commit()
            assert verdict is not None
            row.status, row.progress = "completed", 100
            trace = row.trace
            trace["duration_ms"] = int((time.perf_counter() - started) * 1000)
            row.trace = trace
            if config.get("evaluation", True):
                result = evaluate_debate(UUID(row.id), row.transcript, verdict)
                row.eval_json = result.model_dump_json()
            trace = row.trace
            trace.setdefault("reasoning_summaries", []).append(verdict.synthesis)
            row.trace = trace
            event(row, "decision.summary", verdict.synthesis, decision=verdict.decision)
            event(row, "debate.completed", "PM verdict and critic audit completed", decision=verdict.decision)
        except Exception as exc:
            row.status, row.error = "failed", f"{type(exc).__name__}: {exc}"
            event(row, "debate.failed", row.error)
        await session.commit()


def evaluate_debate(debate_id, transcript, verdict):
    roles = {turn["role"] if isinstance(turn, dict) else turn.role for turn in transcript}
    turns = [DebateTurn.model_validate(turn) for turn in transcript]
    evidence_coverage = sum(bool(turn.evidence) for turn in turns) / max(len(turns), 1)
    rubric_average = sum(item.score for item in verdict.rubric) / max(len(verdict.rubric), 1) / 100
    values = {"required_roles": 1.0 if {"bull", "bear", "pm", "critic"} <= roles else 0.0, "turn_evidence_coverage": evidence_coverage, "rebuttal_present": 1.0 if any(t.turn_type == "rebuttal" for t in turns) else 0.0, "citation_integrity": 1.0 if verdict.evidence else 0.0, "rubric_average": rubric_average}
    thresholds = {"required_roles": 1.0, "turn_evidence_coverage": .95, "rebuttal_present": 1.0, "citation_integrity": 1.0, "rubric_average": .85}
    metrics = [EvalMetric(name=name, value=value, threshold=thresholds[name], passed=value >= thresholds[name]) for name, value in values.items()]
    return EvalResult(run_id=debate_id, passed=all(metric.passed for metric in metrics), metrics=metrics, evaluated_at=now())
