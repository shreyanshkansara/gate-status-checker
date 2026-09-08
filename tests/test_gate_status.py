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


@patch("backend.services.gate_status.get_live_delay")
def test_configurable_closure_window_affects_causes_closure(mock_live, sample_gate):
    """
    Train is arriving in ~8 minutes.
    At default future window (2 min), causes_closure is False -> likely_open.
    With future window set to 10 min, causes_closure is True -> likely_closed.
    """
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 0)
    # KAD at 12:06 + transit 1.83m = ETA 12:07.83 (~7.83 min from now)
    candidates = [make_candidate("12124", "12:06", direction="DOWN")]

    # Default window (past 3, future 2)
    res_default = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")
    assert res_default["status"] == "likely_open"
    assert res_default["closure_window_past_min"] == 3.0
    assert res_default["closure_window_future_min"] == 2.0
    assert res_default["trains"][0]["causes_closure"] is False

    # Custom extended window (future 10)
    res_custom = estimate_gate_status(
        sample_gate,
        candidates,
        curr_dt,
        api_key="k",
        closure_window_past_min=3.0,
        closure_window_future_min=10.0,
    )
    assert res_custom["status"] == "likely_closed"
    assert res_custom["closure_window_past_min"] == 3.0
    assert res_custom["closure_window_future_min"] == 10.0
    assert res_custom["trains"][0]["causes_closure"] is True


@patch("backend.services.gate_status.get_live_delay")
def test_closure_window_fields_present_in_all_shapes(mock_live, sample_gate):
    """closure_window_past_min and closure_window_future_min are included in all return shapes."""
    curr_dt = datetime(2026, 9, 6, 12, 0)

    # 1. No candidates -> likely_open
    res_empty = estimate_gate_status(
        sample_gate, [], curr_dt, api_key="k", closure_window_past_min=4.0, closure_window_future_min=5.0
    )
    assert res_empty["closure_window_past_min"] == 4.0
    assert res_empty["closure_window_future_min"] == 5.0

    # 2. Error -> status_unknown
    mock_live.side_effect = RailRadarRateLimitError("Rate limit")
    res_err = estimate_gate_status(
        sample_gate,
        [make_candidate("12124", "12:00", direction="DOWN")],
        curr_dt,
        api_key="k",
        closure_window_past_min=4.0,
        closure_window_future_min=5.0,
    )
    assert res_err["status"] == "status_unknown"
    assert res_err["closure_window_past_min"] == 4.0
    assert res_err["closure_window_future_min"] == 5.0
    assert res_err["merge_threshold_min"] == 10.0
    assert res_err["closed_intervals"] == []


@patch("backend.services.gate_status.get_live_delay")
def test_closely_spaced_trains_merge_in_gap(mock_live, sample_gate):
    """
    Two trains whose individual windows leave an 8-minute open gap.
    Train 1: reaches KAD at 12:00, ETA at gate ~12:01.83.
             Closure window: [11:59.83, 12:04.83]
    Train 2: reaches KAD at 12:11, ETA at gate ~12:12.83.
             Closure window: [12:10.83, 12:15.83]
    Gap between 12:04.83 and 12:10.83 is ~6.0 minutes.
    At current_dt = 12:07:
    - Neither train individually causes closure (causes_closure is False for both).
    - But with train_merge_threshold_min = 10 (default), the gap (6m <= 10m) merges!
    - Gate status is 'likely_closed' and closed_intervals has 1 entry with is_merged=True.
    - Each train has in_merged_group_with containing the other.
    """
    mock_live.return_value = {"train_number": "1", "delay_minutes": 0, "status": "running"}
    candidates = [
        make_candidate("12124", "12:00", direction="DOWN"),
        make_candidate("22222", "12:11", direction="DOWN"),
    ]
    check_dt = datetime(2026, 9, 6, 12, 7)

    # 1. With default merge_threshold_min = 10 -> merged closure!
    res_merged = estimate_gate_status(
        sample_gate,
        candidates,
        check_dt,
        api_key="k",
        train_merge_threshold_min=10.0,
    )
    assert res_merged["status"] == "likely_closed"
    assert res_merged["merge_threshold_min"] == 10.0
    assert len(res_merged["closed_intervals"]) == 1
    group = res_merged["closed_intervals"][0]
    assert group["is_merged"] is True
    assert set(group["train_numbers"]) == {"12124", "22222"}

    # Individual causes_closure remains False, but in_merged_group_with is set
    t1 = next(t for t in res_merged["trains"] if t["train_number"] == "12124")
    t2 = next(t for t in res_merged["trains"] if t["train_number"] == "22222")
    assert t1["causes_closure"] is False
    assert t2["causes_closure"] is False
    assert t1["in_merged_group_with"] == ["22222"]
    assert t2["in_merged_group_with"] == ["12124"]

    # 2. Lower threshold to 4 (gap 6.0m > 4m) -> gate opens in gap!
    res_unmerged = estimate_gate_status(
        sample_gate,
        candidates,
        check_dt,
        api_key="k",
        train_merge_threshold_min=4.0,
    )
    assert res_unmerged["status"] == "likely_open"
    assert res_unmerged["merge_threshold_min"] == 4.0
    assert len(res_unmerged["closed_intervals"]) == 2
    assert all(not g["is_merged"] for g in res_unmerged["closed_intervals"])
    t1_u = next(t for t in res_unmerged["trains"] if t["train_number"] == "12124")
    t2_u = next(t for t in res_unmerged["trains"] if t["train_number"] == "22222")
    assert t1_u["in_merged_group_with"] == []
    assert t2_u["in_merged_group_with"] == []


@patch("backend.services.gate_status.get_live_delay")
def test_isolated_train_unmerged(mock_live, sample_gate):
    """An isolated train far from others has is_merged=False and in_merged_group_with=[]."""
    mock_live.return_value = {"train_number": "12124", "delay_minutes": 0, "status": "running"}
    candidates = [make_candidate("12124", "12:00", direction="DOWN")]
    curr_dt = datetime(2026, 9, 6, 12, 1, 30)

    res = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")
    assert res["status"] == "likely_closed"
    assert len(res["closed_intervals"]) == 1
    assert res["closed_intervals"][0]["is_merged"] is False
    assert res["trains"][0]["in_merged_group_with"] == []


# --- Tests for Phase 11: Live, delay-adjusted sorting ---

@patch("backend.services.gate_status.get_live_delay")
def test_supporting_trains_sorted_by_live_delay_adjusted_eta_not_schedule(mock_live, sample_gate):
    """
    Train A scheduled earlier (12:00) with +30m delay -> ETA 12:31.83.
    Train B scheduled later (12:15) on time (0m delay) -> ETA 12:16.83.
    Input order: [Train A, Train B].
    Assert returned list places Train B before Train A based on live-adjusted ETA.
    """
    def fake_live(train_num, **kwargs):
        if train_num == "TrainA":
            return {"train_number": "TrainA", "delay_minutes": 30, "status": "running"}
        return {"train_number": "TrainB", "delay_minutes": 0, "status": "running"}

    mock_live.side_effect = fake_live

    curr_dt = datetime(2026, 9, 6, 12, 0)
    candidates = [
        make_candidate("TrainA", "12:00", train_name="Express A", direction="DOWN"),
        make_candidate("TrainB", "12:15", train_name="Express B", direction="DOWN"),
    ]

    res = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")
    train_nums = [t["train_number"] for t in res["trains"]]
    assert train_nums == ["TrainB", "TrainA"]
    assert res["trains"][0]["minutes_from_now"] < res["trains"][1]["minutes_from_now"]


@patch("backend.services.gate_status.get_live_delay")
def test_supporting_trains_sorted_ascending_natural_iteration_mismatch(mock_live, sample_gate):
    """
    Simulate the real screenshot bug where natural iteration order was ~29.8m, ~41.8m, ~3.8m.
    Assert the result is sorted ascending: ~3.8m, ~29.8m, ~41.8m.
    """
    # Departures:
    # T1: 12:28 + transit 1.83m = 12:29.83 (+29.8m from 12:00)
    # T2: 12:40 + transit 1.83m = 12:41.83 (+41.8m from 12:00)
    # T3: 12:02 + transit 1.83m = 12:03.83 (+3.8m from 12:00)
    mock_live.return_value = {"delay_minutes": 0, "status": "running"}
    curr_dt = datetime(2026, 9, 6, 12, 0)
    candidates = [
        make_candidate("T1", "12:28", direction="DOWN"),
        make_candidate("T2", "12:40", direction="DOWN"),
        make_candidate("T3", "12:02", direction="DOWN"),
    ]

    res = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")
    mins = [t["minutes_from_now"] for t in res["trains"]]
    assert mins == [3.8, 29.8, 41.8]
    assert [t["train_number"] for t in res["trains"]] == ["T3", "T1", "T2"]


@patch("backend.services.gate_status.get_live_delay")
def test_sorting_is_display_only_preserves_status_and_merge_logic(mock_live, sample_gate):
    """
    Assert that shuffling input candidate order does not affect causes_closure,
    in_merged_group_with, closed_intervals, or overall status.
    """
    def fake_live(train_num, **kwargs):
        return {"train_number": train_num, "delay_minutes": 0, "status": "running"}

    mock_live.side_effect = fake_live

    curr_dt = datetime(2026, 9, 6, 12, 0)
    cand1 = make_candidate("1001", "12:00", direction="DOWN") # ETA 12:01.83 (+1.8m -> causes_closure)
    cand2 = make_candidate("1002", "12:08", direction="DOWN") # ETA 12:09.83 (gap ~5m from 1001 -> merges)
    cand3 = make_candidate("1003", "12:45", direction="DOWN") # ETA 12:46.83 (isolated)

    # Run 1: original order [cand1, cand2, cand3]
    res1 = estimate_gate_status(sample_gate, [cand1, cand2, cand3], curr_dt, api_key="k")

    # Run 2: shuffled order [cand3, cand1, cand2]
    res2 = estimate_gate_status(sample_gate, [cand3, cand1, cand2], curr_dt, api_key="k")

    assert res1["status"] == res2["status"]
    assert res1["closed_intervals"] == res2["closed_intervals"]

    # In both runs, returned trains must be sorted identically (1001, 1002, 1003)
    assert [t["train_number"] for t in res1["trains"]] == ["1001", "1002", "1003"]
    assert [t["train_number"] for t in res2["trains"]] == ["1001", "1002", "1003"]

    # For each train number, causes_closure and in_merged_group_with are identical
    for t_num in ["1001", "1002", "1003"]:
        t_res1 = next(t for t in res1["trains"] if t["train_number"] == t_num)
        t_res2 = next(t for t in res2["trains"] if t["train_number"] == t_num)
        assert t_res1["causes_closure"] == t_res2["causes_closure"]
        assert set(t_res1["in_merged_group_with"]) == set(t_res2["in_merged_group_with"])


@patch("backend.services.gate_status.get_live_delay")
def test_departed_train_negative_minutes_sorts_first(mock_live, sample_gate):
    """
    Chronological sorting: trains that have already passed (negative minutes_from_now)
    sort before upcoming trains (positive minutes_from_now).
    More negative (passed earlier) sorts before less negative.
    """
    mock_live.return_value = {"delay_minutes": 0, "status": "running"}
    # Current time: 12:10
    # T_past5: KAD 12:03 + 1.83m = 12:04.83 -> ~ -5.2m from 12:10
    # T_past1: KAD 12:07 + 1.83m = 12:08.83 -> ~ -1.2m from 12:10
    # T_future: KAD 12:12 + 1.83m = 12:13.83 -> ~ +3.8m from 12:10
    curr_dt = datetime(2026, 9, 6, 12, 10)
    candidates = [
        make_candidate("T_future", "12:12", direction="DOWN"),
        make_candidate("T_past5", "12:03", direction="DOWN"),
        make_candidate("T_past1", "12:07", direction="DOWN"),
    ]

    res = estimate_gate_status(sample_gate, candidates, curr_dt, api_key="k")
    assert [t["train_number"] for t in res["trains"]] == ["T_past5", "T_past1", "T_future"]
    assert res["trains"][0]["minutes_from_now"] < res["trains"][1]["minutes_from_now"] < res["trains"][2]["minutes_from_now"]



