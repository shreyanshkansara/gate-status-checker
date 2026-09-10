# Gate Status Checker

A minimal web application that estimates the open/closed status of a railway level crossing near Khandala, Maharashtra, India.

---

## 📍 Location & Scope

There are physically two adjacent level crossing (LC) gates here:
- **Gate No. 30**
- **Gate No. 31**

Because of their immediate proximity, they function together operationally and are treated as a single unified status unit: **`gate30_31`**. One status check represents both gates.

The crossing is situated between:
- **Khandala Station** (IR code: `KAD`)
- **Lonavala Station** (IR code: `LNL`)

on the Indian Railways Central Railway line (Mumbai–Pune corridor).

---

## 🔒 Hard Constraints

1. **Single Fixed Gate Configuration**:
   No map UI, no geolocation lookups, no add/edit/remove controls. The crossing configuration is hardcoded to `gate30_31`.
2. **On-Demand Checking Only**:
   Strictly zero background polling, intervals, or auto-fetch loops. External live queries happen **only** when the user explicitly clicks the **"Check Status"** button.
3. **Local Train Schedule Cache**:
   Train timetable and schedule data is cached locally as a JSON file and reused across checks. It is fetched only when missing or older than 14 days.
4. **Single External API**:
   The only external API permitted is [RailRadar](https://api.railradar.in/v1), called minimally on explicit user demand.
5. **Lightweight, Zero-Database Stack**:
   - Backend: Python + FastAPI
   - Frontend: Vanilla HTML, CSS, JavaScript
   - Storage: Local plain JSON files (no DB server required)
6. **Estimated Distances Only**:
   Gate distances from adjacent stations are rough visual estimates (not surveyed or official Indian Railways chainage values) and are surfaced with an explicit "Estimate" disclaimer wherever displayed.
7. **Flat-Speed Modeling Limitation**:
   Gate transit times are computed using a uniform average speed constant (default 36 km/h) across all trains and directions. Because trains negotiating the Bhor Ghat incline experience substantial operational variations—including steep 1:37 gradients, catch sidings, banker locomotive attachments, speed restrictions, and unscheduled halts (e.g. observed 13-minute holds at Khandala)—and because live delay feeds can lag behind a train's physical movement by several minutes around such holds, the displayed gate status and train ETAs represent reasonable operational approximations, not precision signaling telemetry.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+ installed

### 2. Environment Setup
Create a virtual environment and install backend dependencies:
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r backend/requirements.txt
```

### 3. Configure API Key
Copy the example environment file:
```bash
cp .env.example .env
```
Add your RailRadar API key in `.env`:
```env
RAILRADAR_API_KEY="your_railradar_api_key_here"
```

### 4. Run the Development Server
```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```
Then navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

---

## 📁 Project Structure

```
├── backend/
│   ├── main.py              # FastAPI app & endpoints
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── index.html           # Minimal UI skeleton
│   ├── style.css            # Styles & responsive design
│   └── app.js               # Client-side logic (on-demand checks only)
├── data/
│   └── .gitkeep             # Local 14-day timetable cache target
├── .env.example             # Environment variable template
├── .gitignore               # Ignored files (venv, keys, local cache)
└── README.md
```
