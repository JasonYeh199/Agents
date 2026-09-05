from uuid import uuid4

from app.evals import evaluate_report
from app.schemas import Citation, EarningsReport, Fact, ReportSection, SourceDocument


def test_evaluation_gate_passes_fully_cited_report():
    source = SourceDocument(
        id="s",
        url="https://investor.nvidia.com/a",
        publisher="NVIDIA",
        document_type="release",
        published_at="2025-01-01T00:00:00Z",
        fetched_at="2025-01-01T00:00:00Z",
        sha256="a" * 64,
        object_key="a",
        language="en",
    )
    fact = Fact(
        id="revenue",
        category="metrics",
        label="Revenue",
        value="$1",
        period="FY2025-Q4",
        citations=[
            Citation(
                source_id="s",
                claim_id="revenue",
                locator="p:1",
                supporting_excerpt="Revenue was $1",
            )
        ],
    )
    report = EarningsReport(
        company="nvidia",
        fiscal_period="FY2025-Q4",
        language="en",
        executive_summary="Summary",
        sections=[ReportSection(title="Metrics", claims=[fact])],
        catalysts=[],
        risks=[],
        unverified=[],
        sources=[source],
        disclaimer="Not advice",
        canonical_facts_hash="x",
        rendered_markdown="# Report",
    )
    result = evaluate_report(uuid4(), report)
    assert result.passed
    assert all(m.passed for m in result.metrics)
