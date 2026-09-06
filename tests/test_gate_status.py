from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest
import httpx

from backend.services.gate_status import (
    get_live_delay,
    estimate_gate_status,
    RailRadarError,
    RailRadarRateLimitError,
    RailRadarTimeoutError,
)


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
        "avg_speed_kmph": 36.0,  # KAD to gate: 1.1 km = 1.83 min. LNL to gate: 1.9 km = 3.17 min
    }


def make_candidate(train_number: str, departure_time: str, train_name: str = "Test Express", direction: str = "DOWN", lnl_time: str = None):
    return {
        "train": {"number": train_number, "name": train_name},
        "direction": direction,
        "_direction": direction,
        "_scheduled_time": departure_time,
        "_scheduled_time_kad": departure_time,
        "_scheduled_time_lnl": lnl_time,
        "from": {"code": "KAD", "departure": departure_time},
        "to": {"code": "LNL", "arrival": "15:40"},
    }


# --- Tests for get_live_delay ---

@patch("httpx.Client.get")
def test_get_live_delay_running(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "status": "running",
            "delayMinutes": 10,
        }
    }
    mock_get.return_value = mock_resp

    result = get_live_delay("12124", api_key="dummy_key")
    assert result["train_number"] == "12124"
    assert result["delay_minutes"] == 10
    assert result["status"] == "running"


@patch("httpx.Client.get")
def test_get_live_delay_not_started_defaults_zero(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "status": "not_started",
            "delayMinutes": None,
        }
    }
    mock_get.return_value = mock_resp

    result = get_live_delay("11008", api_key="dummy_key")
    assert result["delay_minutes"] == 0
    assert result["status"] == "not_started"


@patch("httpx.Client.get")
def test_get_live_delay_rate_limit_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests"
    mock_get.return_value = mock_resp

    with pytest.raises(RailRadarRateLimitError):
        get_live_delay("12124", api_key="dummy_key")


@patch("httpx.Client.get")
def test_get_live_delay_timeout_error(mock_get):
    mock_get.side_effect = httpx.ReadTimeout("Connection timed out")

    with pytest.raises(RailRadarTimeoutError):
        get_live_delay("12124", api_key="dummy_key")


# --- Tests for estimate_gate_status ---

def test_estimate_gate_status_no_candidates(sample_gate):
    """When candidate_trains is empty, status is likely_open."""
    curr_dt = datetime(2026, 9, 6, 12, 0)
    result = estimate_gate_status(sample_gate, [], curr_dt, api_key="dummy_key")

    assert result["status"] == "likely_open"
    assert result["represents"] == ["Gate No. 30", "Gate No. 31"]
    assert result["distance_is_estimated"] is True
    assert result["distance_source"] == "visual_proportion_estimate"
    assert result["distance_from_near_km"] == 1.1
    assert result["trains"] == []
    assert result["error"] is None


@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_candidate_outside_window(mock_live, sample_gate):
    """Candidate train arriving in +10 minutes (outside -3 to +2 min) -> likely_open."""
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 0)
    # KAD at 12:08 + transit 1.83m = ETA 12:09.83 (+9.83m from now)
    candidates = [make_candidate("12124", "12:08", direction="DOWN")]

    result = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="dummy_key")
    assert result["status"] == "likely_open"
    assert len(result["trains"]) == 1
    assert result["trains"][0]["causes_closure"] is False
    assert result["trains"][0]["minutes_from_now"] > 2.0


@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_down_approaching_inside_window(mock_live, sample_gate):
    """DOWN train arriving at gate in +1.83 minutes (within -3 to +2 min) -> likely_closed."""
    mock_live.return_value = {"train_number": "12701", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 0)
    # DOWN train reaches KAD at 12:00, transit to gate is 1.1km / 36 = 1.83 min -> ETA 12:01.83 (+1.83m)
    candidates = [make_candidate("12701", "12:00", direction="DOWN")]

    result = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="dummy_key")
    assert result["status"] == "likely_closed"
    assert len(result["trains"]) == 1
    assert result["trains"][0]["causes_closure"] is True
    assert result["trains"][0]["direction"] == "DOWN"
    assert -3.0 <= result["trains"][0]["minutes_from_now"] <= 2.0


@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_up_approaching_inside_window(mock_live, sample_gate):
    """
    UP train (Pune to Mumbai): arrives at LNL at 12:00.
    Gate distance from LNL is 3.0 - 1.1 = 1.9 km -> transit time 1.9 / 36 * 60 = 3.17 min.
    ETA at gate is 12:03.17.
    When current time is 12:02 (ETA is +1.17 min from now, inside [-3, +2]) -> likely_closed.
    """
    mock_live.return_value = {"train_number": "17412", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 2)
    candidates = [make_candidate("17412", "12:05", direction="UP", lnl_time="12:00")]

    result = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="dummy_key")
    assert result["status"] == "likely_closed"
    assert result["trains"][0]["causes_closure"] is True
    assert result["trains"][0]["direction"] == "UP"
    assert result["trains"][0]["reference_station"] == "LNL"
    assert -3.0 <= result["trains"][0]["minutes_from_now"] <= 2.0


@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_recently_passed_inside_tightened_window(mock_live, sample_gate):
    """Candidate train passed 2 minutes ago (within -3 to +2 min) -> likely_closed."""
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 3)
    # KAD at 12:00, transit 1.83m -> ETA 12:01.83 (1.17m ago relative to 12:03)
    candidates = [make_candidate("12124", "12:00", direction="DOWN")]

    result = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="dummy_key")
    assert result["status"] == "likely_closed"
    assert result["trains"][0]["causes_closure"] is True


@patch("backend.services.gate_status.get_live_delay")
def test_estimate_gate_status_passed_outside_tightened_window(mock_live, sample_gate):
    """
    Candidate train passed 4.5 minutes ago.
    Under the old (-7, +3) window it was likely_closed,
    but under tightened (-3, +2) window it is likely_open!
    """
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 6, 20)  # ~4.5m after 12:01.83
    candidates = [make_candidate("12124", "12:00", direction="DOWN")]

    result = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="dummy_key")
    assert result["status"] == "likely_open"
    assert result["trains"][0]["causes_closure"] is False


@patch("backend.services.gate_status.get_live_delay")
def test_represents_passthrough_unchanged(mock_live, sample_gate):
    """Assert represents list passes through unchanged in open, closed, and error states."""
    expected_represents = ["Gate No. 30", "Gate No. 31"]
    curr_dt = datetime(2026, 9, 6, 12, 0)

    # 1. Closed state
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    closed_res = estimate_gate_status(sample_gate, [make_candidate("12124", "12:00", direction="DOWN")], curr_dt, api_key="k")
    assert closed_res["represents"] == expected_represents

    # 2. Error state (Rate limit)
    mock_live.side_effect = RailRadarRateLimitError("Rate limit exceeded")
    err_res = estimate_gate_status(sample_gate, [make_candidate("12124", "12:00", direction="DOWN")], curr_dt, api_key="k")
    assert err_res["status"] == "status_unknown"
    assert err_res["represents"] == expected_represents
    assert err_res["distance_is_estimated"] is True
    assert err_res["distance_source"] == "visual_proportion_estimate"
    assert "Rate limit exceeded" in err_res["error"]


@patch("backend.services.gate_status.get_live_delay")
def test_single_call_per_candidate(mock_live, sample_gate):
    """Assert exactly one live delay call is made per candidate train."""
    mock_live.return_value = {"train_number": "1", "delay_minutes": 0, "status": "running"}
    candidates = [
        make_candidate("1001", "12:00"),
        make_candidate("1002", "12:05"),
        make_candidate("1003", "12:20"),
    ]
    curr_dt = datetime(2026, 9, 6, 12, 0)
    estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")

    assert mock_live.call_count == 3
    mock_live.assert_any_call("1001", api_key="k")
    mock_live.assert_any_call("1002", api_key="k")
    mock_live.assert_any_call("1003", api_key="k")
