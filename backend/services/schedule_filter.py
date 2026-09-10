from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StaleScheduleError(Exception):
    """Raised when schedule cache is missing, corrupted, or older than allowed max age."""
    pass


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_WINDOW_MINUTES = 30  # Reduced default from 90m to 30m
FALLBACK_WINDOW_MINUTES = 90  # Restored 90m window for fallback path where live delays are unknown
SUSPICIOUS_DELAY_THRESHOLD_MINUTES = 20  # Trains scheduled 20+ min ago claiming delay=0/not_started

# Authenticated Central and Western Railway Mumbai suburban and terminal codes.
# Closed-set matching for secondary direction inference when timetable sequence is unavailable:
# - A train terminating at one of these codes is confidently heading UP (towards Mumbai).
# - A train originating at one of these codes is confidently heading DOWN (away from Mumbai).
# Source stations on the Deccan / Pune / South side (e.g. JU, AII, SBC, MAS, HYB, HDP) are an open-ended
# network that cannot be reliably enumerated without false classifications.
MUMBAI_TERMINAL_CODES = frozenset({
    "CSMT",  # Chhatrapati Shivaji Maharaj Terminus (CR primary terminal)
    "LTT",   # Lokmanya Tilak Terminus, Kurla (CR major long-distance terminal)
    "DR",    # Dadar (CR / WR dual junction & long-distance terminus)
    "KYN",   # Kalyan Junction (CR main bifurcation for North-East & South-East lines)
    "TNA",   # Thane (CR major junction & suburban terminal)
    "PNVL",  # Panvel Junction (Harbour / Konkan / Central Railway junction)
    "BSR",   # Vasai Road (WR junction connecting to Central Diva-Vasai chord)
    "BDTS",  # Bandra Terminus (WR long-distance terminus)
    "MMCT",  # Mumbai Central (WR primary long-distance terminus)
    "CCG",   # Churchgate (WR suburban terminus)
    "BVI",   # Borivali (WR major terminal & origin)
    "ADH",   # Andheri (WR suburban junction & origin)
    "CLA",   # Kurla Junction (CR suburban & Harbour line junction)
    "DIVA",  # Diva Junction (CR junction for chord lines)
    "KJT",   # Karjat (CR suburban terminus at the base of Bhor Ghat)
})


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
    or DOWN (Mumbai -> KAD -> LNL -> Pune), or UNKNOWN if ambiguous.
    """
    # Explicit override if present in fixture/data
    if "direction" in train_item and train_item["direction"]:
        return str(train_item["direction"]).upper()
    if "_direction" in train_item and train_item["_direction"]:
        return str(train_item["_direction"]).upper()

    # Primary method: Compare station sequence in timetable if LNL data is available
    if lnl_train:
        kad_seq = train_item.get("stop", {}).get("sequence")
        lnl_seq = lnl_train.get("stop", {}).get("sequence")
        if kad_seq is not None and lnl_seq is not None:
            # If train reaches LNL at an earlier sequence than KAD, it's heading UP towards Mumbai
            return "UP" if lnl_seq < kad_seq else "DOWN"

    # Secondary method: Closed-set Mumbai terminal matching
    train_obj = train_item.get("train", {})
    dest_val = train_obj.get("destination")
    dest = (dest_val.get("code") if isinstance(dest_val, dict) else (dest_val or "")).strip().upper()
    if not dest:
        to_val = train_item.get("to")
        dest = (to_val.get("code") if isinstance(to_val, dict) else (to_val or "")).strip().upper()

    if dest in MUMBAI_TERMINAL_CODES:
        return "UP"

    src_val = train_obj.get("source")
    src = (src_val.get("code") if isinstance(src_val, dict) else (src_val or "")).strip().upper()
    if not src:
        from_val = train_item.get("from")
        src = (from_val.get("code") if isinstance(from_val, dict) else (from_val or "")).strip().upper()

    if src in MUMBAI_TERMINAL_CODES:
        return "DOWN"

    # If neither sequence nor Mumbai-side closed set matches, do NOT guess.
    return "UNKNOWN"



def get_candidate_trains(
    schedule_data: Dict[str, Any],
    current_datetime: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    data_dir: Optional[Path] = None,
    ambiguous_excluded: Optional[List[Dict[str, Any]]] = None,
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
        train_name = train_info.get("name") or item.get("train_name") or f"Train {train_num}"

        # Scheduled time at near station (KAD)
        kad_time_str = parse_train_time_at_station(item, near_station)
        if not kad_time_str:
            logger.debug(
                "Candidate %s (%s): diff=None delay=None in_window=False runDays_ok=None suspicious=False -> REJECTED: no_scheduled_time",
                train_num, train_name,
            )
            continue

        # Check LNL counterpart and direction
        lnl_counterpart = lnl_lookup.get(train_num)
        direction = _determine_train_direction(item, lnl_counterpart)

        if direction == "UNKNOWN":
            src_val = train_info.get("source") or item.get("from")
            src_code = (src_val.get("code") if isinstance(src_val, dict) else (src_val or ""))
            dest_val = train_info.get("destination") or item.get("to")
            dest_code = (dest_val.get("code") if isinstance(dest_val, dict) else (dest_val or ""))
            logger.warning(
                "Excluded train %s (%s): ambiguous direction (source=%s, destination=%s). "
                "Direction could not be verified via sequence or Mumbai terminal match.",
                train_num, train_name, src_code, dest_code,
            )
            if ambiguous_excluded is not None:
                ambiguous_excluded.append(item)
            continue

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
            logger.debug(
                "Candidate %s (%s): diff=None delay=None in_window=False runDays_ok=None suspicious=False -> REJECTED: invalid_time_diff",
                train_num, train_name,
            )
            continue

        is_in_window = abs(diff) <= window_minutes
        run_days_ok = True
        run_days = train_info.get("runDays")
        if isinstance(run_days, list) and len(run_days) > 0:
            train_dt = current_datetime + timedelta(minutes=diff)
            train_weekday = train_dt.strftime("%a").lower()
            normalized_days = [str(d).strip().lower() for d in run_days]
            if train_weekday not in normalized_days:
                run_days_ok = False

        if not is_in_window:
            rejection_reason = f"outside_window(abs({diff:.1f})>{window_minutes}m)"
        elif not run_days_ok:
            rejection_reason = "run_days_mismatch"
        else:
            rejection_reason = None

        decision = "ACCEPTED" if (is_in_window and run_days_ok) else f"REJECTED: {rejection_reason}"
        logger.debug(
            "Candidate %s (%s): diff=%.1f delay=None in_window=%s runDays_ok=%s suspicious=False -> %s",
            train_num, train_name, diff, is_in_window, run_days_ok, decision,
        )

        if not (is_in_window and run_days_ok):
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
    fallback_window_minutes: int = FALLBACK_WINDOW_MINUTES,
    suspicious_threshold_minutes: int = SUSPICIOUS_DELAY_THRESHOLD_MINUTES,
    data_dir: Optional[Path] = None,
    ambiguous_excluded: Optional[List[Dict[str, Any]]] = None,
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
        train_name = train_info.get("name") or item.get("train_name") or f"Train {train_num}"

        # Scheduled time at near station (KAD)
        kad_time_str = parse_train_time_at_station(item, near_station)
        if not kad_time_str:
            stop_info = item.get("stop", {})
            kad_time_str = stop_info.get("departure") or stop_info.get("arrival")
        if not kad_time_str:
            logger.debug(
                "Candidate %s (%s): diff=None delay=None in_window=False runDays_ok=None suspicious=False -> REJECTED: no_scheduled_time",
                train_num, train_name,
            )
            continue

        # Check LNL counterpart and direction
        lnl_counterpart = lnl_lookup.get(train_num)
        direction = _determine_train_direction(item, lnl_counterpart)

        if direction == "UNKNOWN":
            src_val = train_info.get("source") or item.get("from")
            src_code = (src_val.get("code") if isinstance(src_val, dict) else (src_val or ""))
            dest_val = train_info.get("destination") or item.get("to")
            dest_code = (dest_val.get("code") if isinstance(dest_val, dict) else (dest_val or ""))
            logger.warning(
                "Excluded train %s (%s): ambiguous direction (source=%s, destination=%s). "
                "Direction could not be verified via sequence or Mumbai terminal match.",
                train_num, train_name, src_code, dest_code,
            )
            if ambiguous_excluded is not None:
                ambiguous_excluded.append(item)
            continue

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
            logger.debug(
                "Candidate %s (%s): diff=None delay=%s in_window=False runDays_ok=None suspicious=False -> REJECTED: invalid_time_diff",
                train_num, train_name, delay_minutes,
            )
            continue

        # Check both scheduled diff and delayed diff, guarding against suspicious delay=0 misclassification:
        is_suspicious = (
            diff <= -suspicious_threshold_minutes
            and (delay_minutes == 0 or live_status in ("not_started", "yet_to_start", "scheduled"))
        )

        if is_suspicious:
            # Widen window for suspicious entries so they survive to per-train verification
            is_in_window = abs(diff) <= fallback_window_minutes
        else:
            is_in_window = (abs(diff) <= window_minutes) or (abs(diff + delay_minutes) <= window_minutes)

        run_days_ok = True
        run_days = train_info.get("runDays")
        if isinstance(run_days, list) and len(run_days) > 0:
            train_dt = current_datetime + timedelta(minutes=diff)
            train_weekday = train_dt.strftime("%a").lower()
            normalized_days = [str(d).strip().lower() for d in run_days]
            if train_weekday not in normalized_days:
                run_days_ok = False

        if not is_in_window:
            rejection_reason = f"outside_window(diff={diff:.1f},delay={delay_minutes})"
        elif not run_days_ok:
            rejection_reason = "run_days_mismatch"
        else:
            rejection_reason = None

        decision = "ACCEPTED" if (is_in_window and run_days_ok) else f"REJECTED: {rejection_reason}"
        logger.debug(
            "Candidate %s (%s): diff=%.1f delay=%s in_window=%s runDays_ok=%s suspicious=%s -> %s",
            train_num, train_name, diff, delay_minutes, is_in_window, run_days_ok, is_suspicious, decision,
        )

        if not (is_in_window and run_days_ok):
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

        # Live board delay integration:
        # For suspicious entries, do not trust bundled delay=0 / not_started. Tag as "needs_verification"
        # so estimate_gate_status calls get_live_delay for a fresh, authoritative reading.
        if is_suspicious:
            candidate["_delay_source"] = "needs_verification"
        else:
            candidate["_delay_source"] = "live_board"
        candidate["_is_suspicious"] = is_suspicious
        candidate["delay_minutes"] = delay_minutes
        candidate["_delay_minutes"] = delay_minutes
        candidate["_live_status"] = live_status
        candidates.append(candidate)

    return candidates

