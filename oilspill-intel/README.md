# OSI — Oil Spill Intelligence (SIH26143 · NTRO)

Satellite → U-Net segmentation → look-alike rules → geodesic geometry → backward drift (origin window × ellipses) → AIS cleaning/tracks → explainable vessel correlation → GIS evidence dashboard.  
Zero-cost, open-source, **runs fully offline** on a laptop.

```bash
python app.py            # one command: creates .venv, installs deps, starts http://localhost:8000
                         # Windows: double-click run.bat · Linux/macOS: ./run.sh · or: docker compose up --build
cd backend && pytest -q  # 16 offline tests (mocked) incl. full chain (culprit must rank #1)
OSI_LIVE_TESTS=1 pytest -q backend/tests/test_integrations.py -k live   # 3 live tests against real services
```
Full install guide & troubleshooting: `docs/INSTALL.md`. Spec compliance matrix: `docs/SPEC_COMPLIANCE.md` · measured results on real S1 data: `docs/EVALUATION.md`.

**No account or API key is needed for the complete real-data chain**: Sentinel-1 via Microsoft Planetary
Computer (STAC + COG, AOI streamed & calibrated in ~20–60 s) → HYCOM currents + ERA5 wind → hindcast/forecast →
AIS (US/DK archives auto-fetched; other regions via CSV upload) → ranking.

* Full research + specification: `docs/RESEARCH_REPORT.md` (32 sections, evidence-tagged).
* Bundled demo data is **synthetic** (scene, forcing, AIS) — labelled as such everywhere.
* Drop a trained checkpoint at `backend/models/unet_s1_oil.pt` (see `backend/training/train_unet.py`, Zenodo CC BY 4.0 dataset) to replace the classical fallback detector.
* Scores are *correlation with evidence*, never proof of responsibility.

## Real-data mode (Phase 2 – live connectors)

Page **7 Data Sources** in the dashboard wires the pipeline to real services. All are free; those needing a
(free) account are marked. Status of every connector is shown live in the UI (`GET /api/data/status`).

| Connector | Used for | Account | Verified live (2026-09-05) |
|---|---|---|---|
| **Microsoft Planetary Computer** (STAC + COG, default) | Sentinel-1 IW GRD search **and pixel access** | **none, no quota** | yes – S1A 2025-03-20 off Mumbai: 0.6° AOI calibrated to σ⁰ in 20 s, peak RAM ≈ 520 MB |
| Copernicus Data Space OData catalogue | Sentinel-1 IW GRD search (fallback) | none | yes – 4 S1A products returned for 72.6E 18.9N, Mar 2025 |
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
