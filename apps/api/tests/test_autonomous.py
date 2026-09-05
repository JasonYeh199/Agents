from fastapi.testclient import TestClient

from app.main import app


def test_autonomous_project_end_to_end():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/autonomous-projects",
            json={
                "question": "Do the verified NVIDIA earnings facts support continued AI infrastructure growth?",
                "company": "nvidia",
                "fiscal_period": "FY2025-Q4",
                "language": "en",
            },
        )
        assert created.status_code == 202
        project_id = created.json()["id"]
        project = client.get(f"/api/v1/autonomous-projects/{project_id}").json()
        assert project["status"] == "completed"
        assert len(project["plan"]) == 6
        assert all(task["status"] == "completed" for task in project["plan"])
        report = client.get(f"/api/v1/autonomous-projects/{project_id}/report").json()
        assert report["findings"]
        assert all(finding["citations"] for finding in report["findings"])
        assert report["bull_case"] and report["bear_case"]
        trace = client.get(f"/api/v1/autonomous-projects/{project_id}/trace").json()
        assert len(trace["checkpoints"]) == 6
        assert trace["tool_calls"] == 6
        evaluation = client.post(f"/api/v1/autonomous-projects/{project_id}/evaluate").json()
        assert evaluation["passed"] is True


def test_autonomous_project_budget_guard():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/autonomous-projects",
            json={
                "question": "Complete a bounded NVIDIA evidence research project with a deliberately low budget.",
                "company": "nvidia",
                "fiscal_period": "FY2025-Q4",
                "language": "en",
                "config": {"max_tool_calls": 5},
            },
        ).json()
        project = client.get(f"/api/v1/autonomous-projects/{created['id']}").json()
        assert project["status"] == "awaiting_retry"
        assert "budget exceeded" in project["error"]
