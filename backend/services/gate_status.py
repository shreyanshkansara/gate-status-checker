from datetime import datetime, timezone, timedelta
import os
import logging
from typing import Any, Dict, List, Optional
import httpx
from dotenv import load_dotenv

from backend.services.schedule_filter import parse_train_time_at_station

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

RAILRADAR_BASE_URL = "https://api.railradar.in/v1"

# Gate closure window: gate is likely closed between -3 min (clearing) and +2 min (approaching)
CLOSURE_WINDOW_PAST_MIN = -3.0
CLOSURE_WINDOW_FUTURE_MIN = 2.0


class RailRadarError(Exception):
    """Base exception for RailRadar API errors."""
    pass


class RailRadarRateLimitError(RailRadarError):
    """Raised when RailRadar returns HTTP 429 Too Many Requests."""
    pass


class RailRadarTimeoutError(RailRadarError):
    """Raised when RailRadar request times out."""
    pass


def get_live_delay(
    train_number: str,
    api_key: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """
    Calls RailRadar's live endpoint for a given train:
    GET https://api.railradar.in/v1/trains/{train_number}/live

    Returns structured delay and status information.
    Default delay is 0 if train is 'not_started' or delayMinutes is missing.

    Raises:
        RailRadarRateLimitError: on HTTP 429.
        RailRadarTimeoutError: on connection or read timeouts.
        RailRadarError: on other HTTP errors, missing key, or invalid responses.
    """
    key = api_key or os.getenv("RAILRADAR_API_KEY", "").strip()
    if not key:
        raise RailRadarError("RAILRADAR_API_KEY is not set.")

    clean_train_num = str(train_number).strip()
    url = f"{RAILRADAR_BASE_URL}/trains/{clean_train_num}/live"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)

        if resp.status_code == 429:
            raise RailRadarRateLimitError(
                f"RailRadar rate limit exceeded for train {clean_train_num} (HTTP 429)."
            )

        if resp.status_code != 200:
            raise RailRadarError(
                f"RailRadar error for train {clean_train_num}: HTTP {resp.status_code} - {resp.text}"
            )

        data_payload = resp.json()
    except httpx.TimeoutException as exc:
        raise RailRadarTimeoutError(
            f"Timeout contacting RailRadar for train {clean_train_num}: {exc}"
        ) from exc
    except httpx.RequestError as exc:
        raise RailRadarError(
            f"Network error contacting RailRadar for train {clean_train_num}: {exc}"
        ) from exc
    except ValueError as exc:
        raise RailRadarError(
            f"Invalid JSON returned from RailRadar for train {clean_train_num}: {exc}"
        ) from exc

    # Parse response body
    data = data_payload.get("data", data_payload)
    status = (
        data.get("status")
        or data.get("current_status")
        or ("not_started" if not data.get("started", True) else "running")
    ).lower()

    if status in ("not_started", "yet_to_start", "scheduled"):
        delay_minutes = 0
    else:
        raw_delay = data.get("delayMinutes")
        if raw_delay is None:
            raw_delay = data.get("delay_minutes") or data.get("delay") or 0
        try:
            delay_minutes = int(raw_delay)
        except (ValueError, TypeError):
            delay_minutes = 0

    return {
        "train_number": clean_train_num,
        "delay_minutes": delay_minutes,
        "status": status,
        "raw": data,
    }


def _parse_time_to_datetime(time_str: str, current_datetime: datetime) -> Optional[datetime]:
    """Converts HH:MM string to datetime aligning with current_datetime day and circular wrap-around."""
    try:
        parts = time_str.strip().split(":")
        th = int(parts[0])
        tm = int(parts[1])
    except (ValueError, IndexError):
        return None

    train_min = th * 60 + tm
    curr_min = current_datetime.hour * 60 + current_datetime.minute
    diff = train_min - curr_min
    if diff > 720:
        diff -= 1440
    elif diff < -720:
        diff += 1440

    return current_datetime + timedelta(minutes=diff)


def estimate_gate_status(
    gate: Dict[str, Any],
    candidate_trains: List[Dict[str, Any]],
    current_datetime: datetime,
    api_key: Optional[str] = None,
    segment_km: float = 3.0,
) -> Dict[str, Any]:
    """
    Estimates the open/closed status for level crossing gate 30 & 31.

    Direction-of-Travel Aware:
    - DOWN trains (Mumbai to Pune): train reaches KAD first, gate is 1.1 km after KAD.
      eta_at_gate = scheduled_time_at_KAD + delay + (distance_from_near_km / speed * 60)
    - UP trains (Pune to Mumbai): train reaches LNL first, gate is 1.9 km after LNL.
      eta_at_gate = scheduled_time_at_LNL + delay + ((segment_km - distance_from_near_km) / speed * 60)

    Closure rule:
    - If any train's eta_at_gate falls within -3 to +2 minutes of current_datetime,
      status is 'likely_closed', otherwise 'likely_open'.
    """
    gate_id = gate.get("id", "unknown_gate")
    gate_name = gate.get("name", "")
    represents = gate.get("represents", [])
    distance_is_estimated = gate.get("distance_is_estimated", True)
    distance_source = gate.get("distance_source", "")
    near_station = gate.get("near_station", "KAD")

    distance_from_near_km = float(gate.get("distance_from_near_km") or 1.1)
    distance_from_far_km = max(float(segment_km) - distance_from_near_km, 0.0)
    avg_speed_kmph = float(gate.get("avg_speed_kmph") or 36.0)

    # Transit times in minutes
    transit_from_near = (distance_from_near_km / avg_speed_kmph) * 60.0 if avg_speed_kmph > 0 else 0.0
    transit_from_far = (distance_from_far_km / avg_speed_kmph) * 60.0 if avg_speed_kmph > 0 else 0.0

    supporting_trains: List[Dict[str, Any]] = []
    live_calls_made = 0

    for train in candidate_trains:
        train_info = train.get("train", {})
        train_num = str(train_info.get("number") or train.get("train_number") or "").strip()
        train_name = train_info.get("name") or train.get("train_name") or f"Train {train_num}"

        if not train_num:
            continue

        direction = str(train.get("direction") or train.get("_direction") or "DOWN").upper()

        # Scheduled times
        kad_time_str = train.get("_scheduled_time_kad") or train.get("_scheduled_time") or parse_train_time_at_station(train, near_station)
        lnl_time_str = train.get("_scheduled_time_lnl") or train.get("scheduled_time_lnl")

        # Call RailRadar live delay endpoint only if candidate does not already carry live delay
        if train.get("_delay_source") == "live_board":
            delay_minutes = int(train.get("delay_minutes", train.get("_delay_minutes", 0)))
            live_status = str(train.get("_live_status", train.get("live_status", "running")))
        else:
            try:
                live_data = get_live_delay(train_num, api_key=api_key)
                live_calls_made += 1
            except RailRadarError as exc:
                logger.warning("RailRadar error fetching live delay for train %s: %s", train_num, exc)
                return {
                    "status": "status_unknown",
                    "gate_id": gate_id,
                    "gate_name": gate_name,
                    "represents": represents,
                    "distance_is_estimated": distance_is_estimated,
                    "distance_source": distance_source,
                    "distance_from_near_km": distance_from_near_km,
                    "evaluated_at": current_datetime.isoformat(),
                    "trains": supporting_trains,
                    "error": str(exc),
                    "_live_calls_made": live_calls_made,
                }

            delay_minutes = live_data.get("delay_minutes", 0)
            live_status = live_data.get("status", "unknown")

            # Fallback for suburban/local trains lacking explicit schedule time in cache:
            if not kad_time_str and not lnl_time_str:
                raw = live_data.get("raw", {})
                for key in ("expectedArrivalTime", "expectedDepartureTime", "actualArrival", "actualDeparture"):
                    val = raw.get(key)
                    if isinstance(val, str) and "T" in val:
                        time_candidate = val.split("T")[1][:5]
                        if direction == "UP":
                            lnl_time_str = time_candidate
                        else:
                            kad_time_str = time_candidate
                        break
                if not kad_time_str and not lnl_time_str:
                    default_time = current_datetime.strftime("%H:%M")
                    if direction == "UP":
                        lnl_time_str = default_time
                    else:
                        kad_time_str = default_time

        if not kad_time_str and not lnl_time_str:
            continue

        # Convert to reference datetimes
        kad_dt = _parse_time_to_datetime(kad_time_str, current_datetime) if kad_time_str else None
        lnl_dt = _parse_time_to_datetime(lnl_time_str, current_datetime) if lnl_time_str else None



        # Branched ETA computation based on direction of travel
        if direction == "UP":
            # Pune to Mumbai: train passes LNL first, then crossing, then KAD
            if lnl_dt:
                eta_at_gate = lnl_dt + timedelta(minutes=delay_minutes + transit_from_far)
            elif kad_dt:
                # Fallback if LNL time missing: reaches gate transit_from_near mins before KAD
                eta_at_gate = kad_dt + timedelta(minutes=delay_minutes - transit_from_near)
            else:
                continue
            reference_station = "LNL"
            scheduled_display = lnl_time_str or kad_time_str
        else:
            # DOWN: Mumbai to Pune: train passes KAD first, then crossing, then LNL
            if kad_dt:
                eta_at_gate = kad_dt + timedelta(minutes=delay_minutes + transit_from_near)
            elif lnl_dt:
                # Fallback if KAD time missing: reaches gate transit_from_far mins before LNL
                eta_at_gate = lnl_dt + timedelta(minutes=delay_minutes - transit_from_far)
            else:
                continue
            reference_station = "KAD"
            scheduled_display = kad_time_str or lnl_time_str

        minutes_from_now = (eta_at_gate - current_datetime).total_seconds() / 60.0

        # Tightened closure window: -3 to +2 minutes
        causes_closure = (CLOSURE_WINDOW_PAST_MIN <= minutes_from_now <= CLOSURE_WINDOW_FUTURE_MIN)

        supporting_trains.append({
            "train_number": train_num,
            "train_name": train_name,
            "direction": direction,
            "reference_station": reference_station,
            "scheduled_time": scheduled_display,
            "delay_minutes": delay_minutes,
            "live_status": live_status,
            "eta_at_gate": eta_at_gate.isoformat(),
            "minutes_from_now": round(minutes_from_now, 1),
            "causes_closure": causes_closure,
        })

    is_closed = any(t["causes_closure"] for t in supporting_trains)
    overall_status = "likely_closed" if is_closed else "likely_open"

    return {
        "status": overall_status,
        "gate_id": gate_id,
        "gate_name": gate_name,
        "represents": represents,
        "distance_is_estimated": distance_is_estimated,
        "distance_source": distance_source,
        "distance_from_near_km": distance_from_near_km,
        "evaluated_at": current_datetime.isoformat(),
        "trains": supporting_trains,
        "error": None,
        "_live_calls_made": live_calls_made,
    }
