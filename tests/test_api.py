from datetime import datetime, timezone
import json
from unittest.mock import patch, MagicMock
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.services.schedule_filter import StaleScheduleError


@pytest.fixture
def client():
    return TestClient(app)


# --- Tests for GET /gate ---

def test_get_gate_returns_single_gate(client):
    """GET /gate must return single gate's id, name, represents list and distance info."""
    response = client.get("/gate")
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == "gate30_31"
    assert data["name"] == "LC Gate No. 30 & 31, Khandala"
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["near_station"] == "KAD"
    assert data["far_station"] == "LNL"
    assert data["distance_from_near_km"] == 1.1
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"


# --- Tests for GET /gate/status ---

@patch("backend.main.get_candidate_trains")
@patch("backend.main.load_cached_schedule")
@patch("backend.main.logger")
def test_get_gate_status_no_candidates_zero_api_calls(mock_logger, mock_load, mock_candidates, client):
    """
    When no candidate trains are found in the window:
    - Status is 'likely_open'
    - ZERO RailRadar live calls are made
    - represents, distance_is_estimated, and distance_source are present
    """
    mock_load.return_value = {"from_station": "KAD", "to_station": "LNL", "trains": []}
    mock_candidates.return_value = []

    with patch("backend.services.gate_status.get_live_delay") as mock_live:
        response = client.get("/gate/status")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "likely_open"
        assert data["trains"] == []
        assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
        assert data["distance_is_estimated"] is True
        assert data["distance_source"] == "visual_proportion_estimate"
        assert data["distance_from_near_km"] == 1.1

        # Assert zero RailRadar calls were made
        mock_live.assert_not_called()

        # Assert call count 0 logged
        mock_logger.info.assert_any_call(
            "[REQUEST /gate/status] Made 0 RailRadar live API call(s) (no candidate trains in +/- 30m window)"
        )


@patch("backend.main.get_candidate_trains")
@patch("backend.main.load_cached_schedule")
@patch("backend.services.gate_status.get_live_delay")
@patch("backend.main.logger")
def test_get_gate_status_with_candidates_calls_railradar(mock_logger, mock_live, mock_load, mock_candidates, client):
    """
    When candidate trains exist:
    - Calls RailRadar once per candidate
    - Returns structured gate status with represents and distance metadata
    """
    mock_load.return_value = {"from_station": "KAD", "to_station": "LNL"}
    mock_candidates.return_value = [
        {
            "train": {"number": "12124", "name": "Deccan Queen"},
            "_scheduled_time": "15:30",
            "from": {"code": "KAD", "departure": "15:30"},
        }
    ]
    mock_live.return_value = {
        "train_number": "12124",
        "delay_minutes": 5,
        "status": "running",
    }

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] in ("likely_open", "likely_closed")
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"
    assert len(data["trains"]) == 1
    assert data["trains"][0]["train_number"] == "12124"

    # Exactly 1 call made for 1 candidate
    assert mock_live.call_count == 1


@patch("backend.main.load_cached_schedule")
@patch("backend.main.logger")
def test_get_gate_status_stale_schedule_handling(mock_logger, mock_load, client):
    """
    When schedule is stale or missing:
    - Returns status 'schedule_stale' with clear instructions
    - Zero RailRadar live calls
    - Preserves represents, distance_is_estimated, and distance_source
    """
    mock_load.side_effect = StaleScheduleError("Schedule cache older than 14 days")

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "schedule_stale"
    assert "instructions" in data
    assert "fetch_schedule_cache.py" in data["instructions"]
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"
    assert data["distance_from_near_km"] == 1.1

    mock_logger.info.assert_any_call(
        "[REQUEST /gate/status] Made 0 RailRadar live API call(s) (schedule cache stale/missing)"
    )
