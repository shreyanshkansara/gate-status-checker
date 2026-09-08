from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union


def _to_datetime(val: Union[datetime, str]) -> datetime:
    """Converts datetime or ISO format string to datetime object."""
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


def compute_merged_closure_intervals(
    supporting_trains: List[Dict[str, Any]],
    merge_threshold_min: float = 10.0,
    closure_window_past_min: float = 3.0,
    closure_window_future_min: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Pure merging logic for train level-crossing closure windows.

    Each train has an individual closure window:
      interval_start = eta_at_gate - closure_window_future_min
      interval_end   = eta_at_gate + closure_window_past_min

    Trains are sorted by interval_start. If the next train's interval_start is
    within merge_threshold_min minutes of the current group's interval_end
    (i.e. next.interval_start - group.interval_end <= merge_threshold_min, inclusive),
    the group is extended:
      group.interval_end = max(group.interval_end, next.interval_end)
      group.train_numbers.append(next.train_number)

    Returns a list of dicts:
      [
        {
          "interval_start": "<iso datetime>",
          "interval_end": "<iso datetime>",
          "train_numbers": ["12124", ...],
          "is_merged": bool,  # True only if group contains > 1 train
        },
        ...
      ]

    Handles 0, 1, 2, and 3+ trains with transitive chaining.
    """
    if not supporting_trains:
        return []

    # 1. Compute individual intervals
    train_intervals = []
    for train in supporting_trains:
        eta_raw = train.get("eta_at_gate")
        if not eta_raw:
            continue

        eta_dt = _to_datetime(eta_raw)
        past_min = float(train.get("closure_window_past_min", closure_window_past_min))
        future_min = float(train.get("closure_window_future_min", closure_window_future_min))

        start_dt = eta_dt - timedelta(minutes=future_min)
        end_dt = eta_dt + timedelta(minutes=past_min)
        train_num = str(train.get("train_number") or "").strip()

        train_intervals.append({
            "start": start_dt,
            "end": end_dt,
            "train_number": train_num,
        })

    if not train_intervals:
        return []

    # 2. Sort by interval_start
    train_intervals.sort(key=lambda t: t["start"])

    # 3. Walk and merge
    groups = []
    current_group = {
        "start": train_intervals[0]["start"],
        "end": train_intervals[0]["end"],
        "train_numbers": [train_intervals[0]["train_number"]] if train_intervals[0]["train_number"] else [],
    }

    for item in train_intervals[1:]:
        # Gap between group's current end and next train's start
        gap_minutes = (item["start"] - current_group["end"]).total_seconds() / 60.0

        # If gap <= merge_threshold_min (inclusive), merge into current group
        if gap_minutes <= float(merge_threshold_min):
            if item["end"] > current_group["end"]:
                current_group["end"] = item["end"]
            if item["train_number"] and item["train_number"] not in current_group["train_numbers"]:
                current_group["train_numbers"].append(item["train_number"])
        else:
            # Finalize previous group and begin new one
            groups.append(current_group)
            current_group = {
                "start": item["start"],
                "end": item["end"],
                "train_numbers": [item["train_number"]] if item["train_number"] else [],
            }

    groups.append(current_group)

    # 4. Format structured output
    result = []
    for g in groups:
        result.append({
            "interval_start": g["start"].isoformat(),
            "interval_end": g["end"].isoformat(),
            "train_numbers": g["train_numbers"],
            "is_merged": len(g["train_numbers"]) > 1,
        })

    return result
