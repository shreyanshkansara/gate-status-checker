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
    parse_train_time_at_station,
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
