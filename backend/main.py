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
    get_candidate_trains,
    StaleScheduleError,
)
from backend.services.gate_status import estimate_gate_status

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
    1. Load gate config.
    2. Run validate_gate_distance (advisory sanity check).
    3. Load cached schedule (returns 'schedule_stale' if missing or expired).
    4. Filter candidate trains within +/- 90 minutes.
    5. If no candidates, return 'likely_open' with 0 RailRadar calls made.
    6. If candidates exist, call estimate_gate_status (hits RailRadar once per candidate).
    7. Return structured result with represents, distance_is_estimated, and distance_source.
    """
    # 1. Load single gate config
    gate = load_single_gate_config()
    near_station = gate.get("near_station", "KAD")
    far_station = gate.get("far_station", "LNL")

    # Current IST time
    current_dt = datetime.now(IST)

    # 2. Advisory distance validation check
    stations = load_stations_config()
    if stations:
        validate_gate_distance(gate, stations)

    # 3. Load cached schedule
    try:
        schedule_data = load_cached_schedule(near_station, far_station, data_dir=DATA_DIR)
    except StaleScheduleError as exc:
        logger.warning("[REQUEST /gate/status] Schedule cache stale or missing: %s", exc)
        logger.info("[REQUEST /gate/status] Made 0 RailRadar live API call(s) (schedule cache stale/missing)")
        return {
            "status": "schedule_stale",
            "gate_id": gate.get("id"),
            "gate_name": gate.get("name"),
            "represents": gate.get("represents", []),
            "distance_is_estimated": gate.get("distance_is_estimated", True),
            "distance_source": gate.get("distance_source", ""),
            "distance_from_near_km": gate.get("distance_from_near_km"),
            "evaluated_at": current_dt.isoformat(),
            "trains": [],
            "error": str(exc),
            "instructions": "Run 'python backend/scripts/fetch_schedule_cache.py' to update schedule cache.",
        }

    # 4. Filter candidate trains (+/- 30 minutes)
    candidates = get_candidate_trains(schedule_data, current_dt, window_minutes=30, data_dir=DATA_DIR)

    # 5. If no candidate trains, return likely_open without calling RailRadar
    if not candidates:
        logger.info("[REQUEST /gate/status] Made 0 RailRadar live API call(s) (no candidate trains in +/- 30m window)")
        return {
            "status": "likely_open",
            "gate_id": gate.get("id"),
            "gate_name": gate.get("name"),
            "represents": gate.get("represents", []),
            "distance_is_estimated": gate.get("distance_is_estimated", True),
            "distance_source": gate.get("distance_source", ""),
            "distance_from_near_km": gate.get("distance_from_near_km"),
            "evaluated_at": current_dt.isoformat(),
            "trains": [],
            "error": None,
        }

    # 6. Call RailRadar live API for candidates (once per candidate)
    train_summary = [
        f"{str(c.get('train', {}).get('number') or c.get('train_number') or '').strip()}({c.get('direction', 'DOWN')})"
        for c in candidates
    ]
    logger.info(
        "[REQUEST /gate/status] Making %d RailRadar live API call(s) for candidate train(s): %s",
        len(candidates),
        train_summary,
    )

    segment_km = float(stations.get("_reference", {}).get("KAD_LNL_segment_km", 3.0)) if stations else 3.0
    result = estimate_gate_status(gate, candidates, current_dt, segment_km=segment_km)

    logger.info(
        "[REQUEST /gate/status] Completed %d RailRadar live API call(s). Overall gate status: %s",
        len(candidates),
        result.get("status"),
    )

    # 7. Ensure all required fields are present in response
    result["represents"] = gate.get("represents", [])
    result["distance_is_estimated"] = gate.get("distance_is_estimated", True)
    result["distance_source"] = gate.get("distance_source", "")
    result["distance_from_near_km"] = gate.get("distance_from_near_km")

    return result


# Mount frontend static files if directory exists
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
