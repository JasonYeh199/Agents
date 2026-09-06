import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .db import InvestmentThesisRow, ResearchRunRow, SessionLocal
from .profiles import published_profile
from .providers import get_provider
from .schemas import Citation, EarningsReport, ThesisClaim, ThesisEvidence, ThesisSnapshot

CREATE_PROMPT = (
    "Build an evidence-bound investment thesis. Preserve all evidence values and "
    "citations exactly. Every claim must have supplied evidence."
)
UPDATE_PROMPT = (
    "Update the prior investment thesis using only the new cited evidence. Mark "
    "claims strengthened, unchanged, weakened, invalidated, or new. Preserve "
    "citations and values exactly."
)


def _profile_instructions(profile_prompt: str, task_prompt: str) -> str:
    """Apply the immutable published prompt while retaining harness invariants."""
    return f"{profile_prompt.strip()}\n\n{task_prompt}"


def _snapshot(report: EarningsReport, run_id: str, version: int, previous=None) -> ThesisSnapshot:
    zh = report.language.value == "zh-TW"
    old_facts = {e.fact_id for c in previous.claims for e in c.evidence} if previous else set()
    dimensions = {
        "metrics": "growth",
        "guidance": "growth",
        "comparison": "competitive",
        "management": "margin",
    }
    claims = []
    for section in report.sections:
        for fact in section.claims:
            evidence = ThesisEvidence(
                source_run_id=run_id,
                fact_id=fact.id,
                value=fact.value,
                citation=Citation.model_validate(fact.citations[0]),
            )
            statement = (
                f"{fact.label} \u70ba {fact.value}\uff0c\u662f\u76ee\u524d\u6295\u8cc7\u8ad6\u9ede\u7684\u53ef\u9a57\u8b49\u4f9d\u64da\u3002"
                if zh
                else f"{fact.label} at {fact.value} is verified evidence for the current thesis."
            )
            claims.append(
                ThesisClaim(
                    id=hashlib.sha1(f"{fact.category}:{fact.id}".encode()).hexdigest()[:12],
                    statement=statement,
                    dimension=dimensions.get(fact.category, "growth"),
                    status="strengthened" if fact.id in old_facts else "new",
                    confidence=85 if fact.verified else 50,
                    evidence=[evidence],
                )
            )
    company_name = (
        "\u53f0\u7a4d\u96fb"
        if report.company == "2330.TW" and zh
        else report.company.upper()
    )
    title_suffix = "\u6295\u8cc7\u8ad6\u9ede" if zh else "Investment Thesis"
    signals = (
        [
            f"\u82e5\u4e0b\u671f\u5df2\u9a57\u8b49\u6578\u64da\u8207\u300c{risk}\u300d\u4e00\u81f4\uff0c\u61c9\u964d\u4f4e\u4fe1\u5fc3\u3002"
            for risk in report.risks
        ]
        if zh
        else [
            f"Reduce confidence if subsequent verified data confirms: {risk}."
            for risk in report.risks
        ]
    )
    return ThesisSnapshot(
        version=version,
        company=report.company,
        title=f"{company_name} {title_suffix}",
        core_thesis=report.executive_summary,
        claims=claims,
        catalysts=report.catalysts,
        risks=report.risks,
        disconfirming_signals=signals,
        source_run_ids=[run_id],
        language=report.language,
        updated_at=datetime.now(UTC),
    )


def _evidence_keys(snapshot: ThesisSnapshot) -> set[tuple[str, str, str, str]]:
    return {
        (e.source_run_id, e.fact_id, e.value, e.citation.source_id)
        for claim in snapshot.claims
        for e in claim.evidence
    }


def _enforce_authoritative_state(
    generated: ThesisSnapshot,
    draft: ThesisSnapshot,
    allowed_evidence: set[tuple[str, str, str, str]],
) -> ThesisSnapshot:
    """The model writes prose; the harness owns identity, versions and evidence integrity."""
    generated.version, generated.company, generated.language = (
        draft.version,
        draft.company,
        draft.language,
    )
    generated.source_run_ids = draft.source_run_ids
    return generated if _evidence_keys(generated).issubset(allowed_evidence) else draft


async def create_thesis_from_run(source_run_id: UUID, title: str | None = None) -> UUID:
    async with SessionLocal() as session:
        run = await session.get(ResearchRunRow, str(source_run_id))
        if not run or run.status != "completed" or not run.report_json:
            raise ValueError("source earnings run must be completed")
        report = EarningsReport.model_validate_json(run.report_json)
        profile = await published_profile(session, "thesis")
        snapshot = _snapshot(report, run.id, 1)
        draft = snapshot.model_copy(deep=True)
        if provider := get_provider(run_config=profile.config):
            generated, _ = await provider.generate_structured(
                _profile_instructions(profile.config["prompt"], CREATE_PROMPT),
                {"draft": snapshot.model_dump(mode="json")},
                ThesisSnapshot,
            )
            snapshot = _enforce_authoritative_state(generated, draft, _evidence_keys(draft))
        if title:
            snapshot.title = title
        thesis_id, stamp = uuid4(), datetime.now(UTC)
        event = {
            "sequence": 1,
            "kind": "thesis.created",
            "step": "synthesize",
            "message": "Initial thesis created",
            "timestamp": stamp.isoformat(),
            "payload": {"source_run_id": run.id, "version": 1, "profile_version_id": profile.id},
        }
        row = InvestmentThesisRow(
            id=str(thesis_id),
            company=run.company,
            title=snapshot.title,
            language=run.output_language,
            status="active",
            version=1,
            thesis_json=snapshot.model_dump_json(),
            versions_json=json.dumps([snapshot.model_dump(mode="json")], default=str),
            events_json=json.dumps([event]),
            source_run_ids_json=json.dumps([run.id]),
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        await session.commit()
        return thesis_id


async def update_thesis_from_run(thesis_id: UUID, source_run_id: UUID) -> None:
    async with SessionLocal() as session:
        row = await session.get(InvestmentThesisRow, str(thesis_id))
        run = await session.get(ResearchRunRow, str(source_run_id))
        if not row or not run or run.status != "completed" or not run.report_json:
            raise ValueError("thesis and completed source run are required")
        if run.company != row.company:
            raise ValueError("source run company does not match thesis")
        if run.id in row.source_run_ids:
            raise ValueError("source run was already applied")
        previous = ThesisSnapshot.model_validate(row.thesis)
        report = EarningsReport.model_validate_json(run.report_json)
        profile = await published_profile(session, "thesis")
        snapshot = _snapshot(report, run.id, row.version + 1, previous)
        snapshot.source_run_ids = [*row.source_run_ids, run.id]
        draft = snapshot.model_copy(deep=True)
        allowed = _evidence_keys(previous) | _evidence_keys(draft)
        if provider := get_provider(run_config=profile.config):
            generated, _ = await provider.generate_structured(
                _profile_instructions(profile.config["prompt"], UPDATE_PROMPT),
                {
                    "previous": previous.model_dump(mode="json"),
                    "new_evidence_report": report.model_dump(mode="json"),
                    "draft": snapshot.model_dump(mode="json"),
                },
                ThesisSnapshot,
            )
            snapshot = _enforce_authoritative_state(generated, draft, allowed)
        versions, events, source_ids = row.versions, row.thesis_events, row.source_run_ids
        versions.append(snapshot.model_dump(mode="json"))
        events.append(
            {
                "sequence": len(events) + 1,
                "kind": "thesis.updated",
                "step": "update",
                "message": "Thesis updated from new earnings evidence",
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": {"source_run_id": run.id, "version": snapshot.version, "profile_version_id": profile.id},
            }
        )
        source_ids.append(run.id)
        row.version, row.title, row.updated_at = snapshot.version, snapshot.title, datetime.now(UTC)
        row.thesis, row.versions, row.thesis_events, row.source_run_ids = (
            snapshot.model_dump(mode="json"),
            versions,
            events,
            source_ids,
        )
        await session.commit()
