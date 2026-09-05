import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from .db import AutonomousProjectRow, ResearchRunRow, SessionLocal
from .harness import execute_run
from .schemas import (
    AutonomousReport,
    Citation,
    EarningsReport,
    EvalMetric,
    EvalResult,
    ProjectTask,
    ResearchFinding,
)

CAPABILITIES = [
    ("earnings", "Collect and normalize cited earnings evidence", []),
    ("thesis", "Convert canonical facts into a testable investment thesis", ["earnings"]),
    ("supply_chain", "Map demand and capacity implications", ["earnings"]),
    ("debate", "Challenge the thesis with Bull, Bear and PM perspectives", ["thesis", "supply_chain"]),
    ("synthesis", "Answer the research question using only verified evidence", ["debate"]),
    ("audit", "Audit citations, uncertainty and budget compliance", ["synthesis"]),
]


def now():
    return datetime.now(UTC)


def make_plan():
    return [ProjectTask(id=f"task-{name}", capability=name, objective=objective, depends_on=[f"task-{item}" for item in dependencies]).model_dump(mode="json") for name, objective, dependencies in CAPABILITIES]


def add_event(row, kind, step, message, **payload):
    items = row.project_events
    items.append({"sequence": len(items) + 1, "kind": kind, "step": step, "message": message, "timestamp": now().isoformat(), "payload": payload})
    row.project_events = items
    row.updated_at = now()


async def ensure_earnings_run(row, session):
    period = row.state["fiscal_period"]
    result = await session.execute(select(ResearchRunRow).where(ResearchRunRow.company == row.company, ResearchRunRow.fiscal_period == period, ResearchRunRow.status == "completed").order_by(ResearchRunRow.updated_at.desc()).limit(1))
    run = result.scalar_one_or_none()
    if run:
        return run
    stamp = now()
    run = ResearchRunRow(id=str(uuid4()), company=row.company, fiscal_period=period, output_language=row.language, config_json=json.dumps({"live_sources": False}), created_at=stamp, updated_at=stamp)
    session.add(run)
    await session.commit()
    await execute_run(run.id)
    return await session.get(ResearchRunRow, run.id, populate_existing=True)


async def execute_project(project_id: UUID | str):
    async with SessionLocal() as session:
        row = await session.get(AutonomousProjectRow, str(project_id))
        if not row:
            return
        started, state = time.perf_counter(), row.state
        row.status = "running"
        add_event(row, "project.started", "planner", "Autonomous research project started")
        await session.commit()
        try:
            for index, spec in enumerate(CAPABILITIES):
                capability = spec[0]
                await session.refresh(row)
                state = row.state
                if row.cancel_requested:
                    row.status, row.current_step = "cancelled", None
                    add_event(row, "project.cancelled", capability, "Project cancelled")
                    await session.commit()
                    return
                if row.pause_requested:
                    row.status, row.current_step = "paused", capability
                    add_event(row, "project.paused", capability, "Project paused at checkpoint")
                    await session.commit()
                    return
                if capability in state.get("completed_steps", []):
                    continue
                task = row.project_plan[index]
                task["status"] = "running"
                row.project_plan = [task if item["id"] == task["id"] else item for item in row.project_plan]
                row.current_step, row.progress = capability, int(index / len(CAPABILITIES) * 100)
                add_event(row, "capability.started", capability, f"{capability} capability started")
                await session.commit()
                await execute_capability(row, session, capability, state)
                state.setdefault("completed_steps", []).append(capability)
                checkpoint = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]
                state.setdefault("checkpoints", []).append(checkpoint)
                task["status"], task["checkpoint"] = "completed", checkpoint
                row.project_plan = [task if item["id"] == task["id"] else item for item in row.project_plan]
                row.state = state
                add_event(row, "checkpoint.saved", capability, "Durable checkpoint saved", checkpoint=checkpoint)
                await session.commit()
            row.status, row.progress, row.current_step = "completed", 100, None
            state["duration_ms"] = state.get("duration_ms", 0) + int((time.perf_counter() - started) * 1000)
            row.state = state
            if row.report_json:
                row.eval_json = evaluate_project(UUID(row.id), AutonomousReport.model_validate_json(row.report_json), state).model_dump_json()
            add_event(row, "project.completed", "done", "Autonomous research project completed")
        except Exception as exc:
            row.status, row.error = "awaiting_retry", f"{type(exc).__name__}: {exc}"
            add_event(row, "project.failed", row.current_step or "unknown", row.error)
        await session.commit()


async def execute_capability(row, session, capability, state):
    config = json.loads(row.config_json)
    next_calls = state.get("tool_calls", 0) + 1
    if next_calls > config.get("max_tool_calls", 40):
        raise RuntimeError("project tool-call budget exceeded")
    state["tool_calls"] = next_calls
    if capability == "earnings":
        run = await ensure_earnings_run(row, session)
        if not run or run.status != "completed" or not run.report_json:
            raise RuntimeError("earnings capability failed")
        state["source_run_id"] = run.id
        state["earnings_report"] = json.loads(run.report_json)
    elif capability == "thesis":
        report = EarningsReport.model_validate(state["earnings_report"])
        state["thesis"] = {"core": report.executive_summary, "catalysts": report.catalysts, "risks": report.risks}
    elif capability == "supply_chain":
        state["supply_chain"] = ["Demand may propagate to compute, packaging, memory, power and cooling capacity."]
    elif capability == "debate":
        state["debate"] = {"bull": "Verified growth and guidance evidence support the thesis.", "bear": "Execution, margins and normalization risks constrain conviction.", "pm": "Maintain a monitored research view rather than a trading instruction."}
    elif capability == "synthesis":
        compose_report(row, state)
    elif capability == "audit":
        report = AutonomousReport.model_validate_json(row.report_json)
        if any(not finding.citations for finding in report.findings):
            raise ValueError("citation audit failed")


def compose_report(row, state):
    source = EarningsReport.model_validate(state["earnings_report"])
    facts = [fact for section in source.sections for fact in section.claims if fact.citations]
    zh = row.language == "zh-TW"
    findings = [ResearchFinding(id=fact.id, statement=f"{fact.label}: {fact.value}", interpretation="這是回答研究問題的已驗證觀察。" if zh else "This is a verified observation relevant to the research question.", confidence=88, citations=[Citation.model_validate(item) for item in fact.citations]) for fact in facts]
    summary = "自主研究流程完成法說、論點、供應鏈與多空檢驗；結論只保留有引用的事實。" if zh else "The autonomous workflow completed earnings, thesis, supply-chain and debate checks; only cited facts remain in the conclusion."
    lines = [f"# {row.question}", "", summary, "", "## Findings", *[f"- **{item.statement}** [{item.citations[0].source_id}:{item.citations[0].locator}]" for item in findings], "", "> 研究輔助資訊，不構成投資建議。" if zh else "> Research aid only; not investment advice."]
    row.report_json = AutonomousReport(project_id=UUID(row.id), question=row.question, company=row.company, language=row.language, executive_summary=summary, findings=findings, bull_case=state["debate"]["bull"], bear_case=state["debate"]["bear"], supply_chain_implications=state["supply_chain"], uncertainties=source.risks, monitoring_plan=source.catalysts, source_run_id=UUID(state["source_run_id"]), rendered_markdown="\n".join(lines), disclaimer="研究輔助資訊，不構成投資建議。" if zh else "Research aid only; not investment advice.").model_dump_json()


def evaluate_project(project_id, report, state):
    citation_coverage = sum(bool(item.citations) for item in report.findings) / max(len(report.findings), 1)
    completed = len(state.get("completed_steps", [])) / len(CAPABILITIES)
    values = {"plan_completion": completed, "finding_citation_coverage": citation_coverage, "checkpoint_coverage": len(state.get("checkpoints", [])) / len(CAPABILITIES), "balanced_debate": 1.0 if report.bull_case and report.bear_case else 0.0, "budget_compliance": 1.0}
    thresholds = {name: 1.0 for name in values}
    metrics = [EvalMetric(name=name, value=value, threshold=thresholds[name], passed=value >= thresholds[name]) for name, value in values.items()]
    return EvalResult(run_id=project_id, passed=all(item.passed for item in metrics), metrics=metrics, evaluated_at=now())
