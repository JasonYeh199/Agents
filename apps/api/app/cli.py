import asyncio
import json
from datetime import UTC

import typer

from .db import SessionLocal, get_run, init_db
from .harness import execute_run
from .schemas import Language
from .universe import normalize_ticker

cli = typer.Typer()


@cli.command()
def demo(
    company: str = "NVDA",
    period: str = "FY2025-Q4",
    language: Language = Language.ZH_TW,
):
    async def go():
        from datetime import datetime
        from uuid import uuid4

        from .db import ResearchRunRow

        await init_db()
        stamp = datetime.now(UTC)
        rid = str(uuid4())
        async with SessionLocal() as s:
            s.add(
                ResearchRunRow(
                    id=rid,
                    company=normalize_ticker(company),
                    fiscal_period=period,
                    output_language=language.value,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            await s.commit()
        await execute_run(rid)
        async with SessionLocal() as s:
            row = await get_run(s, rid)
            print(json.dumps(json.loads(row.report_json), indent=2, ensure_ascii=False))

    asyncio.run(go())


if __name__ == "__main__":
    cli()
