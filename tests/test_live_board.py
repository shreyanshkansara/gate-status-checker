from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
from unittest.mock import patch, MagicMock
import pytest
import httpx
from starlette.testclient import TestClient

from backend.main import app
from backend.services.gate_status import (
    estimate_gate_status,
    get_live_delay,
    RailRadarError,
    RailRadarRateLimitError,
    RailRadarTimeoutError,
)
from backend.services.live_board import (
    get_live_station_board,
    filter_local_trains_for_segment,
)
from backend.services.schedule_filter import (
    get_candidate_trains_from_live,
    load_cached_local_trains,
    StaleScheduleError,
)
from backend.scripts.fetch_schedule_cache import fetch_and_cache_local_trains


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_gate():
    return {
        "id": "gate30_31",
        "name": "LC Gate No. 30 & 31, Khandala",
        "represents": ["Gate No. 30", "Gate No. 31"],
        "near_station": "KAD",
        "far_station": "LNL",
        "distance_from_near_km": 1.1,
        "distance_is_estimated": True,
        "distance_source": "visual_proportion_estimate",
        "avg_speed_kmph": 36.0,
    }


# ==============================================================================
# Requirement (a): Live board candidate with delay attached does NOT call get_live_delay
# ==============================================================================
@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_skips_live_delay_for_live_board_candidates(mock_get_live_delay, sample_gate):
    """
    Candidates sourced from the live station board carry '_delay_source': 'live_board'
    and their delay is already fresh. estimate_gate_status must NOT call get_live_delay
    for these candidates.
    """
    current_dt = datetime(2026, 9, 7, 15, 10)
    candidate = {
        "train": {"number": "20495", "name": "Hadapsar SF Express"},
        "direction": "DOWN",
        "_direction": "DOWN",
        "_scheduled_time": "15:12",
        "_scheduled_time_kad": "15:12",
        "_delay_source": "live_board",
        "delay_minutes": 15,
        "_delay_minutes": 15,
        "_live_status": "upcoming",
    }

    result = estimate_gate_status(sample_gate, [candidate], current_dt)

    assert result["status"] in ("likely_open", "likely_closed")
    assert len(result["trains"]) == 1
    assert result["trains"][0]["train_number"] == "20495"
    assert result["trains"][0]["delay_minutes"] == 15
    # Assert get_live_delay was NOT called
    mock_get_live_delay.assert_not_called()
    assert result["_live_calls_made"] == 0


# ==============================================================================
# Requirement (b): Live board failure gracefully falls back to cached schedule
# ==============================================================================
@patch("backend.main.get_live_station_board")
@patch("backend.main.load_cached_schedule")
@patch("backend.main.get_candidate_trains")
@patch("backend.services.gate_status.get_live_delay")
def test_gate_status_live_board_failure_falls_back_to_cached_schedule(
    mock_get_live_delay, mock_get_candidates, mock_load_cached_schedule, mock_get_live_board, client
):
    """
    When get_live_station_board fails with RailRadarError, /gate/status must:
    - Fall back to load_cached_schedule
    - Set data_source to 'cached_fallback'
    - Retain represents, distance_is_estimated, and distance_source
    - Degrade gracefully without crashing
    """
    mock_get_live_board.side_effect = RailRadarError("RailRadar 500: Internal Server Error")
    mock_load_cached_schedule.return_value = {"cached_at": datetime.now(timezone.utc).isoformat(), "trains": []}
    mock_get_candidates.return_value = [
        {
            "train": {"number": "11008", "name": "Deccan Express"},
            "direction": "UP",
            "_scheduled_time": "15:30",
            "_scheduled_time_kad": "15:30",
            "_scheduled_time_lnl": "15:20",
        }
    ]
    mock_get_live_delay.return_value = {"train_number": "11008", "delay_minutes": 0, "status": "running"}

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["data_source"] == "cached_fallback"
    assert data["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert data["distance_is_estimated"] is True
    assert data["distance_source"] == "visual_proportion_estimate"
    assert data["distance_from_near_km"] == 1.1
    assert len(data["trains"]) == 1
    assert data["trains"][0]["train_number"] == "11008"
    # Fallback candidate triggers get_live_delay
    mock_get_live_delay.assert_called_once_with("11008", api_key=None)


# ==============================================================================
# Requirement (c): Suburban-local candidate not in live board DOES trigger get_live_delay
# ==============================================================================
@patch("backend.main.get_live_station_board")
@patch("backend.main.get_candidate_trains_from_live")
@patch("backend.main.load_cached_local_trains")
@patch("backend.services.gate_status.get_live_delay")
def test_suburban_local_candidate_triggers_get_live_delay(
    mock_get_live_delay, mock_load_local, mock_candidates_live, mock_get_live_board, client
):
    """
    A suburban/local train from the 14-day cached file that was NOT in the live board:
    - Merges into candidate list tagged _source='suburban_local'
    - Does NOT carry _delay_source='live_board'
    - DOES call get_live_delay (since its cached schedule is not minute-fresh)
    """
    # Live board returns empty or different train
    mock_get_live_board.return_value = {"success": True, "data": {"trains": []}}
    mock_candidates_live.return_value = []

    # Suburban local cache returns a local train relevant to Khandala
    mock_load_local.return_value = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "99813": "Lonavala - Pune Local",
        },
    }
    # Local candidate needs live delay
    mock_get_live_delay.return_value = {
        "train_number": "99813",
        "delay_minutes": 2,
        "status": "running",
    }

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["data_source"] == "live"
    # Suburban local train should be present in evaluated trains
    assert len(data["trains"]) == 1
    assert data["trains"][0]["train_number"] == "99813"
    mock_get_live_delay.assert_called_once_with("99813", api_key=None)


# ==============================================================================
# Requirement (d): fetch_schedule_cache.py writes expected cached_at + wrapper
# ==============================================================================
@patch("httpx.Client.get")
def test_fetch_and_cache_local_trains_structure(mock_get):
    """
    fetch_and_cache_local_trains must save to schedule_cache_local_mumbai.json
    with {"cached_at": <iso timestamp>, ...} wrapper.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "success": True,
        "data": {
            "95101": "S5 / Mumbai CSMT - Karjat Fast Local",
            "99813": "Lonavala - Pune Local",
            "90001": "Bandra - Borivali Local",  # Non-corridor train
        },
    }
    mock_get.return_value = mock_resp

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("backend.scripts.fetch_schedule_cache.DATA_DIR", tmp_path):
            success = fetch_and_cache_local_trains("test_api_key", city="Mumbai")
            assert success is True

            cache_file = tmp_path / "schedule_cache_local_mumbai.json"
            assert cache_file.exists()

            with open(cache_file, "r", encoding="utf-8") as f:
                saved = json.load(f)

            assert "cached_at" in saved
            assert saved["city"] == "Mumbai"
            assert saved["success"] is True
            # Verified corridor filtering kept Karjat and Lonavala locals
            assert "95101" in saved["data"]
            assert "99813" in saved["data"]


# ==============================================================================
# Requirement (e): Zero candidates from live board logs EXACTLY 1 RailRadar call
# ==============================================================================
@patch("backend.main.get_live_station_board")
@patch("backend.main.get_candidate_trains_from_live")
@patch("backend.main.load_cached_local_trains")
@patch("backend.main.logger")
def test_zero_candidates_live_board_logs_one_call(
    mock_logger, mock_load_local, mock_candidates_live, mock_get_live_board, client
):
    """
    When live station board is queried (1 call) and yields 0 candidates:
    - Status is 'likely_open'
    - Total calls logged must be exactly 1 (not 0, not 2+)
    """
    mock_get_live_board.return_value = {"success": True, "data": {"trains": []}}
    mock_candidates_live.return_value = []
    mock_load_local.return_value = {"cached_at": datetime.now(timezone.utc).isoformat(), "data": {}}

    response = client.get("/gate/status")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "likely_open"
    assert data["data_source"] == "live"
    assert data["trains"] == []

    # Exactly 1 call was made (the live board)
    mock_get_live_board.assert_called_once()
    mock_logger.info.assert_any_call(
        "[REQUEST /gate/status] Made 1 RailRadar live API call(s) (live station board; no candidate trains in +/- 30m window)"
    )


# ==============================================================================
# Unit tests for filter_local_trains_for_segment
# ==============================================================================
def test_filter_local_trains_for_segment_pure_function():
    """Tests pure filtering without mocking."""
    dict_input = {
        "95101": "Mumbai CSMT - Karjat Fast Local",   # Karjat (not KAD/LNL)
        "99801": "Pune - Lonavala Local",             # Matches LNL
        "99901": "Khandala - Karjat Shuttle",         # Matches KAD
        "90001": "Churchgate - Borivali Local",       # Western line
    }
    filtered = filter_local_trains_for_segment(dict_input, near_station="KAD", far_station="LNL")
    train_nums = [t["train_number"] for t in filtered]

    assert "99801" in train_nums
    assert "99901" in train_nums
    assert "95101" not in train_nums
    assert "90001" not in train_nums
    for t in filtered:
        assert t["_source"] == "suburban_local"


# ==============================================================================
# Unit tests for load_cached_local_trains
# ==============================================================================
def test_load_cached_local_trains_staleness():
    """Missing or older than 14 days raises StaleScheduleError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        # Missing file
        with pytest.raises(StaleScheduleError):
            load_cached_local_trains(data_dir=tmp_dir)

        # Stale file (> 14 days)
        stale_file = tmp_dir / "schedule_cache_local_mumbai.json"
        stale_time = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        with open(stale_file, "w", encoding="utf-8") as f:
            json.dump({"cached_at": stale_time, "data": {}}, f)

        with pytest.raises(StaleScheduleError):
            load_cached_local_trains(data_dir=tmp_dir, max_age_days=14)
