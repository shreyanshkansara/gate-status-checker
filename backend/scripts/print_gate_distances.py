import json
import logging
import sys
from pathlib import Path

# Setup logging so warnings from validation are visible
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Ensure repository root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.distance import validate_gate_distance

DATA_DIR = ROOT_DIR / "backend" / "data"
GATES_FILE = DATA_DIR / "gates.json"
STATIONS_FILE = DATA_DIR / "stations.json"


def main():
    print("=" * 68)
    print("       GATE DISTANCES & SEGMENT REFERENCE SANITY CHECK       ")
    print("=" * 68)

    if not GATES_FILE.exists():
        print(f"Error: Gates file not found at {GATES_FILE}")
        sys.exit(1)

    if not STATIONS_FILE.exists():
        print(f"Error: Stations file not found at {STATIONS_FILE}")
        sys.exit(1)

    with open(GATES_FILE, "r", encoding="utf-8") as f:
        gates = json.load(f)

    with open(STATIONS_FILE, "r", encoding="utf-8") as f:
        stations = json.load(f)

    ref = stations.get("_reference", {})
    kad_station = stations.get("KAD", {})
    lnl_station = stations.get("LNL", {})

    print("\n[STATION REFERENCE COORDINATES & SEGMENT]")
    print(f"  Near Station: {kad_station.get('name')} (KAD) -> Lat: {kad_station.get('lat')}, Lon: {kad_station.get('lon')}")
    print(f"  Far Station:  {lnl_station.get('name')} (LNL) -> Lat: {lnl_station.get('lat')}, Lon: {lnl_station.get('lon')}")
    print(f"  Observed KAD-LNL Segment: {ref.get('KAD_LNL_segment_km')} km ({ref.get('KAD_LNL_observed_minutes')} min transit)")
    print(f"  Derived Ghat Speed:       {ref.get('derived_avg_speed_kmph')} km/h")
    print(f"  Reference Note:           {ref.get('note')}")

    print("\n[GATE CONFIGURATION SUMMARY]")
    for i, gate in enumerate(gates, 1):
        gate_id = gate.get("id")
        name = gate.get("name")
        represents = gate.get("represents", [])
        near = gate.get("near_station")
        far = gate.get("far_station")
        dist_km = gate.get("distance_from_near_km")
        is_est = gate.get("distance_is_estimated")
        src = gate.get("distance_source")
        speed = gate.get("avg_speed_kmph")
        note = gate.get("note")

        is_valid = validate_gate_distance(gate, stations)
        val_status = "PASSED (within segment bound)" if is_valid else "WARNING (exceeds segment bound)"

        print(f"\n  Entry #{i}: {name} (ID: {gate_id})")
        print(f"  - Represents:             {', '.join(represents)} (single status unit)")
        print(f"  - Near / Far Stations:    {near} -> {far}")
        print(f"  - Distance from Near:     {dist_km} km ({'ESTIMATE' if is_est else 'MEASURED'})")
        print(f"  - Estimate Source:        {src}")
        print(f"  - Average Speed:          {speed} km/h")
        print(f"  - Sanity Validation:      {val_status}")
        print(f"  - Note:                   {note}")

    print("\n" + "=" * 68)


if __name__ == "__main__":
    main()
