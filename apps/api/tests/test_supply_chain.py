from fastapi.testclient import TestClient

from app.main import app
from app.schemas import CreateInvestigation, EvidenceGraph, InvestigationReport
from app.supply_chain import evaluate_investigation, load_fixture


def test_investigation_input_contract():
    request = CreateInvestigation(
        signal_type="capacity_bottleneck",
        subject="CoWoS",
        time_window="2024-Q4 to 2025-Q2",
    )
    assert request.config.max_tool_calls == 30


def test_fixture_edges_have_valid_direction_and_primary_evidence():
    fixture = load_fixture()
    nodes = {node["id"] for node in fixture["nodes"]}
    for edge in fixture["edges"]:
        assert edge["source_entity_id"] in nodes
        assert edge["target_entity_id"] in nodes
        assert edge["source_ids"]
        if edge["type"] == "benefits_from":
            assert edge["inference_level"] == "derived"


def test_supply_chain_api_end_to_end():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/supply-chain-investigations",
            json={
                "signal_type": "capacity_bottleneck",
                "subject": "AI accelerators / CoWoS",
                "time_window": "2024-Q4 to 2025-Q2",
                "question": "Which public companies have evidence-backed exposure?",
                "language": "en",
            },
        )
        assert created.status_code == 202
        investigation_id = created.json()["id"]
        status = client.get(f"/api/v1/supply-chain-investigations/{investigation_id}")
        assert status.json()["status"] == "completed"
        assert len(status.json()["tasks"]) == 7

        graph_response = client.get(f"/api/v1/supply-chain-investigations/{investigation_id}/graph")
        graph = EvidenceGraph.model_validate(graph_response.json())
        assert graph_response.status_code == 200
        assert all(edge.evidence for edge in graph.edges)
        assert any(edge.type == "benefits_from" for edge in graph.edges)

        report_response = client.get(f"/api/v1/supply-chain-investigations/{investigation_id}/report")
        report = InvestigationReport.model_validate(report_response.json())
        assert {item.company_entity_id for item in report.candidates} == {"tsmc", "vertiv"}
        assert all(item.primary_source_count >= 2 for item in report.candidates)

        trace = client.get(f"/api/v1/supply-chain-investigations/{investigation_id}/trace").json()
        assert len(trace["agents"]) == 7
        assert trace["tool_calls"] > 0
        assert {agent["agent_role"] for agent in trace["agents"]} >= {"capacity", "supplier", "demand", "critic"}

        evaluation = client.post(f"/api/v1/supply-chain-investigations/{investigation_id}/evaluate")
        assert evaluation.status_code == 200
        assert evaluation.json()["passed"] is True


def test_tool_budget_fails_safely_and_is_retryable():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/supply-chain-investigations",
            json={
                "signal_type": "shortage",
                "subject": "HBM",
                "time_window": "2025",
                "config": {"max_tool_calls": 1},
            },
        )
        investigation_id = created.json()["id"]
        status = client.get(
            f"/api/v1/supply-chain-investigations/{investigation_id}"
        ).json()
        assert status["status"] == "awaiting_retry"
        assert "budget exceeded" in status["error"]
        assert client.get(
            f"/api/v1/supply-chain-investigations/{investigation_id}/graph"
        ).status_code == 404


def test_eval_rejects_missing_candidate_path():
    # The API integration test exercises the passing golden dataset. This asserts
    # the candidate path gate itself, independently of orchestration.
    graph = EvidenceGraph(nodes=[], edges=[], conflicts=[])
    report = InvestigationReport.model_construct(candidates=[], watchlist=[])
    result = evaluate_investigation(__import__("uuid").uuid4(), graph, report)
    assert next(m for m in result.metrics if m.name == "edge_citation_coverage").passed is False
