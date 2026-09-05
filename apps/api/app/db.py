import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings


class Base(DeclarativeBase):
    pass


class ResearchRunRow(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company: Mapped[str] = mapped_column(String(16))
    fiscal_period: Mapped[str] = mapped_column(String(16))
    output_language: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def touch(self):
        self.updated_at = datetime.now(UTC)

    @property
    def state(self):
        return json.loads(self.state_json)

    @state.setter
    def state(self, value):
        self.state_json = json.dumps(value, ensure_ascii=False)

    @property
    def events(self):
        return json.loads(self.events_json)

    @events.setter
    def events(self, value):
        self.events_json = json.dumps(value, ensure_ascii=False)


class InvestmentThesisRow(Base):
    __tablename__ = "investment_theses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(240))
    language: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    thesis_json: Mapped[str] = mapped_column(Text)
    versions_json: Mapped[str] = mapped_column(Text, default="[]")
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    source_run_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def thesis(self):
        return json.loads(self.thesis_json)

    @thesis.setter
    def thesis(self, value):
        self.thesis_json = json.dumps(value, ensure_ascii=False)

    @property
    def versions(self):
        return json.loads(self.versions_json)

    @versions.setter
    def versions(self, value):
        self.versions_json = json.dumps(value, ensure_ascii=False)

    @property
    def thesis_events(self):
        return json.loads(self.events_json)

    @thesis_events.setter
    def thesis_events(self, value):
        self.events_json = json.dumps(value, ensure_ascii=False)

    @property
    def source_run_ids(self):
        return json.loads(self.source_run_ids_json)

    @source_run_ids.setter
    def source_run_ids(self, value):
        self.source_run_ids_json = json.dumps(value)


class SupplyChainInvestigationRow(Base):
    __tablename__ = "supply_chain_investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(120), index=True)
    time_window: Mapped[str] = mapped_column(String(64))
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    graph_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def touch(self):
        self.updated_at = datetime.now(UTC)

    def _get_json(self, name, default):
        return json.loads(getattr(self, name) or json.dumps(default))

    def _set_json(self, name, value):
        setattr(self, name, json.dumps(value, ensure_ascii=False))

    state = property(lambda self: self._get_json("state_json", {}), lambda self, v: self._set_json("state_json", v))
    events = property(lambda self: self._get_json("events_json", []), lambda self, v: self._set_json("events_json", v))


class ResearchDebateRow(Base):
    __tablename__ = "research_debates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(16), index=True)
    language: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    transcript_json: Mapped[str] = mapped_column(Text, default="[]")
    verdict_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    trace_json: Mapped[str] = mapped_column(Text, default="{}")
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def _json_property(name, default):
        return property(
            lambda self: json.loads(getattr(self, name) or json.dumps(default)),
            lambda self, value: setattr(self, name, json.dumps(value, ensure_ascii=False)),
        )

    source_ids = _json_property("source_ids_json", [])
    transcript = _json_property("transcript_json", [])
    debate_events = _json_property("events_json", [])
    trace = _json_property("trace_json", {})


class AutonomousProjectRow(Base):
    __tablename__ = "autonomous_research_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(String(16), index=True)
    language: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="[]")
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pause_requested: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def _json_property(name, default):
        return property(lambda self: json.loads(getattr(self, name) or json.dumps(default)), lambda self, value: setattr(self, name, json.dumps(value, ensure_ascii=False)))

    state = _json_property("state_json", {})
    project_plan = _json_property("plan_json", [])
    project_events = _json_property("events_json", [])


class EvaluationArenaRow(Base):
    __tablename__ = "evaluation_arenas"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    dataset: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    winner_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def _json_property(name, default):
        return property(lambda self: json.loads(getattr(self, name) or json.dumps(default)), lambda self, value: setattr(self, name, json.dumps(value, ensure_ascii=False)))

    arena_results = _json_property("results_json", [])
    arena_events = _json_property("events_json", [])


engine = create_async_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session():
    async with SessionLocal() as session:
        yield session


async def get_run(session: AsyncSession, run_id: UUID | str) -> ResearchRunRow | None:
    return await session.get(ResearchRunRow, str(run_id))
