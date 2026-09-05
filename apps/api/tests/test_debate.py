from fastapi.testclient import TestClient

from app.main import app


def test_research_debate_end_to_end():
    with TestClient(app) as client:
        run = client.post(
            "/api/v1/research-runs",
            json={"company": "nvidia", "fiscal_period": "FY2025-Q4", "output_language": "en"},
        ).json()
        created = client.post(
            "/api/v1/research-debates",
            json={
                "topic": "Does the cited earnings evidence support continued AI growth?",
                "source_run_id": run["id"],
                "language": "en",
                "rebuttal_rounds": 1,
            },
        )
        assert created.status_code == 202
        debate_id = created.json()["id"]
        debate = client.get(f"/api/v1/research-debates/{debate_id}").json()
        assert debate["status"] == "completed"
        assert debate["verdict"]["decision"] == "watch"
        assert {turn["role"] for turn in debate["transcript"]} == {
            "bull", "bear", "pm", "critic"
        }
        assert any(turn["turn_type"] == "rebuttal" for turn in debate["transcript"])
        assert all(turn["evidence"] for turn in debate["transcript"])
        trace = client.get(f"/api/v1/research-debates/{debate_id}/trace").json()
        assert trace["tool_calls"] == 6
        assert len(trace["agents"]) == 6
        evaluation = client.post(f"/api/v1/research-debates/{debate_id}/evaluate").json()
        assert evaluation["passed"] is True
        assert len(evaluation["metrics"]) == 5


def test_debate_rejects_nonexistent_source():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/research-debates",
            json={
                "topic": "Should this unsupported claim pass?",
                "source_run_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert response.status_code == 409


def test_debate_rejects_encoding_replacement_characters():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/research-debates",
            json={
                "topic": "?????????? AI ?????",
                "source_run_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert response.status_code == 422
