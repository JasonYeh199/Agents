import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import Base, ResearchRunRow, SessionLocal, engine, get_run
from app.harness import execute_run


@pytest.mark.asyncio
@pytest.mark.parametrize("company,period", [("nvidia", "FY2025-Q4"), ("tsmc", "FY2024-Q4")])
async def test_end_to_end_checkpointed_bilingual_run(company, period):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    rid, stamp = str(uuid4()), datetime.now(UTC)
    async with SessionLocal() as session:
        session.add(
            ResearchRunRow(
                id=rid,
                company=company,
                fiscal_period=period,
                output_language="zh-TW",
                created_at=stamp,
                updated_at=stamp,
            )
        )
        await session.commit()
    await execute_run(rid)
    async with SessionLocal() as session:
        row = await get_run(session, rid)
        assert row.status == "completed"
        report = json.loads(row.report_json)
        assert report["canonical_facts_hash"]
        assert any(
            fact["category"] == "comparison"
            for section in report["sections"]
            for fact in section["claims"]
        )
        assert all(f["citations"] for s in report["sections"] for f in s["claims"])
        assert json.loads(row.eval_json)["passed"] is True
