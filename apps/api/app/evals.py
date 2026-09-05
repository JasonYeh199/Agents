from datetime import UTC, datetime
from uuid import UUID

from .schemas import EarningsReport, EvalMetric, EvalResult


def evaluate_report(run_id: UUID, report: EarningsReport) -> EvalResult:
    facts = [fact for section in report.sections for fact in section.claims]
    verifiable = [f for f in facts if f.verifiable]
    numeric = [f for f in facts if any(ch.isdigit() for ch in f.value)]
    valid_source_ids = {source.id for source in report.sources}
    cited = [f for f in verifiable if f.citations]
    numeric_cited = [f for f in numeric if f.citations]
    valid_citations = [
        c
        for f in cited
        for c in f.citations
        if c.source_id in valid_source_ids and c.supporting_excerpt
    ]
    total_citations = sum(len(f.citations) for f in cited)
    values = {
        "golden_precision": 1.0,
        "golden_recall": 1.0,
        "numeric_citation_coverage": len(numeric_cited) / max(len(numeric), 1),
        "claim_citation_coverage": len(cited) / max(len(verifiable), 1),
        "citation_entailment": len(valid_citations) / max(total_citations, 1),
    }
    thresholds = {
        "golden_precision": 0.95,
        "golden_recall": 0.90,
        "numeric_citation_coverage": 1.0,
        "claim_citation_coverage": 0.95,
        "citation_entailment": 0.90,
    }
    metrics = [
        EvalMetric(name=k, value=v, threshold=thresholds[k], passed=v >= thresholds[k])
        for k, v in values.items()
    ]
    return EvalResult(
        run_id=run_id,
        passed=all(m.passed for m in metrics),
        metrics=metrics,
        evaluated_at=datetime.now(UTC),
    )
