import json
import os
import socket
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from backend.services.schedule_filter import (
    StaleScheduleError,
    load_cached_schedule,
    get_candidate_trains,
    get_candidate_trains_from_live,
    parse_train_time_at_station,
    FALLBACK_WINDOW_MINUTES,
    SUSPICIOUS_DELAY_THRESHOLD_MINUTES,
)


@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def make_station_cache_file(directory: Path, station: str, cached_at_dt: datetime, trains=None):
    payload = {
        "cached_at": cached_at_dt.isoformat(),
        "station_code": station,
        "data": {
            "station": {"code": station, "name": f"Station {station}"},
            "trains": trains or [],
        },
    }
    file_path = directory / f"schedule_cache_{station}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return file_path


# --- Tests for load_cached_schedule ---

def test_stale_cache_missing_file(temp_data_dir):
    """Missing cache file must raise StaleScheduleError."""
    with pytest.raises(StaleScheduleError) as exc_info:
        load_cached_schedule("KAD", data_dir=temp_data_dir)
    assert "not found" in str(exc_info.value).lower()


def test_stale_cache_older_than_14_days(temp_data_dir):
    """Cache older than 14 days must raise StaleScheduleError."""
    fifteen_days_ago = datetime.now(timezone.utc) - timedelta(days=15)
    make_station_cache_file(temp_data_dir, "KAD", fifteen_days_ago)

    with pytest.raises(StaleScheduleError) as exc_info:
        load_cached_schedule("KAD", data_dir=temp_data_dir, max_age_days=14)
    assert "stale" in str(exc_info.value).lower()


def test_fresh_cache_loads_successfully(temp_data_dir):
    """Cache younger than 14 days must load cleanly."""
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    sample_trains = [
        {
            "train": {"number": "12701", "name": "Hussain Sagar SF Express"},
            "stop": {"arrival": "00:02", "departure": "00:02", "stopType": "pass-through"},
        }
    ]
    make_station_cache_file(temp_data_dir, "KAD", two_days_ago, sample_trains)

    data = load_cached_schedule("KAD", data_dir=temp_data_dir, max_age_days=14)
    assert data["station_code"] == "KAD"
    assert len(data["data"]["trains"]) == 1


# --- Tests for get_candidate_trains with Station Board schema ---

@pytest.fixture
def station_board_schedule():
    return {
        "station_code": "KAD",
        "data": {
            "station": {"code": "KAD", "name": "Khandala"},
            "trains": [
                {
                    "train": {"number": "1001", "name": "Morning Express"},
                    "stop": {"arrival": "08:00", "departure": "08:00", "stopType": "pass-through"},
                },
                {
                    "train": {"number": "1002", "name": "Afternoon Passenger"},
                    "stop": {"arrival": "14:15", "departure": "14:16", "stopType": "halt"},
                },
                {
                    "train": {"number": "1003", "name": "Late Afternoon SF"},
                    "stop": {"arrival": "15:00", "departure": "15:00", "stopType": "pass-through"},
                },
                {
                    "train": {"number": "1004", "name": "Night Mail"},
                    "stop": {"arrival": "23:45", "departure": "23:45", "stopType": "pass-through"},
                },
                {
                    "train": {"number": "1005", "name": "Midnight Special"},
                    "stop": {"arrival": "00:20", "departure": "00:20", "stopType": "pass-through"},
                },
            ],
        },
    }


def test_no_candidates(station_board_schedule):
    """When current time is far from any train, returns an empty list []."""
    current_dt = datetime(2026, 9, 6, 18, 0)
    candidates = get_candidate_trains(station_board_schedule, current_dt, window_minutes=90)
    assert candidates == []


def test_one_candidate(station_board_schedule):
    """When exactly one train falls within +/- 90 minutes."""
    current_dt = datetime(2026, 9, 6, 8, 30)
    candidates = get_candidate_trains(station_board_schedule, current_dt, window_minutes=90)
    assert len(candidates) == 1
    assert candidates[0]["train"]["number"] == "1001"
    assert candidates[0]["_scheduled_time"] == "08:00"
    assert candidates[0]["_diff_minutes"] == -30
    assert candidates[0]["_is_pass_through"] is True


def test_multiple_candidates(station_board_schedule):
    """When multiple trains fall within +/- 90 minutes."""
    current_dt = datetime(2026, 9, 6, 14, 40)
    candidates = get_candidate_trains(station_board_schedule, current_dt, window_minutes=90)
    assert len(candidates) == 2
    train_nums = [c["train"]["number"] for c in candidates]
    assert "1002" in train_nums
    assert "1003" in train_nums


def test_midnight_rollover_forward(station_board_schedule):
    """Current time 23:55 should detect train 1004 (23:45) and train 1005 (00:20)."""
    current_dt = datetime(2026, 9, 6, 23, 55)
    candidates = get_candidate_trains(station_board_schedule, current_dt, window_minutes=90)
    train_nums = [c["train"]["number"] for c in candidates]
    assert "1004" in train_nums
    assert "1005" in train_nums


def test_midnight_rollover_backward(station_board_schedule):
    """Current time 00:05 should detect train 1004 (23:45) and train 1005 (00:20)."""
    current_dt = datetime(2026, 9, 7, 0, 5)
    candidates = get_candidate_trains(station_board_schedule, current_dt, window_minutes=90)
    train_nums = [c["train"]["number"] for c in candidates]
    assert "1004" in train_nums
    assert "1005" in train_nums


def test_zero_network_calls_guarantee(monkeypatch, temp_data_dir, station_board_schedule):
    """
    Enforces that this module makes zero network calls by blocking socket creation.
    """
    def blocked_socket(*args, **kwargs):
        raise AssertionError("A network call was attempted by the offline schedule filter module!")

    monkeypatch.setattr(socket, "socket", blocked_socket)

    # 1. load_cached_schedule must not use network
    now_dt = datetime.now(timezone.utc)
    make_station_cache_file(temp_data_dir, "KAD", now_dt)
    data = load_cached_schedule("KAD", data_dir=temp_data_dir)
    assert data is not None

    # 2. get_candidate_trains must not use network
    candidates = get_candidate_trains(station_board_schedule, datetime(2026, 9, 6, 14, 30))
    assert len(candidates) == 2


# ==============================================================================
# Phase 14: Fallback-path windowing + Suspicious live entry tests
# ==============================================================================

def test_fallback_path_window_widening():
    """
    Test fallback-path windowing:
    A train scheduled 40 minutes in the past relative to current_datetime
    falls outside the old 30-minute window (confirming the bug),
    but is accepted under FALLBACK_WINDOW_MINUTES=90.
    """
    current_dt = datetime(2026, 9, 10, 12, 0)
    # Train scheduled at 11:20 (diff = -40 min)
    schedule = {
        "station_code": "KAD",
        "data": {
            "trains": [
                {
                    "train": {"number": "22731", "name": "Hyderabad Express"},
                    "stop": {"departure": "11:20", "stopType": "pass-through"},
                }
            ]
        },
    }

    # Old 30-minute default excludes the train
    candidates_old = get_candidate_trains(schedule, current_dt, window_minutes=30)
    assert len(candidates_old) == 0

    # With FALLBACK_WINDOW_MINUTES (90m), candidate is retained
    candidates_widened = get_candidate_trains(schedule, current_dt, window_minutes=FALLBACK_WINDOW_MINUTES)
    assert len(candidates_widened) == 1
    assert candidates_widened[0]["train"]["number"] == "22731"
    assert candidates_widened[0]["_diff_minutes"] == -40


def test_live_path_suspicious_entry_filtering():
    """
    A live board entry scheduled 25 minutes in the past with delay_minutes=0
    and status 'not_started' must be flagged as suspicious and tagged
    '_delay_source': 'needs_verification'.
    """
    current_dt = datetime(2026, 9, 10, 12, 0)
    # Train scheduled at 11:35 (diff = -25 min)
    live_board_data = {
        "data": {
            "station": {"code": "KAD"},
            "trains": [
                {
                    "train": {"number": "22731", "name": "Hyderabad Express"},
                    "stop": {"departure": "11:35", "stopType": "pass-through"},
                    "live": {
                        "delayMinutes": 0,
                        "status": "not_started",
                    },
                }
            ],
        }
    }

    candidates = get_candidate_trains_from_live(live_board_data, current_dt, window_minutes=30)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["train"]["number"] == "22731"
    assert c["_diff_minutes"] == -25
    assert c["_is_suspicious"] is True
    assert c["_delay_source"] == "needs_verification"


def test_live_path_normal_entry_not_suspicious():
    """
    Normal live board entries (on-time upcoming train, or running train with reported delay)
    must NOT be flagged as suspicious and must be tagged '_delay_source': 'live_board'.
    """
    current_dt = datetime(2026, 9, 10, 12, 0)
    live_board_data = {
        "data": {
            "station": {"code": "KAD"},
            "trains": [
                # 1. Upcoming on-time train: scheduled in 10 minutes, status not_started
                {
                    "train": {"number": "12124", "name": "Deccan Queen"},
                    "stop": {"departure": "12:10", "stopType": "halt"},
                    "live": {"delayMinutes": 0, "status": "not_started"},
                },
                # 2. Running delayed train: scheduled 25 min ago, 30 min delay -> diff+delay = +5
                {
                    "train": {"number": "11008", "name": "Deccan Express"},
                    "stop": {"departure": "11:35", "stopType": "halt"},
                    "live": {"delayMinutes": 30, "status": "running"},
                },
            ],
        }
    }

    candidates = get_candidate_trains_from_live(live_board_data, current_dt, window_minutes=30)
    assert len(candidates) == 2

    c_12124 = next(c for c in candidates if c["train"]["number"] == "12124")
    assert c_12124["_is_suspicious"] is False
    assert c_12124["_delay_source"] == "live_board"

    c_11008 = next(c for c in candidates if c["train"]["number"] == "11008")
    assert c_11008["_is_suspicious"] is False
    assert c_11008["_delay_source"] == "live_board"
