import pytest
from pydantic import ValidationError

from app.schemas import CreateRun, Fact


def test_period_schema():
    assert CreateRun(company="nvidia", fiscal_period="FY2025-Q4").fiscal_period == "FY2025-Q4"
    with pytest.raises(ValidationError):
        CreateRun(company="nvidia", fiscal_period="2025Q4")


def test_verified_fact_requires_citation():
    with pytest.raises(ValidationError):
        Fact(id="x", category="metrics", label="Revenue", value="1", period="FY2025-Q4")
