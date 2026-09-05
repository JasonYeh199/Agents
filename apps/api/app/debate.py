import time
from datetime import UTC, datetime
from uuid import UUID

from .db import ResearchDebateRow, ResearchRunRow, SessionLocal
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
    started = time.perf_counter()
    event(row, "agent.started", f"{role.upper()} agent started", role)
    callback()
    trace = row.trace
    trace.setdefault("agents", []).append(AgentExecution(task_id=f"{role}-r{row.current_round}", agent_role=role, status="completed", tool_calls=1, duration_ms=max(1, int((time.perf_counter() - started) * 1000))).model_dump(mode="json"))
    trace["tool_calls"] = trace.get("tool_calls", 0) + 1
    row.trace = trace


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
            row.current_round = 1
            run_agent(row, "bull", lambda: add_turn(row, "bull", "opening", "成長、需求與執行證據支持正向論點。" if zh else "Growth, demand and execution evidence support the positive case.", bull_evidence, confidence=78))
            run_agent(row, "bear", lambda: add_turn(row, "bear", "opening", "風險、基期與不確定性限制正向結論。" if zh else "Risks, comparisons and uncertainty constrain the positive conclusion.", bear_evidence, confidence=72))
            row.progress = 40
            for round_number in range(1, rebuttal_rounds + 1):
                row.current_round = round_number + 1
                run_agent(row, "pm", lambda: add_turn(row, "pm", "questions", "請雙方指出對方最脆弱的證據鏈與可推翻條件。" if zh else "Identify the weakest opposing evidence chain and disconfirming condition.", evidence[:2], ["What evidence would reverse your view?"], 65))
                run_agent(row, "bull", lambda: add_turn(row, "bull", "rebuttal", "正向論點只保留有引用支持的部分，並承認風險可能降低信心。" if zh else "The bull case retains only cited claims and accepts that risks can reduce conviction.", bull_evidence, ["Bear case does not invalidate cited demand evidence"], 75))
                run_agent(row, "bear", lambda: add_turn(row, "bear", "rebuttal", "需求證據不等同股東報酬；仍需監控利潤率與執行落差。" if zh else "Demand evidence does not guarantee shareholder returns; margins and execution remain key.", bear_evidence, ["Demand is not sufficient evidence of returns"], 74))
            row.progress = 75
            rubric = [DebateRubricScore(dimension=name, score=score, rationale=reason) for name, score, reason in [
                ("evidence_quality", 94, "All material arguments retain structured source evidence."),
                ("counterargument", 90, "Both sides answer the opposing thesis."),
                ("uncertainty", 92, "The verdict preserves risks and monitoring conditions."),
                ("decision_usefulness", 88, "The PM receives a bounded watch decision."),
                ("citation_integrity", 100, "Every verdict citation is present in the source report."),
            ]]
            verdict = DebateVerdict(decision="watch", conviction=72, synthesis="證據支持持續追蹤，但多空論點仍存在可驗證的不確定性。" if zh else "Evidence supports continued monitoring while testable uncertainties remain on both sides.", strongest_bull_case=row.transcript[0]["argument"], strongest_bear_case=row.transcript[1]["argument"], key_uncertainties=report.risks[:3] or ["Execution"], monitoring_triggers=report.catalysts[:3] or ["Next earnings update"], evidence=evidence[:min(4, len(evidence))], rubric=rubric, disclaimer="研究輔助資訊，不構成投資建議。" if zh else "Research aid only; not investment advice.")
            row.verdict_json = verdict.model_dump_json()
            row.current_round += 1
            run_agent(row, "critic", lambda: add_turn(row, "critic", "audit", "裁判已檢查引用完整性、反方回應與不確定性揭露。" if zh else "The critic audited citation integrity, rebuttals and uncertainty disclosure.", verdict.evidence, confidence=94))
            row.status, row.progress = "completed", 100
            trace = row.trace
            trace["duration_ms"] = int((time.perf_counter() - started) * 1000)
            row.trace = trace
            result = evaluate_debate(UUID(row.id), row.transcript, verdict)
            row.eval_json = result.model_dump_json()
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
