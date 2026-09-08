from datetime import datetime, timedelta
import pytest
from backend.services.closure_intervals import compute_merged_closure_intervals


def make_train(train_num: str, eta_str: str, past_min: float = 3.0, future_min: float = 2.0):
    return {
        "train_number": train_num,
        "eta_at_gate": eta_str,
        "closure_window_past_min": past_min,
        "closure_window_future_min": future_min,
    }


def test_empty_trains_returns_empty_list():
    """Zero candidate trains yields zero intervals."""
    assert compute_merged_closure_intervals([]) == []


def test_single_train_returns_unmerged_interval():
    """A single isolated train produces one group with is_merged=False."""
    # ETA: 12:00 -> interval [11:58, 12:03]
    trains = [make_train("12124", "2026-09-08T12:00:00")]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=10.0)

    assert len(intervals) == 1
    assert intervals[0]["train_numbers"] == ["12124"]
    assert intervals[0]["is_merged"] is False
    assert intervals[0]["interval_start"] == "2026-09-08T11:58:00"
    assert intervals[0]["interval_end"] == "2026-09-08T12:03:00"


def test_two_trains_within_threshold_merge():
    """
    Two trains whose individual windows leave an 8-minute gap.
    Train 1: ETA 12:00 -> window [11:58, 12:03]
    Train 2: ETA 12:13 -> window [12:11, 12:16]
    Gap between Train 1 end (12:03) and Train 2 start (12:11) is 8 minutes.
    At threshold=10: merges into 1 continuous group spanning [11:58, 12:16].
    """
    trains = [
        make_train("12124", "2026-09-08T12:00:00"),
        make_train("22222", "2026-09-08T12:13:00"),
    ]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=10.0)

    assert len(intervals) == 1
    assert intervals[0]["train_numbers"] == ["12124", "22222"]
    assert intervals[0]["is_merged"] is True
    assert intervals[0]["interval_start"] == "2026-09-08T11:58:00"
    assert intervals[0]["interval_end"] == "2026-09-08T12:16:00"


def test_two_trains_outside_threshold_do_not_merge():
    """
    Same two trains with 8-minute gap.
    At threshold=5: gap (8 min) > 5 min -> separates into two distinct groups.
    """
    trains = [
        make_train("12124", "2026-09-08T12:00:00"),
        make_train("22222", "2026-09-08T12:13:00"),
    ]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=5.0)

    assert len(intervals) == 2
    assert intervals[0]["train_numbers"] == ["12124"]
    assert intervals[0]["is_merged"] is False
    assert intervals[0]["interval_start"] == "2026-09-08T11:58:00"
    assert intervals[0]["interval_end"] == "2026-09-08T12:03:00"

    assert intervals[1]["train_numbers"] == ["22222"]
    assert intervals[1]["is_merged"] is False
    assert intervals[1]["interval_start"] == "2026-09-08T12:11:00"
    assert intervals[1]["interval_end"] == "2026-09-08T12:16:00"


def test_exact_boundary_threshold_merges():
    """Gap exactly equal to merge_threshold_min merges (inclusive boundary)."""
    # Train 1: ETA 12:00 -> window [11:58, 12:03]
    # Train 2: ETA 12:15 -> window [12:13, 12:18]
    # Gap: 12:13 - 12:03 = exactly 10 minutes
    trains = [
        make_train("T1", "2026-09-08T12:00:00"),
        make_train("T2", "2026-09-08T12:15:00"),
    ]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=10.0)
    assert len(intervals) == 1
    assert intervals[0]["is_merged"] is True
    assert intervals[0]["train_numbers"] == ["T1", "T2"]


def test_three_trains_transitive_chaining():
    """
    Three trains: A, B, C.
    A: ETA 12:00 -> window [11:58, 12:03]
    B: ETA 12:12 -> window [12:10, 12:15]  (gap A->B is 7m <= 10m)
    C: ETA 12:24 -> window [12:22, 12:27]  (gap B->C is 7m <= 10m)

    Gap A->C alone is 19 minutes (> 10m).
    Transitive chaining through B results in one single continuous group spanning A to C.
    """
    trains = [
        make_train("TrainA", "2026-09-08T12:00:00"),
        make_train("TrainB", "2026-09-08T12:12:00"),
        make_train("TrainC", "2026-09-08T12:24:00"),
    ]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=10.0)

    assert len(intervals) == 1
    group = intervals[0]
    assert group["train_numbers"] == ["TrainA", "TrainB", "TrainC"]
    assert group["is_merged"] is True
    assert group["interval_start"] == "2026-09-08T11:58:00"
    assert group["interval_end"] == "2026-09-08T12:27:00"


def test_overlapping_train_windows():
    """Trains that arrive while another is still clearing merge with max end time."""
    # Train 1: ETA 12:00 -> [11:58, 12:03]
    # Train 2: ETA 12:02 -> [12:00, 12:05]
    trains = [
        make_train("T1", "2026-09-08T12:00:00"),
        make_train("T2", "2026-09-08T12:02:00"),
    ]
    intervals = compute_merged_closure_intervals(trains, merge_threshold_min=5.0)

    assert len(intervals) == 1
    assert intervals[0]["interval_start"] == "2026-09-08T11:58:00"
    assert intervals[0]["interval_end"] == "2026-09-08T12:05:00"
    assert intervals[0]["train_numbers"] == ["T1", "T2"]
    assert intervals[0]["is_merged"] is True
