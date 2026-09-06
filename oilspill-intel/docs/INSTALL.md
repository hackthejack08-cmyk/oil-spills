# Running OSI on your own machine

Everything below is free; **no accounts or API keys are needed for the full pipeline** (satellite → detection →
drift → AIS → ranking). Optional accounts only add alternative data providers.

## Requirements
| | Minimum | Comfortable |
|---|---|---|
| OS | Windows 10/11, macOS 12+, Ubuntu 20.04+ | any |
| Python | 3.10 – 3.13 (python.org, tick "Add to PATH" on Windows) | 3.11/3.12 |
| RAM | 4 GB (real S1 AOI ≤ 0.6°, downsample 4) | 8 GB+ |
| Disk | 3 GB (deps ≈ 1 GB CPU-torch, cache for AIS/S1) | 10 GB |
| Internet | only for real-data pages; the offline demo needs none | — |
| GPU | not required | optional, for training only |

## Option A – one command (recommended)
```bash
git clone <this repo> oilspill-intel && cd oilspill-intel
python app.py            # Windows: double-click run.bat   |  Linux/macOS: ./run.sh
```
First run creates `.venv`, installs dependencies (2–4 min), generates the synthetic demo data and starts the
server. Open **http://localhost:8000**. Later runs start in ~3 s. Change port: `PORT=9000 python app.py`.

## Option B – Docker
```bash
docker compose up --build        # http://localhost:8000 ; data persists in the osi_runtime volume
```
Optional credentials: `cp .env.example .env` and fill in what you have (compose reads it automatically).

## Option C – manual
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r backend/requirements.txt
python demo/make_demo_data.py
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## First 10 minutes
1. **1 Mission → "Run offline demo investigation"** – synthetic end-to-end run (~4 s, no internet).
2. **7 Data Sources → "Search IW GRD scenes"** with any coastal point/date (default: off Mumbai, Mar 2025) → **Fetch +
   calibrate** (Planetary Computer, ~20–60 s, no account). The scene appears in **2 Satellite → Scene**.
3. **2 Satellite → Run preprocessing + segmentation**, then **3 Drift → Build real forcing → Run hindcast**.
4. **4 AIS**: upload a CSV of AIS data for the scene window (see "AIS reality check" below) or, for US/Danish
   waters, use **Fetch AIS**. Then **5 Ranking / 6 Evidence**.

## Data providers used (all free)
| Data | Provider | Account | Limits |
|---|---|---|---|
| Sentinel-1 GRD (search + pixels) | Microsoft Planetary Computer STAC/COG | none | none published; anonymous SAS token auto-fetched |
| Sentinel-1 GRD (alternative) | Copernicus Data Space | free account for download | monthly quota |
| Ocean currents | HYCOM GOFS 3.1 / ESPC-D-V02 OPeNDAP | none | 2018-12 → today (+8-day forecast) |
| Ocean currents (alt.) | Copernicus Marine | free account | — |
| 10 m wind | Open-Meteo ERA5 archive | none | 10 000 req/day, non-commercial (CC BY 4.0) |
| Historical AIS | NOAA MarineCadastre (US), Danish Maritime Authority (DK) | none | US 2009–2023 direct; large daily files |
| Live AIS | aisstream.io | free key | real-time only |
| Coastline / land mask | Natural Earth 10 m (bundled) | none | public domain |

### AIS reality check (important for Indian waters)
There is **no free global historical AIS archive**. For Indian EEZ investigations you need one of:
(a) an institutional feed (Indian Coast Guard / DG Shipping / NTRO's own), (b) a licensed provider export
(Spire, MarineTraffic, Kpler…), or (c) your own AIS receiver logs. Any CSV with
`MMSI, BaseDateTime, LAT, LON, SOG, COG[, VesselName, IMO, VesselType]` (MarineCadastre schema) or the
DMA schema uploads directly on page 4. The system never fabricates AIS; synthetic AIS is labelled everywhere.

## Troubleshooting
* `torch` install fails / slow → use the CPU index URL shown above; on Apple Silicon plain `pip install torch` works.
* `rasterio`/GDAL errors on Windows → `pip install rasterio` wheels from PyPI work on Python ≤ 3.13 x64; avoid 32-bit Python.
* `MemoryError` / job "estimated X MB needed" → raise downsample (4→8) or shrink the AOI on page 7.
* Sentinel-1 search returns nothing → S1B ended Dec 2021; S1A/S1C revisit is 6–12 days; widen the date window.
* Planetary Computer timeouts → retried automatically (4×); try again in a minute.
* Port in use → `PORT=9000 python app.py`.
