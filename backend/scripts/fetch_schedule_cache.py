import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
GATES_FILE = DATA_DIR / "gates.json"

# Load environment variables (.env from workspace root)
load_dotenv(ROOT_DIR / ".env")

RAILRADAR_BASE_URL = "https://api.railradar.in/v1"


def get_gate_stations(gates_path: Path):
    """Extracts unique near_station and far_station codes from gates.json."""
    if not gates_path.exists():
        raise FileNotFoundError(f"Gates file not found: {gates_path}")

    with open(gates_path, "r", encoding="utf-8") as f:
        gates = json.load(f)

    stations = set()
    for g in gates:
        near = g.get("near_station")
        far = g.get("far_station")
        if near:
            stations.add(near.strip().upper())
        if far:
            stations.add(far.strip().upper())
    return sorted(list(stations)) or ["KAD", "LNL"]


get_near_stations = get_gate_stations


def fetch_and_cache_station_board(station_code: str, api_key: str) -> bool:
    """
    Calls RailRadar Station Board endpoint:
    GET https://api.railradar.in/v1/stations/{station}/trains?includeIntermediate=true

    Saves the response to backend/data/schedule_cache_{station}.json
    along with 'cached_at' ISO timestamp.
    """
    st_clean = station_code.strip().upper()
    url = f"{RAILRADAR_BASE_URL}/stations/{st_clean}/trains"
    params = {"includeIntermediate": "true"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    cache_file = DATA_DIR / f"schedule_cache_{st_clean}.json"

    print(f"\nQuerying RailRadar Station Board: {url}?includeIntermediate=true")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params, headers=headers)

        if response.status_code != 200:
            print(f"[-] Request failed with status HTTP {response.status_code}: {response.text}")
            return False

        raw_data = response.json()
        now_iso = datetime.now(timezone.utc).isoformat()

        if isinstance(raw_data, dict):
            cached_payload = {
                "cached_at": now_iso,
                "station_code": st_clean,
                **raw_data,
            }
            trains = raw_data.get("data", {}).get("trains", []) if isinstance(raw_data.get("data"), dict) else []
        elif isinstance(raw_data, list):
            cached_payload = {
                "cached_at": now_iso,
                "station_code": st_clean,
                "trains": raw_data,
            }
            trains = raw_data
        else:
            cached_payload = {
                "cached_at": now_iso,
                "station_code": st_clean,
                "raw_response": raw_data,
            }
            trains = []

        total_trains = len(trains)
        pass_through_count = 0
        halt_count = 0

        for t in trains:
            stop_info = t.get("stop", {})
            stop_type = str(stop_info.get("stopType", "")).lower()
            if stop_type == "pass-through":
                pass_through_count += 1
            elif stop_type in ("halt", "scheduled", "originating", "terminating"):
                halt_count += 1
            else:
                arr = stop_info.get("arrival")
                dep = stop_info.get("departure")
                if arr and dep and arr == dep:
                    pass_through_count += 1
                else:
                    halt_count += 1

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cached_payload, f, indent=2)

        print(f"[+] SUCCESS: Cached {total_trains} train(s) to {cache_file.name}")
        print(f"    - Pass-through (non-halting): {pass_through_count}")
        print(f"    - Scheduled halts:            {halt_count}")
        print(f"    - Timestamp:                  {now_iso}")
        return True

    except httpx.RequestError as exc:
        print(f"[-] Connection error contacting RailRadar: {exc}")
        return False
    except Exception as exc:
        print(f"[-] Unexpected error: {exc}")
        return False


def main():
    print("=" * 64)
    print("      RAILRADAR STATION BOARD CACHE FETCHER (MANUAL)      ")
    print("=" * 64)

    api_key = os.getenv("RAILRADAR_API_KEY", "").strip()
    if not api_key:
        print("\n[-] Error: RAILRADAR_API_KEY environment variable is missing or empty.")
        print("    Please set your key in .env before running this script.")
        print(f"    Expected path: {ROOT_DIR / '.env'}\n")
        sys.exit(1)

    stations = get_gate_stations(GATES_FILE)
    print(f"Found {len(stations)} station(s) to cache from {GATES_FILE.name}: {', '.join(stations)}")

    success_count = 0
    for st in stations:
        if fetch_and_cache_station_board(st, api_key):
            success_count += 1

    print("\n" + "=" * 64)
    if success_count == len(stations):
        print(f"All {success_count} station board cache(s) successfully updated.")
    else:
        print(f"Completed with issues: {success_count}/{len(stations)} cache(s) updated.")
    print("=" * 64)


if __name__ == "__main__":
    main()
