from fastapi.testclient import TestClient

from app.main import app


def test_api_contract_and_complete_run():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/research-runs",
            json={
                "company": "nvidia",
                "fiscal_period": "FY2025-Q4",
                "output_language": "en",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert client.get(f"/api/v1/research-runs/{run_id}").json()["status"] == "completed"
        report = client.get(f"/api/v1/research-runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["language"] == "en"
        trace = client.get(f"/api/v1/research-runs/{run_id}/trace")
        assert trace.status_code == 200
        assert trace.json()["tool_calls"] > 0
        evaluation = client.post(f"/api/v1/research-runs/{run_id}/evaluate")
        assert evaluation.status_code == 200
        assert evaluation.json()["passed"] is True
