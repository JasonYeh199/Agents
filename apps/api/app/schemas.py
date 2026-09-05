from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class Company(StrEnum):
    NVIDIA = "nvidia"
    TSMC = "tsmc"


class Language(StrEnum):
    EN = "en"
    ZH_TW = "zh-TW"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_RETRY = "awaiting_retry"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunConfig(BaseModel):
    model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    max_tool_calls: int = Field(20, ge=1, le=100)
    max_output_tokens: int = Field(6000, ge=500, le=50000)
    live_sources: bool = False


class CreateRun(BaseModel):
    company: Company
    fiscal_period: str = Field(pattern=r"^FY\d{4}-Q[1-4]$")
    output_language: Language = Language.ZH_TW
    config: RunConfig = Field(default_factory=RunConfig)


class SourceDocument(BaseModel):
    id: str
    # Keep the generated Structured Outputs schema provider-compatible. Pydantic's
    # HttpUrl emits `format: uri`, which the Responses API JSON-schema subset rejects.
    url: str
    publisher: str
    document_type: str
    published_at: datetime
    fetched_at: datetime
    sha256: str
    object_key: str
    parser_version: str = "1.0.0"
    language: str

    @field_validator("url")
    @classmethod
    def validate_https_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("source URL must be an absolute HTTPS URL")
        return value


class Citation(BaseModel):
    source_id: str
    claim_id: str
    locator: str
    supporting_excerpt: str = Field(min_length=1, max_length=1200)


class Fact(BaseModel):
    id: str
    category: str
    label: str
    value: str
    period: str
    verifiable: bool = True
    verified: bool = True
    citations: list[Citation] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_facts_need_evidence(self):
        if self.verifiable and self.verified and not self.citations:
            raise ValueError("verified facts require at least one citation")
        return self


class ReportSection(BaseModel):
    title: str
    claims: list[Fact]


class EarningsReport(BaseModel):
    company: Company
    fiscal_period: str
    language: Language
    executive_summary: str
    sections: list[ReportSection]
    catalysts: list[str]
    risks: list[str]
    unverified: list[str]
    sources: list[SourceDocument]
    disclaimer: str
    canonical_facts_hash: str
    rendered_markdown: str


class RunEvent(BaseModel):
    sequence: int
    kind: str
    step: str
    message: str
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class RunView(BaseModel):
    id: UUID
    company: Company
    fiscal_period: str
    output_language: Language
    status: RunStatus
    current_step: str | None
    progress: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class EvalMetric(BaseModel):
    name: str
    value: float
    threshold: float
    passed: bool


class EvalResult(BaseModel):
    run_id: UUID
    passed: bool
    metrics: list[EvalMetric]
    evaluated_at: datetime


class TraceView(BaseModel):
    run_id: UUID
    events: list[RunEvent]
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    tool_calls: int
    duration_ms: int


class ThesisEvidence(BaseModel):
    source_run_id: str
    fact_id: str
    value: str
    citation: Citation


class ThesisClaim(BaseModel):
    id: str
    statement: str
    dimension: Literal["growth", "margin", "competitive", "valuation", "risk"]
    status: Literal["strengthened", "unchanged", "weakened", "invalidated", "new"]
    confidence: int = Field(ge=0, le=100)
    evidence: list[ThesisEvidence] = Field(min_length=1)


class ThesisSnapshot(BaseModel):
    version: int = Field(ge=1)
    company: Company
    title: str
    core_thesis: str
    claims: list[ThesisClaim] = Field(min_length=1)
    catalysts: list[str]
    risks: list[str]
    disconfirming_signals: list[str]
    source_run_ids: list[str] = Field(min_length=1)
    language: Language
    updated_at: datetime


class CreateThesis(BaseModel):
    source_run_id: UUID
    title: str | None = Field(default=None, max_length=240)


class UpdateThesis(BaseModel):
    source_run_id: UUID


class ThesisView(BaseModel):
    id: UUID
    status: Literal["active", "invalidated", "archived"]
    snapshot: ThesisSnapshot
    versions: list[ThesisSnapshot]
    events: list[RunEvent]


class InvestigationConfig(BaseModel):
    model: str | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    max_tool_calls: int = Field(30, ge=1, le=100)
    max_output_tokens: int = Field(8000, ge=500, le=50000)
    max_cost_usd: float = Field(2.0, gt=0, le=100)
    live_sources: bool = False


class CreateInvestigation(BaseModel):
    signal_type: Literal["shortage", "price_increase", "capacity_bottleneck"]
    subject: str = Field(min_length=2, max_length=120)
    time_window: str = Field(min_length=2, max_length=64)
    question: str | None = Field(default=None, max_length=1000)
    language: Language = Language.ZH_TW
    config: InvestigationConfig = Field(default_factory=InvestigationConfig)


class InvestigationTask(BaseModel):
    id: str
    agent_role: Literal["capacity", "supplier", "demand", "entity_resolver", "graph_synthesizer", "critic", "beneficiary"]
    objective: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    depends_on: list[str] = Field(default_factory=list)
    tool_budget: int = Field(ge=0, le=50)


class AgentExecution(BaseModel):
    task_id: str
    agent_role: str
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    duration_ms: int = 0
    retries: int = 0


class SupplyChainEntity(BaseModel):
    id: str
    type: Literal["company", "product", "technology", "capacity", "facility", "region"]
    name: str
    ticker: str | None = None
    aliases: list[str] = Field(default_factory=list)
    verified: bool = True


class RelationshipEvidence(BaseModel):
    citation: Citation
    source_date: datetime
    primary_source: bool = True


class SupplyChainRelationship(BaseModel):
    id: str
    source_entity_id: str
    target_entity_id: str
    type: Literal["supplies", "manufactures", "packages", "depends_on", "expands", "constrained_by", "benefits_from"]
    confidence: int = Field(ge=0, le=100)
    inference_level: Literal["direct", "derived"]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[RelationshipEvidence] = Field(min_length=1)


class GraphConflict(BaseModel):
    id: str
    relationship_ids: list[str] = Field(min_length=1)
    description: str
    status: Literal["open", "resolved"] = "open"


class EvidenceGraph(BaseModel):
    version: str = "1.0"
    nodes: list[SupplyChainEntity]
    edges: list[SupplyChainRelationship]
    conflicts: list[GraphConflict] = Field(default_factory=list)


class BeneficiaryCandidate(BaseModel):
    company_entity_id: str
    exposure: str
    benefit_mechanism: str
    evidence_path: list[str] = Field(min_length=1)
    catalysts: list[str]
    risks: list[str]
    counter_evidence: list[str]
    unverified: list[str]
    confidence: int = Field(ge=0, le=100)
    primary_source_count: int = Field(ge=0)
    qualified: bool


class InvestigationReport(BaseModel):
    investigation_id: UUID
    language: Language
    summary: str
    candidates: list[BeneficiaryCandidate]
    watchlist: list[BeneficiaryCandidate]
    graph_version: str
    source_documents: list[SourceDocument]
    rendered_markdown: str
    disclaimer: str


class InvestigationView(BaseModel):
    id: UUID
    signal_type: str
    subject: str
    time_window: str
    question: str | None
    language: Language
    status: RunStatus
    current_step: str | None
    progress: int
    error: str | None
    tasks: list[InvestigationTask] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InvestigationTrace(BaseModel):
    investigation_id: UUID
    events: list[RunEvent]
    agents: list[AgentExecution]
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    tool_calls: int
    duration_ms: int


class CreateDebate(BaseModel):
    topic: str = Field(min_length=5, max_length=500)
    source_run_id: UUID
    thesis_id: UUID | None = None
    investigation_id: UUID | None = None
    language: Language = Language.ZH_TW
    rebuttal_rounds: int = Field(1, ge=1, le=3)

    @field_validator("topic")
    @classmethod
    def reject_corrupted_topic(cls, value: str) -> str:
        if value.count("?") >= 3 and value.count("?") / max(len(value), 1) > 0.25:
            raise ValueError("topic appears to contain encoding replacement characters")
        return value


class DebateEvidence(BaseModel):
    source_kind: Literal["earnings", "thesis", "supply_chain"]
    source_record_id: str
    claim_id: str
    value: str
    citation: Citation


class DebateTurn(BaseModel):
    sequence: int
    round: int
    role: Literal["bull", "bear", "pm", "critic"]
    turn_type: Literal["opening", "rebuttal", "questions", "verdict", "audit"]
    argument: str
    evidence: list[DebateEvidence] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)
    confidence: int = Field(ge=0, le=100)


class DebateRubricScore(BaseModel):
    dimension: Literal["evidence_quality", "counterargument", "uncertainty", "decision_usefulness", "citation_integrity"]
    score: int = Field(ge=0, le=100)
    rationale: str


class DebateVerdict(BaseModel):
    decision: Literal["bullish", "bearish", "watch", "insufficient_evidence"]
    conviction: int = Field(ge=0, le=100)
    synthesis: str
    strongest_bull_case: str
    strongest_bear_case: str
    key_uncertainties: list[str]
    monitoring_triggers: list[str]
    evidence: list[DebateEvidence] = Field(min_length=1)
    rubric: list[DebateRubricScore]
    disclaimer: str


class DebateView(BaseModel):
    id: UUID
    topic: str
    company: Company
    language: Language
    status: RunStatus
    current_round: int
    progress: int
    source_ids: list[str]
    transcript: list[DebateTurn]
    verdict: DebateVerdict | None
    events: list[RunEvent]
    created_at: datetime
    updated_at: datetime


class DebateTrace(BaseModel):
    debate_id: UUID
    agents: list[AgentExecution]
    events: list[RunEvent]
    input_tokens: int
    output_tokens: int
    tool_calls: int
    duration_ms: int
    estimated_cost_usd: float


class AutonomousConfig(BaseModel):
    max_tool_calls: int = Field(40, ge=5, le=200)
    max_cost_usd: float = Field(5, gt=0, le=100)
    max_steps: int = Field(10, ge=3, le=20)
    checkpoint_each_step: bool = True


class CreateAutonomousProject(BaseModel):
    question: str = Field(min_length=10, max_length=2000)
    company: Company
    fiscal_period: str = Field(pattern=r"^FY\d{4}-Q[1-4]$")
    language: Language = Language.ZH_TW
    config: AutonomousConfig = Field(default_factory=AutonomousConfig)


class ProjectTask(BaseModel):
    id: str
    capability: Literal["earnings", "thesis", "supply_chain", "debate", "synthesis", "audit"]
    objective: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    depends_on: list[str] = Field(default_factory=list)
    checkpoint: str | None = None


class ResearchFinding(BaseModel):
    id: str
    statement: str
    interpretation: str
    confidence: int = Field(ge=0, le=100)
    citations: list[Citation] = Field(min_length=1)


class AutonomousReport(BaseModel):
    project_id: UUID
    question: str
    company: Company
    language: Language
    executive_summary: str
    findings: list[ResearchFinding]
    bull_case: str
    bear_case: str
    supply_chain_implications: list[str]
    uncertainties: list[str]
    monitoring_plan: list[str]
    source_run_id: UUID
    rendered_markdown: str
    disclaimer: str


class AutonomousProjectView(BaseModel):
    id: UUID
    question: str
    company: Company
    language: Language
    status: Literal["queued", "running", "paused", "awaiting_retry", "completed", "failed", "cancelled"]
    current_step: str | None
    progress: int
    error: str | None
    plan: list[ProjectTask]
    budget: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AutonomousTrace(BaseModel):
    project_id: UUID
    events: list[RunEvent]
    checkpoints: list[str]
    completed_steps: list[str]
    tool_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    duration_ms: int


class HarnessVariant(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    label: str = Field(min_length=2, max_length=80)
    model: str = "deterministic"
    prompt_version: str = "v1"
    skills: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(20, ge=1, le=100)
    citation_audit: bool = True
    critic_enabled: bool = True


class CreateArena(BaseModel):
    name: str = Field(min_length=3, max_length=240)
    company: Company
    fiscal_period: str = Field(pattern=r"^FY\d{4}-Q[1-4]$")
    variants: list[HarnessVariant] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def unique_variants(self):
        if len({item.id for item in self.variants}) != len(self.variants):
            raise ValueError("variant IDs must be unique")
        return self


class ArenaMetric(BaseModel):
    name: str
    value: float
    unit: str


class ArenaResult(BaseModel):
    variant: HarnessVariant
    passed: bool
    quality_score: float = Field(ge=0, le=100)
    metrics: list[ArenaMetric]
    trajectory: list[str]
    failure_reasons: list[str] = Field(default_factory=list)


class ArenaWinner(BaseModel):
    variant_id: str
    rationale: str


class ArenaView(BaseModel):
    id: UUID
    name: str
    dataset: str
    status: RunStatus
    progress: int
    results: list[ArenaResult]
    winner: ArenaWinner | None
    events: list[RunEvent]
    created_at: datetime
    updated_at: datetime
