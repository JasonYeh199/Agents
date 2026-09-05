from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import Base, ResearchRunRow, SessionLocal, engine
from app.harness import execute_run
from app.schemas import ThesisSnapshot
from app.thesis import (
    _enforce_authoritative_state,
    _evidence_keys,
    create_thesis_from_run,
    update_thesis_from_run,
)


async def completed_run(company: str, period: str, language: str = "en") -> str:
    rid, stamp = str(uuid4()), datetime.now(UTC)
    async with SessionLocal() as session:
        session.add(
            ResearchRunRow(
                id=rid,
                company=company,
                fiscal_period=period,
                output_language=language,
                created_at=stamp,
                updated_at=stamp,
            )
        )
        await session.commit()
    await execute_run(rid)
    return rid


@pytest.mark.asyncio
async def test_thesis_memory_versions_and_company_guard():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    q3 = await completed_run("nvidia", "FY2025-Q3")
    q4 = await completed_run("nvidia", "FY2025-Q4")
    thesis_id = await create_thesis_from_run(q3)
    await update_thesis_from_run(thesis_id, q4)
    async with SessionLocal() as session:
        from app.db import InvestmentThesisRow

        row = await session.get(InvestmentThesisRow, str(thesis_id))
        assert row.version == 2
        assert len(row.versions) == 2
        snapshot = ThesisSnapshot.model_validate(row.thesis)
        assert snapshot.source_run_ids == [q3, q4]
        assert all(claim.evidence for claim in snapshot.claims)

    tsmc = await completed_run("tsmc", "FY2024-Q4")
    with pytest.raises(ValueError, match="does not match"):
        await update_thesis_from_run(thesis_id, tsmc)


def test_model_cannot_overwrite_authoritative_thesis_memory():
    from app.schemas import Citation, ThesisClaim, ThesisEvidence, ThesisSnapshot

    stamp = datetime.now(UTC)
    evidence = ThesisEvidence(
        source_run_id="run-1",
        fact_id="revenue",
        value="$1",
        citation=Citation(
            source_id="s1", claim_id="revenue", locator="p:1", supporting_excerpt="Revenue was $1"
        ),
    )
    draft = ThesisSnapshot(
        version=2,
        company="nvidia",
        title="T",
        core_thesis="Core",
        claims=[
            ThesisClaim(
                id="c1",
                statement="S",
                dimension="growth",
                status="new",
                confidence=80,
                evidence=[evidence],
            )
        ],
        catalysts=[],
        risks=[],
        disconfirming_signals=[],
        source_run_ids=["run-0", "run-1"],
        language="en",
        updated_at=stamp,
    )
    generated = draft.model_copy(deep=True)
    generated.version = 99
    generated.source_run_ids = ["hallucinated"]
    secured = _enforce_authoritative_state(generated, draft, _evidence_keys(draft))
    assert secured.version == 2
    assert secured.source_run_ids == ["run-0", "run-1"]
