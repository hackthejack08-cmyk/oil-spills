# OSI — Oil Spill Intelligence (SIH26143 · NTRO)

Satellite → U-Net segmentation → look-alike rules → geodesic geometry → backward drift (origin window × ellipses) → AIS cleaning/tracks → explainable vessel correlation → GIS evidence dashboard.  
Zero-cost, open-source, **runs fully offline** on a laptop.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r backend/requirements.txt
./run.sh                 # http://localhost:8000  → click "Run offline demo"
# or: docker compose up --build
cd backend && pytest -q  # 13 tests (offline, mocked) incl. full chain (culprit must rank #1)
```

* Full research + specification: `docs/RESEARCH_REPORT.md` (32 sections, evidence-tagged).
* Bundled demo data is **synthetic** (scene, forcing, AIS) — labelled as such everywhere.
* Drop a trained checkpoint at `backend/models/unet_s1_oil.pt` (see `backend/training/train_unet.py`, Zenodo CC BY 4.0 dataset) to replace the classical fallback detector.
* Scores are *correlation with evidence*, never proof of responsibility.

## Real-data mode (Phase 2 – live connectors)

Page **7 Data Sources** in the dashboard wires the pipeline to real services. All are free; those needing a
(free) account are marked. Status of every connector is shown live in the UI (`GET /api/data/status`).

| Connector | Used for | Account | Verified live (2026-09-05) |
|---|---|---|---|
| Copernicus Data Space OData catalogue | Sentinel-1 IW GRD search | none | yes – 4 S1A products returned for 72.6E 18.9N, Mar 2025 |
| CDSE product download → pure-Python σ⁰ calibration (`integrations/s1_calibrate.py`) | real SAR scenes | **free CDSE account** (`OSI_CDSE_USER/PASSWORD`, monthly download quota) | download path implemented, **not exercised** (no credentials in the sandbox) |
| HYCOM GOFS 3.1 / ESPC-D-V02 OPeNDAP (tds.hycom.org) | surface currents 2018-12 → today (+8-day forecast) | none | yes – 2023 and 2025 subsets fetched in ~3 s |
| Open-Meteo ERA5 archive | 10 m wind grid | none (CC BY 4.0, non-commercial free tier) | yes |
| Copernicus Marine (`copernicusmarine`) | alternative currents | free CMEMS account | implemented, not exercised |
| NOAA MarineCadastre daily zips | historical AIS, US waters | none (CC0) | yes – 2023-06-15 (329 MB, streamed+cached) → 91 684 msgs / 454 vessels in 0.6° box |
| Danish Maritime Authority daily CSV | historical AIS, Danish waters | none | implemented, not exercised (files ≈ 2 GB) |
| aisstream.io WebSocket | **live** AIS recording only (no history) | free key `OSI_AISSTREAM_KEY` | implemented, not exercised |

Limits to know: MarineCadastre 2024+ files were **404** at the 2009–2023 URL during verification — the code tries
alternative hosts and otherwise tells you to export from AccessAIS; there is **no free global historical AIS**
(Indian waters require a licensed feed or your own receiver). Long-running fetches run as background jobs
(`/api/data/jobs`) with progress bars.

Walkthrough without any account: `demo/samples/README.md` (synthetic slick placed in the Gulf of Mexico → real
HYCOM/ERA5 forcing → real 2023 AIS). Credentials: copy `.env.example` → `.env`.

Tests: `pytest backend/tests` (15 offline, mocked); `OSI_LIVE_TESTS=1 pytest backend/tests/test_integrations.py -k live`.
