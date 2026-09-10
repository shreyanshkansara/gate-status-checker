import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from backend.services.distance import validate_gate_distance
from backend.services.schedule_filter import (
    load_cached_schedule,
    load_cached_local_trains,
    get_candidate_trains,
    get_candidate_trains_from_live,
    StaleScheduleError,
    FALLBACK_WINDOW_MINUTES,
)
from backend.services.gate_status import (
    estimate_gate_status,
    RailRadarError,
)
from backend.services.live_board import (
    get_live_station_board,
    filter_local_trains_for_segment,
)
from backend.services.settings_store import (
    load_settings,
    save_settings,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gate_checker")

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"

GATES_FILE = DATA_DIR / "gates.json"
STATIONS_FILE = DATA_DIR / "stations.json"

# Indian Standard Time (IST) offset UTC+05:30 for Indian Railways schedules
IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(
    title="Gate Status Checker API",
    description="Minimal API estimating open/closed status for LC Gate 30 & 31 near Khandala (KAD) - Lonavala (LNL).",
    version="0.2.0",
)

# Enable CORS for frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_single_gate_config() -> Dict[str, Any]:
    """Loads the single hardcoded gate configuration from gates.json."""
    if not GATES_FILE.exists():
        raise RuntimeError(f"Gates configuration file not found at {GATES_FILE}")
    with open(GATES_FILE, "r", encoding="utf-8") as f:
        gates = json.load(f)
    if not isinstance(gates, list) or len(gates) == 0:
        raise RuntimeError(f"No gate configuration entries found in {GATES_FILE}")
    return gates[0]


def load_stations_config() -> Dict[str, Any]:
    """Loads station reference and coordinates from stations.json."""
    if not STATIONS_FILE.exists():
        return {}
    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
async def health_check():
    """Service health check endpoint."""
    gate = load_single_gate_config()
    return {
        "status": "ok",
        "service": "gate-status-checker",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate_unit": gate.get("id"),
        "represents": gate.get("represents", []),
    }


@app.get("/gate")
@app.get("/api/gate")
async def get_gate():
    """
    Returns the single gate configuration metadata (no list, no gate_id parameter).
    """
    gate = load_single_gate_config()
    return {
        "id": gate.get("id"),
        "name": gate.get("name"),
        "represents": gate.get("represents", []),
        "near_station": gate.get("near_station"),
        "far_station": gate.get("far_station"),
        "distance_from_near_km": gate.get("distance_from_near_km"),
        "distance_is_estimated": gate.get("distance_is_estimated", True),
        "distance_source": gate.get("distance_source"),
        "avg_speed_kmph": gate.get("avg_speed_kmph"),
        "note": gate.get("note"),
    }


@app.get("/gate/status")
@app.get("/api/gate/status")
async def get_gate_status():
    """
    Evaluates the live estimated status of the single gate unit.
    This is the ONLY endpoint that can trigger network calls to RailRadar.

    Flow:
    1. Load gate config & advisory distance check.
    2. Try live station board (GET /v1/stations/KAD/live).
       On success: candidates carry live delay directly (0 further calls per candidate).
       On failure: gracefully fall back to cached schedule (schedule_cache_KAD.json).
    3. Merge suburban/local candidates from cached local trains file (reused offline up to 14 days).
    4. If no candidates, return 'likely_open' (logging accurate live calls made).
    5. Evaluate gate status: calls get_live_delay only for candidates lacking live delay.
    6. Return structured result with represents, distance_is_estimated, distance_source, and data_source.
    """
    # 1. Load single gate config & settings
    gate = load_single_gate_config()
    near_station = gate.get("near_station", "KAD")
    far_station = gate.get("far_station", "LNL")

    settings = load_settings(DATA_DIR)
    closure_window_past_min = settings.get("closure_window_past_min", 3)
    closure_window_future_min = settings.get("closure_window_future_min", 2)
    train_merge_threshold_min = settings.get("train_merge_threshold_min", 10)

    # Current IST time
    current_dt = datetime.now(IST)

    # Advisory distance validation check
    stations = load_stations_config()
    if stations:
        validate_gate_distance(gate, stations)

    data_source = "live"
    live_calls_count = 0
    candidates = []

    # 2. Primary path: Live station board
    try:
        live_board_data = get_live_station_board(near_station)
        live_calls_count += 1
        data_source = "live"
        candidates = get_candidate_trains_from_live(
            live_board_data, current_dt, window_minutes=30, data_dir=DATA_DIR
        )
        logger.info(
            "[REQUEST /gate/status] Loaded live station board for %s (1 RailRadar call made, data_source=live)",
            near_station,
        )
    except RailRadarError as exc:
        logger.warning(
            "[REQUEST /gate/status] Live station board call failed for %s (%s). Falling back to cached schedule.",
            near_station,
            exc,
        )
        data_source = "cached_fallback"
        try:
            schedule_data = load_cached_schedule(near_station, far_station, data_dir=DATA_DIR)
            # Pass FALLBACK_WINDOW_MINUTES (90m) instead of live path's 30m window.
            # The offline schedule cache has no live delay data at filter time,
            # so a wider window is necessary to ensure severely delayed trains
            # are not prematurely dropped before their live delay can be evaluated.
            candidates = get_candidate_trains(
                schedule_data, current_dt, window_minutes=FALLBACK_WINDOW_MINUTES, data_dir=DATA_DIR
            )
        except StaleScheduleError as stale_exc:
            logger.warning("[REQUEST /gate/status] Schedule cache stale or missing: %s", stale_exc)
            logger.info(
                "[REQUEST /gate/status] Made %d RailRadar live API call(s) (schedule cache stale/missing)",
                live_calls_count,
            )
            return {
                "status": "schedule_stale",
                "gate_id": gate.get("id"),
                "gate_name": gate.get("name"),
                "represents": gate.get("represents", []),
                "distance_is_estimated": gate.get("distance_is_estimated", True),
                "distance_source": gate.get("distance_source", ""),
                "distance_from_near_km": gate.get("distance_from_near_km"),
                "closure_window_past_min": closure_window_past_min,
                "closure_window_future_min": closure_window_future_min,
                "merge_threshold_min": train_merge_threshold_min,
                "closed_intervals": [],
                "evaluated_at": current_dt.isoformat(),
                "data_source": data_source,
                "trains": [],
                "error": str(stale_exc),
                "instructions": "Run 'python backend/scripts/fetch_schedule_cache.py' to update schedule cache.",
            }

    # 3. Merge cached suburban/local trains (purely offline read)
    try:
        local_cache = load_cached_local_trains(data_dir=DATA_DIR)
        local_data = local_cache.get("data", {})
        local_candidates = filter_local_trains_for_segment(
            local_data, near_station=near_station, far_station=far_station
        )
        existing_nums = {
            str(c.get("train", {}).get("number") or c.get("train_number") or "").strip()
            for c in candidates
        }
        for lt in local_candidates:
            lt_num = str(lt.get("train", {}).get("number") or lt.get("train_number") or "").strip()
            if lt_num and lt_num not in existing_nums:
                candidates.append(lt)
                existing_nums.add(lt_num)
    except StaleScheduleError as exc:
        logger.warning(
            "[REQUEST /gate/status] Suburban/local schedule cache stale or missing: %s. Proceeding without local candidates.",
            exc,
        )
    except Exception as exc:
        logger.warning(
            "[REQUEST /gate/status] Could not load suburban/local trains cache: %s. Proceeding without local candidates.",
            exc,
        )

    # 4. If no candidate trains, return likely_open
    if not candidates:
        if data_source == "live":
            logger.info(
                "[REQUEST /gate/status] Made 1 RailRadar live API call(s) (live station board; no candidate trains in +/- 30m window)"
            )
        else:
            logger.info(
                "[REQUEST /gate/status] Made 0 RailRadar live API call(s) (cached fallback; no candidate trains in +/- 30m window)"
            )

        return {
            "status": "likely_open",
            "gate_id": gate.get("id"),
            "gate_name": gate.get("name"),
            "represents": gate.get("represents", []),
            "distance_is_estimated": gate.get("distance_is_estimated", True),
            "distance_source": gate.get("distance_source", ""),
            "distance_from_near_km": gate.get("distance_from_near_km"),
            "closure_window_past_min": closure_window_past_min,
            "closure_window_future_min": closure_window_future_min,
            "merge_threshold_min": train_merge_threshold_min,
            "closed_intervals": [],
            "evaluated_at": current_dt.isoformat(),
            "data_source": data_source,
            "trains": [],
            "error": None,
        }

    # 5. Call RailRadar live API for candidates (skips candidates from live_board)
    train_summary = [
        f"{str(c.get('train', {}).get('number') or c.get('train_number') or '').strip()}({c.get('direction', 'DOWN')})"
        for c in candidates
    ]
    needed_calls = sum(1 for c in candidates if c.get("_delay_source") != "live_board")
    logger.info(
        "[REQUEST /gate/status] Evaluating %d candidate train(s): %s (data_source=%s, %d per-candidate live call(s) required)",
        len(candidates),
        train_summary,
        data_source,
        needed_calls,
    )

    segment_km = float(stations.get("_reference", {}).get("KAD_LNL_segment_km", 3.0)) if stations else 3.0
    result = estimate_gate_status(
        gate,
        candidates,
        current_dt,
        segment_km=segment_km,
        closure_window_past_min=closure_window_past_min,
        closure_window_future_min=closure_window_future_min,
        train_merge_threshold_min=train_merge_threshold_min,
    )

    calls_in_estimate = result.pop("_live_calls_made", needed_calls)
    total_calls = live_calls_count + calls_in_estimate

    logger.info(
        "[REQUEST /gate/status] Completed %d RailRadar live API call(s) (data_source=%s). Overall gate status: %s",
        total_calls,
        data_source,
        result.get("status"),
    )

    # 6. Ensure all required fields are present in response
    result["data_source"] = data_source
    result["represents"] = gate.get("represents", [])
    result["distance_is_estimated"] = gate.get("distance_is_estimated", True)
    result["distance_source"] = gate.get("distance_source", "")
    result["distance_from_near_km"] = gate.get("distance_from_near_km")
    result["closure_window_past_min"] = closure_window_past_min
    result["closure_window_future_min"] = closure_window_future_min
    result["merge_threshold_min"] = train_merge_threshold_min
    result["closed_intervals"] = result.get("closed_intervals", [])

    return result


@app.get("/settings")
@app.get("/api/settings")
async def get_settings():
    """
    Returns the current settings.json contents.
    Pure local-file read, zero external calls.
    """
    return load_settings(DATA_DIR)


@app.post("/settings")
@app.post("/api/api_settings")
@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any]):
    """
    Updates closure window settings.
    Accepts JSON body: {closure_window_future_min, closure_window_past_min, train_merge_threshold_min}.
    Returns 400 on invalid input, otherwise 200 with saved dict.
    """
    try:
        kwargs = {}
        if "closure_window_future_min" in payload:
            kwargs["closure_window_future_min"] = payload["closure_window_future_min"]
        if "closure_window_past_min" in payload:
            kwargs["closure_window_past_min"] = payload["closure_window_past_min"]
        if "train_merge_threshold_min" in payload:
            kwargs["train_merge_threshold_min"] = payload["train_merge_threshold_min"]

        return save_settings(DATA_DIR, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))



# Mount frontend static files if directory exists
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
