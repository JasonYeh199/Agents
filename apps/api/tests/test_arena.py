from fastapi.testclient import TestClient

from app.main import app

VARIANTS = [
    {"id": "strict", "label": "Evidence-first", "skills": ["citation-auditor"], "citation_audit": True, "critic_enabled": True, "max_tool_calls": 20},
    {"id": "fast", "label": "Fast baseline", "skills": [], "citation_audit": False, "critic_enabled": False, "max_tool_calls": 10},
]


def test_evaluation_arena_compares_same_dataset():
    with TestClient(app) as client:
        created = client.post("/api/v1/evaluation-arenas", json={"name": "Harness comparison", "company": "nvidia", "fiscal_period": "FY2025-Q4", "variants": VARIANTS})
        assert created.status_code == 202
        arena = client.get(f"/api/v1/evaluation-arenas/{created.json()['id']}").json()
        assert arena["status"] == "completed"
        assert arena["dataset"] == "nvidia-FY2025-Q4"
        assert len(arena["results"]) == 2
        assert arena["winner"]["variant_id"] == "strict"
        strict, fast = arena["results"]
        assert strict["passed"] is True
        assert fast["passed"] is False
        assert strict["quality_score"] > fast["quality_score"]
        assert fast["failure_reasons"]


def test_arena_requires_unique_variant_ids():
    with TestClient(app) as client:
        duplicate = [VARIANTS[0], {**VARIANTS[1], "id": "strict"}]
        response = client.post("/api/v1/evaluation-arenas", json={"name": "Invalid comparison", "company": "nvidia", "fiscal_period": "FY2025-Q4", "variants": duplicate})
        assert response.status_code == 422
