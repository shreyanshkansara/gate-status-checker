from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class StaleScheduleError(Exception):
    """Raised when schedule cache is missing, corrupted, or older than allowed max age."""
    pass


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_WINDOW_MINUTES = 30  # Reduced default from 90m to 30m


def load_cached_schedule(
    near_station: str,
    far_station: Optional[str] = None,
    data_dir: Optional[Path] = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Dict[str, Any]:
    """
    Reads the cached schedule board file for the given near station.
    
    Checks schedule_cache_{near_station}.json (Station Board) first,
    falling back to schedule_cache_{near}_{far}.json if legacy pair file exists.

    Strictly offline:
    - If the file is missing or older than max_age_days, raises StaleScheduleError.
    - Zero network calls are made.

    Raises:
        StaleScheduleError: If file is missing or cache timestamp is older than max_age_days.
    """
    near = near_station.strip().upper()
    base_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    # Primary target: Station Board cache
    primary_cache = base_dir / f"schedule_cache_{near}.json"
    cache_path = primary_cache

    if not cache_path.exists():
        if far_station:
            far = far_station.strip().upper()
            legacy_cache = base_dir / f"schedule_cache_{near}_{far}.json"
            if legacy_cache.exists():
                cache_path = legacy_cache

    if not cache_path.exists():
        raise StaleScheduleError(
            f"Schedule cache not found for station {near} at {primary_cache}. "
            f"Run 'python backend/scripts/fetch_schedule_cache.py' manually to create it."
        )

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise StaleScheduleError(f"Failed to read schedule cache at {cache_path}: {exc}") from exc

    cached_at_str = data.get("cached_at")
    if not cached_at_str:
        raise StaleScheduleError(
            f"Schedule cache for {near} is missing 'cached_at' timestamp."
        )

    try:
        clean_ts = cached_at_str.replace("Z", "+00:00")
        cached_dt = datetime.fromisoformat(clean_ts)
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StaleScheduleError(
            f"Invalid 'cached_at' timestamp format in {cache_path}: {cached_at_str}"
        ) from exc

    now_utc = datetime.now(timezone.utc)
    cache_age = now_utc - cached_dt
    max_age_delta = timedelta(days=max_age_days)

    if cache_age > max_age_delta:
        days_old = cache_age.total_seconds() / 86400.0
        raise StaleScheduleError(
            f"Schedule cache for {near} is stale: {days_old:.1f} days old "
            f"(exceeds maximum allowed age of {max_age_days} days)."
        )

    return data


def load_cached_local_trains(
    data_dir: Optional[Path] = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Dict[str, Any]:
    """
    Reads the cached suburban/local trains lookup file (schedule_cache_local_mumbai.json).

    Strictly offline:
    - If the file is missing or older than max_age_days, raises StaleScheduleError.
    - Zero network calls are made.

    Raises:
        StaleScheduleError: If file is missing or cache timestamp is older than max_age_days.
    """
    base_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    cache_path = base_dir / "schedule_cache_local_mumbai.json"

    if not cache_path.exists():
        raise StaleScheduleError(
            f"Suburban/local schedule cache not found at {cache_path}. "
            f"Run 'python backend/scripts/fetch_schedule_cache.py' manually to create it."
        )

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise StaleScheduleError(f"Failed to read local schedule cache at {cache_path}: {exc}") from exc

    cached_at_str = data.get("cached_at")
    if not cached_at_str:
        raise StaleScheduleError("Local schedule cache is missing 'cached_at' timestamp.")

    try:
        clean_ts = cached_at_str.replace("Z", "+00:00")
        cached_dt = datetime.fromisoformat(clean_ts)
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise StaleScheduleError(
            f"Invalid 'cached_at' timestamp format in {cache_path}: {cached_at_str}"
        ) from exc

    now_utc = datetime.now(timezone.utc)
    cache_age = now_utc - cached_dt
    max_age_delta = timedelta(days=max_age_days)

    if cache_age > max_age_delta:
        days_old = cache_age.total_seconds() / 86400.0
        raise StaleScheduleError(
            f"Local schedule cache is stale: {days_old:.1f} days old "
            f"(exceeds maximum allowed age of {max_age_days} days)."
        )

    return data



def parse_train_time_at_station(train_item: Dict[str, Any], station_code: str = "") -> Optional[str]:
    """
    Extracts the scheduled HH:MM time string for the station from a train entry.
    Supports both the Station Board schema (stop object) and legacy structures.
    """
    # 1. Station Board format: stop object with departure/arrival
    stop_obj = train_item.get("stop")
    if isinstance(stop_obj, dict):
        time_str = (
            stop_obj.get("departure")
            or stop_obj.get("arrival")
            or stop_obj.get("scheduledDeparture")
            or stop_obj.get("scheduledArrival")
        )
        if time_str:
            return time_str

    st_upper = station_code.strip().upper()

    # 2. Legacy 'from' object
    from_obj = train_item.get("from")
    if isinstance(from_obj, dict):
        code = (from_obj.get("code") or "").strip().upper()
        if code == st_upper or not st_upper:
            time_str = from_obj.get("departure") or from_obj.get("arrival")
            if time_str:
                return time_str

    # 3. Legacy 'to' object
    to_obj = train_item.get("to")
    if isinstance(to_obj, dict):
        code = (to_obj.get("code") or "").strip().upper()
        if code == st_upper:
            time_str = to_obj.get("arrival") or to_obj.get("departure")
            if time_str:
                return time_str

    # 4. Fallback direct fields
    for field in (
        "scheduled_time", "departure", "departure_time", "arrival", "arrival_time", "time",
        "scheduledDeparture", "scheduledArrival"
    ):
        if field in train_item and isinstance(train_item[field], str):
            return train_item[field]

    return None


def _calculate_minute_difference(train_time_str: str, current_dt: datetime) -> Optional[int]:
    """
    Computes difference in minutes between train_time_str (HH:MM) and current_dt,
    accounting for 24-hour midnight wrap-around (-720 to +720 minutes).
    Positive: train is in the future.
    Negative: train is in the past.
    """
    try:
        parts = train_time_str.strip().split(":")
        train_h = int(parts[0])
        train_m = int(parts[1])
    except (ValueError, IndexError):
        return None

    train_total_min = train_h * 60 + train_m
    current_total_min = current_dt.hour * 60 + current_dt.minute

    diff = train_total_min - current_total_min

    # Adjust for 24-hour circular clock wrap-around
    if diff > 720:
        diff -= 1440
    elif diff < -720:
        diff += 1440

    return diff


def _determine_train_direction(
    train_item: Dict[str, Any],
    lnl_train: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Determines if a train is UP (Pune -> LNL -> KAD -> Mumbai)
    or DOWN (Mumbai -> KAD -> LNL -> Pune).
    """
    # Explicit override if present in fixture/data
    if "direction" in train_item and train_item["direction"]:
        return str(train_item["direction"]).upper()
    if "_direction" in train_item and train_item["_direction"]:
        return str(train_item["_direction"]).upper()

    # Compare station sequence in timetable if LNL data is available
    if lnl_train:
        kad_seq = train_item.get("stop", {}).get("sequence")
        lnl_seq = lnl_train.get("stop", {}).get("sequence")
        if kad_seq is not None and lnl_seq is not None:
            # If train reaches LNL at an earlier sequence than KAD, it's heading UP towards Mumbai
            return "UP" if lnl_seq < kad_seq else "DOWN"

    # Fallback to destination/source clues or train number parity
    train_obj = train_item.get("train", {})
    dest_val = train_obj.get("destination")
    dest = (dest_val.get("code") if isinstance(dest_val, dict) else (dest_val or "")).upper()
    if dest in ("CSMT", "LTT", "DR", "BSR", "BDTS", "MMCT", "PNVL", "KYN"):
        return "UP"
    src_val = train_obj.get("source")
    src = (src_val.get("code") if isinstance(src_val, dict) else (src_val or "")).upper()
    if src in ("PUNE", "SUR", "HYB", "MAS", "SBC", "KOP", "MYS", "MRJ"):
        return "UP"

    # Default to DOWN
    return "DOWN"



def get_candidate_trains(
    schedule_data: Dict[str, Any],
    current_datetime: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    data_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Filters cached train schedule board to find candidate trains scheduled
    near the crossing within +/- window_minutes of current_datetime.

    Supports:
    - Default 30-minute candidate window.
    - Direction of travel detection: 'UP' (Pune to Mumbai) vs 'DOWN' (Mumbai to Pune).
    - Cross-references LNL station board for UP trains when available.
    - Strictly offline, zero network calls.
    """
    base_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    # Load optional LNL lookup cache to enrich direction and LNL scheduled times
    lnl_lookup: Dict[str, Any] = {}
    lnl_cache_file = base_dir / "schedule_cache_LNL.json"
    if lnl_cache_file.exists():
        try:
            with open(lnl_cache_file, "r", encoding="utf-8") as f:
                lnl_raw = json.load(f)
            trains_lnl = (
                lnl_raw.get("data", {}).get("trains", [])
                if isinstance(lnl_raw.get("data"), dict)
                else lnl_raw.get("trains", [])
            )
            for lt in trains_lnl:
                num = str(lt.get("train", {}).get("number") or lt.get("number") or "").strip()
                if num:
                    lnl_lookup[num] = lt
        except Exception:
            pass

    # Extract train list from schedule_data
    data_obj = schedule_data.get("data")
    if isinstance(data_obj, dict):
        trains = data_obj.get("trains", [])
        near_station = (
            schedule_data.get("station_code")
            or data_obj.get("station", {}).get("code")
            or schedule_data.get("from_station")
            or "KAD"
        )
    elif isinstance(data_obj, list):
        trains = data_obj
        near_station = schedule_data.get("station_code") or schedule_data.get("from_station", "KAD")
    elif "trains" in schedule_data and isinstance(schedule_data["trains"], list):
        trains = schedule_data["trains"]
        near_station = schedule_data.get("station_code") or schedule_data.get("from_station", "KAD")
    else:
        trains = []
        near_station = schedule_data.get("station_code") or schedule_data.get("from_station", "KAD")

    if not isinstance(trains, list):
        return []

    candidates = []
    for item in trains:
        if not isinstance(item, dict):
            continue

        train_info = item.get("train", {})
        train_num = str(train_info.get("number") or item.get("train_number") or "").strip()

        # Scheduled time at near station (KAD)
        kad_time_str = parse_train_time_at_station(item, near_station)
        if not kad_time_str:
            continue

        # Check LNL counterpart and direction
        lnl_counterpart = lnl_lookup.get(train_num)
        direction = _determine_train_direction(item, lnl_counterpart)

        lnl_time_str = None
        if lnl_counterpart:
            lnl_time_str = parse_train_time_at_station(lnl_counterpart, "LNL")
        elif "scheduled_time_lnl" in item:
            lnl_time_str = item["scheduled_time_lnl"]

        # Evaluate candidate time window:
        # For UP trains, evaluate around arrival at LNL if available, otherwise KAD
        eval_time_str = (lnl_time_str if (direction == "UP" and lnl_time_str) else kad_time_str)
        diff = _calculate_minute_difference(eval_time_str, current_datetime)
        if diff is None:
            continue

        if abs(diff) <= window_minutes:
            # Check runDays if specified (e.g. ['mon', 'tue'])
            run_days = train_info.get("runDays")
            if isinstance(run_days, list) and len(run_days) > 0:
                train_dt = current_datetime + timedelta(minutes=diff)
                train_weekday = train_dt.strftime("%a").lower()
                normalized_days = [str(d).strip().lower() for d in run_days]
                if train_weekday not in normalized_days:
                    continue

            candidate = dict(item)
            stop_info = item.get("stop", {})
            stop_type = stop_info.get("stopType", "halt")
            candidate["direction"] = direction
            candidate["_direction"] = direction
            candidate["_scheduled_time"] = kad_time_str
            candidate["_scheduled_time_kad"] = kad_time_str
            candidate["_scheduled_time_lnl"] = lnl_time_str
            candidate["_diff_minutes"] = diff
            candidate["_is_pass_through"] = (stop_type == "pass-through")
            candidate["_stop_type"] = stop_type
            candidates.append(candidate)

    return candidates


def get_candidate_trains_from_live(
    live_board_data: Dict[str, Any],
    current_datetime: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    data_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Filters real-time live station board data to find candidate trains scheduled
    or actively running near the crossing within +/- window_minutes of current_datetime.

    Supports:
    - Direction of travel detection: 'UP' (Pune to Mumbai) vs 'DOWN' (Mumbai to Pune).
    - Extracts live delay directly from live_board_data: candidate["_delay_source"] = "live_board".
    - Avoids redundant per-candidate RailRadar API calls.
    - Strictly offline processing of the provided live_board_data snapshot.
    """
    base_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    # Load optional LNL lookup cache to enrich direction and LNL scheduled times
    lnl_lookup: Dict[str, Any] = {}
    lnl_cache_file = base_dir / "schedule_cache_LNL.json"
    if lnl_cache_file.exists():
        try:
            with open(lnl_cache_file, "r", encoding="utf-8") as f:
                lnl_raw = json.load(f)
            trains_lnl = (
                lnl_raw.get("data", {}).get("trains", [])
                if isinstance(lnl_raw.get("data"), dict)
                else lnl_raw.get("trains", [])
            )
            for lt in trains_lnl:
                num = str(lt.get("train", {}).get("number") or lt.get("number") or "").strip()
                if num:
                    lnl_lookup[num] = lt
        except Exception:
            pass

    # Extract train list from live_board_data
    data_obj = live_board_data.get("data")
    if isinstance(data_obj, dict):
        trains = data_obj.get("trains", [])
        near_station = (
            data_obj.get("station", {}).get("code")
            or live_board_data.get("station_code")
            or "KAD"
        )
    elif isinstance(data_obj, list):
        trains = data_obj
        near_station = live_board_data.get("station_code") or "KAD"
    elif "trains" in live_board_data and isinstance(live_board_data["trains"], list):
        trains = live_board_data["trains"]
        near_station = live_board_data.get("station_code") or "KAD"
    else:
        trains = []
        near_station = "KAD"

    if not isinstance(trains, list):
        return []

    candidates = []
    for item in trains:
        if not isinstance(item, dict):
            continue

        train_info = item.get("train", {})
        train_num = str(train_info.get("number") or item.get("train_number") or "").strip()

        # Scheduled time at near station (KAD)
        kad_time_str = parse_train_time_at_station(item, near_station)
        if not kad_time_str:
            stop_info = item.get("stop", {})
            kad_time_str = stop_info.get("departure") or stop_info.get("arrival")
        if not kad_time_str:
            continue

        # Check LNL counterpart and direction
        lnl_counterpart = lnl_lookup.get(train_num)
        direction = _determine_train_direction(item, lnl_counterpart)

        lnl_time_str = None
        if lnl_counterpart:
            lnl_time_str = parse_train_time_at_station(lnl_counterpart, "LNL")
        elif "scheduled_time_lnl" in item:
            lnl_time_str = item["scheduled_time_lnl"]

        # Live delay from live board entry
        live_info = item.get("live", {})
        delay_minutes = 0
        live_status = "running"
        if isinstance(live_info, dict):
            raw_delay = live_info.get("delayMinutes")
            if raw_delay is None:
                raw_delay = live_info.get("delay_minutes", 0)
            try:
                delay_minutes = int(raw_delay)
            except (ValueError, TypeError):
                delay_minutes = 0
            live_status = str(live_info.get("type") or live_info.get("status") or "running").lower()

        # Evaluate candidate time window:
        eval_time_str = (lnl_time_str if (direction == "UP" and lnl_time_str) else kad_time_str)
        diff = _calculate_minute_difference(eval_time_str, current_datetime)
        if diff is None:
            continue

        # Check both scheduled diff and delayed diff:
        is_in_window = (abs(diff) <= window_minutes) or (abs(diff + delay_minutes) <= window_minutes)

        if is_in_window:
            # Check runDays if specified
            run_days = train_info.get("runDays")
            if isinstance(run_days, list) and len(run_days) > 0:
                train_dt = current_datetime + timedelta(minutes=diff)
                train_weekday = train_dt.strftime("%a").lower()
                normalized_days = [str(d).strip().lower() for d in run_days]
                if train_weekday not in normalized_days:
                    continue

            candidate = dict(item)
            stop_info = item.get("stop", {})
            is_halt = stop_info.get("isHalt")
            stop_type = stop_info.get("stopType") or ("pass-through" if is_halt is False else "halt")

            candidate["direction"] = direction
            candidate["_direction"] = direction
            candidate["_scheduled_time"] = kad_time_str
            candidate["_scheduled_time_kad"] = kad_time_str
            candidate["_scheduled_time_lnl"] = lnl_time_str
            candidate["_diff_minutes"] = diff
            candidate["_is_pass_through"] = (is_halt is False or stop_type == "pass-through")
            candidate["_stop_type"] = stop_type

            # Live board delay integration
            candidate["_delay_source"] = "live_board"
            candidate["delay_minutes"] = delay_minutes
            candidate["_delay_minutes"] = delay_minutes
            candidate["_live_status"] = live_status
            candidates.append(candidate)

    return candidates

