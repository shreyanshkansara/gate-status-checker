from starlette.testclient import TestClient
from backend.main import app
from unittest.mock import patch
from backend.services.gate_status import RailRadarError
import json

client = TestClient(app)

print("=" * 60)
print("1. REAL LIVE CALL TO /gate/status")
print("=" * 60)
r = client.get("/gate/status")
print(f"HTTP Status: {r.status_code}")
data = r.json()
print(f"data_source:          {data.get('data_source')}")
print(f"status:               {data.get('status')}")
print(f"gate_id:              {data.get('gate_id')}")
print(f"represents:           {data.get('represents')}")
print(f"distance_is_estimated:{data.get('distance_is_estimated')}")
print(f"distance_source:      {data.get('distance_source')}")
print(f"distance_from_near_km:{data.get('distance_from_near_km')}")
print(f"trains count:         {len(data.get('trains', []))}")
for t in data.get("trains", []):
    print(f"  * Train {t.get('train_number')} - {t.get('train_name')}")
    print(f"    Direction: {t.get('direction')} | Delay: {t.get('delay_minutes')}m | ETA: {t.get('eta_at_gate')} | Causes closure: {t.get('causes_closure')}")

print("\n" + "=" * 60)
print("2. SIMULATED LIVE BOARD FAILURE (FALLBACK TEST)")
print("=" * 60)
with patch("backend.main.get_live_station_board", side_effect=RailRadarError("Simulated Live Failure")):
    r_fallback = client.get("/gate/status")
    print(f"HTTP Status: {r_fallback.status_code}")
    data_fb = r_fallback.json()
    print(f"data_source:          {data_fb.get('data_source')}")
    print(f"status:               {data_fb.get('status')}")
    print(f"represents:           {data_fb.get('represents')}")
    print(f"distance_is_estimated:{data_fb.get('distance_is_estimated')}")
    print(f"distance_source:      {data_fb.get('distance_source')}")
    print(f"trains count:         {len(data_fb.get('trains', []))}")
