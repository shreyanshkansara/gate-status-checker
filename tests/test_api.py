from datetime import datetime, timezone, timedelta
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

@patch("backend.main.get_live_station_board")
@patch("backend.main.get_candidate_trains")
@patch("backend.main.load_cached_schedule")
@patch("backend.main.logger")
def test_get_gate_status_no_candidates_zero_api_calls(mock_logger, mock_load, mock_candidates, mock_live_board, client):
    """
    When live board fails and fallback cached schedule has no candidate trains:
    - Status is 'likely_open'
    - data_source is 'cached_fallback'
    - ZERO RailRadar live calls are made
    - represents, distance_is_estimated, and distance_source are present
    """
    from backend.services.gate_status import RailRadarError
    mock_live_board.side_effect = RailRadarError("Live board unavailable")
    mock_load.return_value = {"from_station": "KAD", "to_station": "LNL", "trains": []}
    mock_candidates.return_value = []

    with patch("backend.services.gate_status.get_live_delay") as mock_live:
        response = client.get("/gate/status")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "likely_open"
        assert data["data_source"] == "cached_fallback"
        assert data["trains"] == []
        assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
        assert data["distance_is_estimated"] is True
        assert data["distance_source"] == "visual_proportion_estimate"
        assert data["distance_from_near_km"] == 1.1
        assert "closure_window_past_min" in data
        assert "closure_window_future_min" in data
        assert "merge_threshold_min" in data
        assert "closed_intervals" in data

        # Assert zero RailRadar calls were made
        mock_live.assert_not_called()

        # Assert call count 0 logged
        mock_logger.info.assert_any_call(
            "[REQUEST /gate/status] Made 0 RailRadar live API call(s) (cached fallback; no candidate trains in +/- 30m window)"
        )


@patch("backend.main.get_live_station_board")
@patch("backend.main.get_candidate_trains")
@patch("backend.main.load_cached_schedule")
@patch("backend.services.gate_status.get_live_delay")
@patch("backend.main.logger")
def test_get_gate_status_with_candidates_calls_railradar(mock_logger, mock_live, mock_load, mock_candidates, mock_live_board, client):
    """
    When in cached_fallback mode and candidate trains exist:
    - Calls RailRadar live delay once per candidate
    - Returns structured gate status with represents, data_source, distance metadata, and closure window
    """
    from backend.services.gate_status import RailRadarError
    mock_live_board.side_effect = RailRadarError("Live board unavailable")
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
    assert data["data_source"] == "cached_fallback"
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"
    assert "closure_window_past_min" in data
    assert "closure_window_future_min" in data
    assert "merge_threshold_min" in data
    assert "closed_intervals" in data
    assert len(data["trains"]) == 1
    assert data["trains"][0]["train_number"] == "12124"

    # Exactly 1 call made for 1 candidate
    assert mock_live.call_count == 1


@patch("backend.main.get_live_station_board")
@patch("backend.main.load_cached_schedule")
@patch("backend.main.logger")
def test_get_gate_status_stale_schedule_handling(mock_logger, mock_load, mock_live_board, client):
    """
    When live board fails and schedule is stale or missing:
    - Returns status 'schedule_stale' with clear instructions
    - Zero RailRadar live calls
    - Preserves represents, distance_is_estimated, distance_source, and closure window
    """
    from backend.services.gate_status import RailRadarError
    mock_live_board.side_effect = RailRadarError("Live board unavailable")
    mock_load.side_effect = StaleScheduleError("Schedule cache older than 14 days")

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "schedule_stale"
    assert data["data_source"] == "cached_fallback"
    assert "instructions" in data
    assert "fetch_schedule_cache.py" in data["instructions"]
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"
    assert data["distance_from_near_km"] == 1.1
    assert "closure_window_past_min" in data
    assert "closure_window_future_min" in data
    assert "merge_threshold_min" in data
    assert "closed_intervals" in data

    mock_logger.info.assert_any_call(
        "[REQUEST /gate/status] Made %d RailRadar live API call(s) (schedule cache stale/missing)",
        0,
    )


# --- Tests for Settings Endpoints & Integration ---

def test_get_settings_dual_routes(client, monkeypatch, tmp_path):
    """GET /settings and GET /api/settings return the current settings."""
    monkeypatch.setattr("backend.main.DATA_DIR", tmp_path)
    # Default settings on missing file
    res1 = client.get("/settings")
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["closure_window_future_min"] == 2
    assert d1["closure_window_past_min"] == 3
    assert d1["train_merge_threshold_min"] == 10

    res2 = client.get("/api/settings")
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["closure_window_future_min"] == d1["closure_window_future_min"]
    assert d2["closure_window_past_min"] == d1["closure_window_past_min"]
    assert d2["train_merge_threshold_min"] == d1["train_merge_threshold_min"]


def test_post_settings_dual_routes_and_persistence(client, monkeypatch, tmp_path):
    """POST /settings and POST /api/settings update settings and persist to JSON."""
    monkeypatch.setattr("backend.main.DATA_DIR", tmp_path)

    # POST to /settings
    res1 = client.post(
        "/settings",
        json={
            "closure_window_future_min": 6,
            "closure_window_past_min": 8,
            "train_merge_threshold_min": 15,
        },
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["closure_window_future_min"] == 6
    assert d1["closure_window_past_min"] == 8
    assert d1["train_merge_threshold_min"] == 15

    # Verify GET /settings reflects it
    get_res = client.get("/settings")
    assert get_res.json()["closure_window_future_min"] == 6
    assert get_res.json()["closure_window_past_min"] == 8
    assert get_res.json()["train_merge_threshold_min"] == 15

    # POST to /api/settings
    res2 = client.post(
        "/api/settings",
        json={
            "closure_window_future_min": 4,
            "closure_window_past_min": 5,
            "train_merge_threshold_min": 12,
        },
    )
    assert res2.status_code == 200
    assert res2.json()["closure_window_future_min"] == 4
    assert res2.json()["closure_window_past_min"] == 5
    assert res2.json()["train_merge_threshold_min"] == 12


@pytest.mark.parametrize("payload,expected_err", [
    ({"closure_window_future_min": 0, "closure_window_past_min": 3}, "closure_window_future_min must be between 1 and 15"),
    ({"closure_window_future_min": 20, "closure_window_past_min": 3}, "closure_window_future_min must be between 1 and 15"),
    ({"closure_window_future_min": 2, "closure_window_past_min": 16}, "closure_window_past_min must be between 1 and 15"),
    ({"train_merge_threshold_min": 0}, "train_merge_threshold_min must be between 1 and 20"),
    ({"train_merge_threshold_min": 21}, "train_merge_threshold_min must be between 1 and 20"),
    ({"closure_window_future_min": "abc", "closure_window_past_min": 3}, "closure_window_future_min must be between 1 and 15"),
    ({"closure_window_future_min": True, "closure_window_past_min": 3}, "closure_window_future_min must be between 1 and 15"),
    ({"train_merge_threshold_min": False}, "train_merge_threshold_min must be between 1 and 20"),
])
def test_post_settings_invalid_input_returns_400(client, monkeypatch, tmp_path, payload, expected_err):
    """POST /settings returns 400 with the ValueError message on invalid inputs."""
    monkeypatch.setattr("backend.main.DATA_DIR", tmp_path)
    res = client.post("/settings", json=payload)
    assert res.status_code == 400
    assert expected_err in res.json()["detail"]


@patch("backend.main.get_live_station_board")
@patch("backend.main.load_cached_schedule")
def test_gate_status_reflects_updated_settings_and_causes_closure(
    mock_load, mock_live_board, client, monkeypatch, tmp_path
):
    """
    POSTing new settings changes what the NEXT /gate/status call returns and uses for
    causes_closure calculation.
    """
    monkeypatch.setattr("backend.main.DATA_DIR", tmp_path)

    # Initial settings: future = 2
    client.post("/settings", json={"closure_window_future_min": 2, "closure_window_past_min": 3})

    from backend.services.gate_status import RailRadarError
    mock_live_board.side_effect = RailRadarError("Live board unavailable")
    mock_load.return_value = {"from_station": "KAD", "to_station": "LNL"}

    # Simulate a train arriving ~8 minutes from now
    now = datetime.now()
    train_dep_time = (now + timedelta(minutes=6)).strftime("%H:%M")

    with patch("backend.main.get_candidate_trains") as mock_cand, \
         patch("backend.services.gate_status.get_live_delay") as mock_live:
        mock_cand.return_value = [
            {
                "train": {"number": "12124", "name": "Deccan Queen"},
                "_scheduled_time": train_dep_time,
                "direction": "DOWN",
                "from": {"code": "KAD", "departure": train_dep_time},
            }
        ]
        mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}

        # 1. Check with future = 2: train 8m out does NOT cause closure
        res1 = client.get("/gate/status")
        assert res1.status_code == 200
        d1 = res1.json()
        assert d1["closure_window_future_min"] == 2
        assert d1["closure_window_past_min"] == 3
        assert d1["status"] == "likely_open"
        assert d1["trains"][0]["causes_closure"] is False

        # 2. Update future window to 10
        post_res = client.post("/settings", json={"closure_window_future_min": 10, "closure_window_past_min": 3})
        assert post_res.status_code == 200

        # 3. Next /gate/status: same train now causes closure!
        res2 = client.get("/gate/status")
        assert res2.status_code == 200
        d2 = res2.json()
        assert d2["closure_window_future_min"] == 10
        assert d2["closure_window_past_min"] == 3
        assert d2["status"] == "likely_closed"
        assert d2["trains"][0]["causes_closure"] is True


@patch("backend.main.get_live_station_board")
@patch("backend.main.load_cached_schedule")
def test_post_settings_threshold_changes_next_gate_status_closure(
    mock_load, mock_live_board, client, monkeypatch, tmp_path
):
    """
    POSTing a different train_merge_threshold_min changes whether two trains whose
    individual windows leave an open gap are merged into a continuous likely_closed status.
    """
    monkeypatch.setattr("backend.main.DATA_DIR", tmp_path)

    # Initial settings: default threshold = 10
    client.post("/settings", json={"train_merge_threshold_min": 10})

    from backend.services.gate_status import RailRadarError
    mock_live_board.side_effect = RailRadarError("Live board unavailable")
    mock_load.return_value = {"from_station": "KAD", "to_station": "LNL"}

    now = datetime.now()
    # Train 1: departure in 1 min -> ETA at gate ~2.8m (window [0.8m, 5.8m])
    # Train 2: departure in 11 min -> ETA at gate ~12.8m (window [10.8m, 15.8m])
    # Gap between Train 1 end (5.8m) and Train 2 start (10.8m) is ~5.0 min.
    # Check at now + 8 min falls in that gap.
    t1_dep = (now + timedelta(minutes=1)).strftime("%H:%M")
    t2_dep = (now + timedelta(minutes=11)).strftime("%H:%M")

    with patch("backend.main.get_candidate_trains") as mock_cand, \
         patch("backend.services.gate_status.get_live_delay") as mock_live:
        mock_cand.return_value = [
            {
                "train": {"number": "12124", "name": "Deccan Queen"},
                "_scheduled_time": t1_dep,
                "direction": "DOWN",
                "from": {"code": "KAD", "departure": t1_dep},
            },
            {
                "train": {"number": "22222", "name": "Vande Bharat"},
                "_scheduled_time": t2_dep,
                "direction": "DOWN",
                "from": {"code": "KAD", "departure": t2_dep},
            },
        ]
        mock_live.return_value = {"delay_minutes": 0, "status": "running"}

        # 1. At default threshold=10: gap (~5m <= 10m) merges into closed interval!
        with patch("backend.main.datetime") as mock_dt:
            # Check at 7 min from now (inside the gap)
            check_time = now + timedelta(minutes=7)
            mock_dt.now.return_value = check_time
            res1 = client.get("/gate/status")
            assert res1.status_code == 200
            d1 = res1.json()
            assert d1["status"] == "likely_closed"
            assert d1["merge_threshold_min"] == 10
            assert len(d1["closed_intervals"]) == 1
            assert d1["closed_intervals"][0]["is_merged"] is True

        # 2. Update threshold to 3 (gap ~5m > 3m -> no merge)
        post_res = client.post("/settings", json={"train_merge_threshold_min": 3})
        assert post_res.status_code == 200

        # 3. Next /gate/status at same check time: now likely_open!
        with patch("backend.main.datetime") as mock_dt:
            check_time = now + timedelta(minutes=7)
            mock_dt.now.return_value = check_time
            res2 = client.get("/gate/status")
            assert res2.status_code == 200
            d2 = res2.json()
            assert d2["status"] == "likely_open"
            assert d2["merge_threshold_min"] == 3
            assert len(d2["closed_intervals"]) == 2
            assert all(not g["is_merged"] for g in d2["closed_intervals"])



