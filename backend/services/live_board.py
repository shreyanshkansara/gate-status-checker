from datetime import datetime, timezone
import os
import logging
from typing import Any, Dict, List, Optional
import httpx
from dotenv import load_dotenv

from backend.services.gate_status import (
    RailRadarError,
    RailRadarRateLimitError,
    RailRadarTimeoutError,
    RAILRADAR_BASE_URL,
)

load_dotenv()
logger = logging.getLogger(__name__)


def get_live_station_board(
    station_code: str,
    hours: int = 2,
    api_key: Optional[str] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """
    Calls RailRadar's live station board endpoint:
    GET https://api.railradar.in/v1/stations/{code}/live?includeIntermediate=true

    Passes includeIntermediate=true to capture all pass-through and intermediate trains,
    which is essential for stations like Khandala (KAD).

    Reuses existing RailRadar error hierarchy from gate_status.py.

    Raises:
        RailRadarRateLimitError: on HTTP 429
        RailRadarTimeoutError: on connection/read timeout
        RailRadarError: on HTTP errors, invalid JSON, or missing API key
    """
    key = api_key or os.getenv("RAILRADAR_API_KEY", "").strip()
    if not key:
        raise RailRadarError("RAILRADAR_API_KEY is not set.")

    st_clean = station_code.strip().upper()
    url = f"{RAILRADAR_BASE_URL}/stations/{st_clean}/live"
    params = {"includeIntermediate": "true"}
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params, headers=headers)

        if resp.status_code == 429:
            raise RailRadarRateLimitError(
                f"RailRadar rate limit exceeded for station board {st_clean} (HTTP 429)."
            )

        if resp.status_code != 200:
            raise RailRadarError(
                f"RailRadar error for station board {st_clean}: HTTP {resp.status_code} - {resp.text}"
            )

        data = resp.json()
        return data

    except httpx.TimeoutException as exc:
        raise RailRadarTimeoutError(
            f"Timeout contacting RailRadar for station board {st_clean}: {exc}"
        ) from exc
    except httpx.RequestError as exc:
        raise RailRadarError(
            f"Network error contacting RailRadar for station board {st_clean}: {exc}"
        ) from exc
    except ValueError as exc:
        raise RailRadarError(
            f"Invalid JSON returned from RailRadar for station board {st_clean}: {exc}"
        ) from exc


def filter_local_trains_for_segment(
    local_trains: Any,
    near_station: str = "KAD",
    far_station: str = "LNL",
) -> List[Dict[str, Any]]:
    """
    Pure function (no network calls): filters cached local/suburban trains
    down to entries relevant to the specific rail segment between near_station
    and far_station (e.g. Khandala - Lonavala).

    Accepts either:
    - A dictionary mapping train_number -> train_name (standard RailRadar lookup shape)
    - A list of train dictionaries (e.g. test fixtures or timetable objects)

    Returns a list of candidate train dictionaries tagged with _source='suburban_local'.
    """
    if not local_trains:
        return []

    near = near_station.strip().upper()
    far = far_station.strip().upper()

    station_keywords = {
        "KAD": ["khandala", "kad"],
        "LNL": ["lonavla", "lonavala", "lnl"],
    }
    target_kws = (
        station_keywords.get(near, [near.lower()])
        + station_keywords.get(far, [far.lower()])
    )

    matching_trains: List[Dict[str, Any]] = []

    if isinstance(local_trains, dict):
        for t_num, t_name in local_trains.items():
            name_str = str(t_name).lower()
            if any(kw in name_str for kw in target_kws):
                matching_trains.append({
                    "train": {
                        "number": str(t_num),
                        "name": str(t_name),
                        "type": "SUBURBAN",
                    },
                    "train_number": str(t_num),
                    "train_name": str(t_name),
                    "_source": "suburban_local",
                })
    elif isinstance(local_trains, list):
        for item in local_trains:
            if not isinstance(item, dict):
                continue
            train_info = item.get("train", {})
            t_num = str(train_info.get("number") or item.get("train_number") or "").strip()
            t_name = str(train_info.get("name") or item.get("train_name") or f"Local {t_num}")

            # Check direct name, source/dest, or stops
            name_lower = t_name.lower()
            src = str(train_info.get("source") or item.get("source") or "").upper()
            dst = str(train_info.get("destination") or item.get("destination") or "").upper()
            stops = item.get("stops") or []

            is_match = any(kw in name_lower for kw in target_kws)
            if not is_match and (src in (near, far) or dst in (near, far)):
                is_match = True
            if not is_match and any(
                str(s.get("code") or s.get("station_code") or "").upper() in (near, far)
                for s in stops if isinstance(s, dict)
            ):
                is_match = True

            if is_match:
                candidate = dict(item)
                candidate["_source"] = "suburban_local"
                candidate["train_number"] = t_num
                candidate["train_name"] = t_name
                matching_trains.append(candidate)

    return matching_trains
