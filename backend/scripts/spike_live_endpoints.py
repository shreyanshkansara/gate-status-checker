import json
import os
import sys
import time
from pathlib import Path
import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

API_KEY = os.getenv("RAILRADAR_API_KEY", "").strip()
BASE_URL = "https://api.railradar.in/v1"
DATA_DIR = ROOT_DIR / "backend" / "data"

if not API_KEY:
    print("ERROR: RAILRADAR_API_KEY is not set in .env")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
}

def query_endpoint(url: str, params=None):
    print(f"\n---> Calling: {url} with params={params}")
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, params=params, headers=HEADERS)
            print(f"Status: {resp.status_code}")
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text}
            return resp.status_code, data
    except Exception as exc:
        print(f"Request error: {exc}")
        return None, {"error": str(exc)}

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. KAD Live (needs includeIntermediate=true because KAD has mostly pass-through trains)
    kad_status, kad_data = query_endpoint(f"{BASE_URL}/stations/KAD/live", params={"includeIntermediate": "true"})
    with open(DATA_DIR / "_spike_live_KAD.json", "w", encoding="utf-8") as f:
        json.dump(kad_data, f, indent=2)
    print("Saved to backend/data/_spike_live_KAD.json")

    # Sleep 3s to stay well below 10 req/min limit
    time.sleep(3)

    # 2. LNL Live
    lnl_status, lnl_data = query_endpoint(f"{BASE_URL}/stations/LNL/live", params={"includeIntermediate": "true"})
    with open(DATA_DIR / "_spike_live_LNL.json", "w", encoding="utf-8") as f:
        json.dump(lnl_data, f, indent=2)
    print("Saved to backend/data/_spike_live_LNL.json")

    # Sleep 3s
    time.sleep(3)

    # 3. Local trains lookup
    print("\n--- Calling /lookup/trains/local?city=Mumbai ---")
    local_status, local_data = query_endpoint(f"{BASE_URL}/lookup/trains/local", params={"city": "Mumbai"})
    with open(DATA_DIR / "_spike_local_mumbai.json", "w", encoding="utf-8") as f:
        json.dump(local_data, f, indent=2)
    print("Saved to backend/data/_spike_local_mumbai.json")


if __name__ == "__main__":
    main()
