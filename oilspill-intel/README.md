# OSI — Oil Spill Intelligence (SIH26143 · NTRO)

Continuous Sentinel-1 catalogue watch → every new AOI image → multiple-slick segmentation → cross-pass incident grouping → drift → configured AIS matching → explainable alerts.

The monitor deduplicates catalogue product IDs, processes every new AOI acquisition, retains multiple slicks from one image and links repeat-pass detections into incidents. A one-image upload remains available for ad-hoc review. See `docs/CONTINUOUS_MONITORING.md`.

**Public SIH judge demo:** https://public-demo-one.vercel.app — read-only bundled synthetic case. The full FastAPI build below enables real uploads/connectors and must be secured before operational deployment.

The judge link is a static, deterministic replay, so it does not depend on a sleeping backend. Select **Arabian Sea synthetic replay** or **Run historical replay**, then pause, restart or change speed while the six processing stages appear. Map controls independently show SAR, a visual-only contrast preview, the suspected slick, modelled drift and historical AIS. Observed, processed and modelled products are labelled separately; vessel ranking is an investigation lead, not proof of responsibility.

```bash
python app.py            # one command: creates .venv, installs deps, starts http://localhost:8000
                         # Windows: double-click run.bat · Linux/macOS: ./run.sh · or: docker compose up --build
cd backend && pytest -q  # 19 offline tests incl. full chain (culprit must rank #1)
OSI_LIVE_TESTS=1 pytest -q backend/tests/test_integrations.py -k live   # 3 live tests against real services
python tools/check_public_demo.py  # fast static-deployment regression check
```
Full install guide & troubleshooting: `docs/INSTALL.md`. Spec compliance matrix: `docs/SPEC_COMPLIANCE.md` · measured results on real S1 data: `docs/EVALUATION.md`.

**No account or API key is needed for Sentinel-1, HYCOM currents, Open-Meteo wind, detection and drift.** Historical AIS availability is regional: US/Danish archives can be fetched, while Indian/global waters require an authorized provider or agency CSV. The system returns an honest partial result when AIS is unavailable.

* Full research + specification: `docs/RESEARCH_REPORT.md` (32 sections, evidence-tagged).
* Bundled demo data is **synthetic** (scene, forcing, AIS) — labelled as such everywhere.
* Drop a trained checkpoint at `backend/models/unet_s1_oil.pt` (see `backend/training/train_unet.py`, Zenodo CC BY 4.0 dataset) to replace the classical fallback detector.
* Scores are *correlation with evidence*, never proof of responsibility.

## Real-data mode (Phase 2 – live connectors)

Page **7 Data Sources** in the dashboard wires the pipeline to real services. All are free; those needing a
(free) account are marked. Status of every connector is shown live in the UI (`GET /api/data/status`).

| Connector | Used for | Account | Verification status |
|---|---|---|---|
| **Microsoft Planetary Computer** (STAC + COG, default) | Sentinel-1 IW GRD search **and pixel access** | **none, no quota** | yes – S1A 2025-03-20 off Mumbai: 0.6° AOI calibrated to σ⁰ in 20 s, peak RAM ≈ 520 MB |
| Copernicus Data Space OData catalogue | Sentinel-1 IW GRD search (fallback) | none | yes – 4 S1A products returned for 72.6E 18.9N, Mar 2025 |
| CDSE product download → pure-Python σ⁰ calibration (`integrations/s1_calibrate.py`) | real SAR scenes | **free CDSE account** (`OSI_CDSE_USER/PASSWORD`, monthly download quota) | download path implemented, **not exercised** (no credentials in the sandbox) |
| HYCOM GOFS 3.1 / ESPC-D-V02 NCSS (ncss.hycom.org) | surface currents 2018-12 → today (+8-day forecast) | none | yes – live subset check passed 2026-09-19 |
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

Real upload test pack: `demo/samples/globalosd_diverse20/README.md` (19 Sentinel-1 GeoTIFFs with oil/look-alike point labels and expected baseline results). Backend/model plan and current limitations: `docs/PRD_BACKEND_MODEL_AND_DATA.md` and `docs/BACKEND_MODEL_HANDOFF.md`.

Tests: `cd backend && pytest -q` (19 passed, 3 credential/network checks skipped on 2026-09-24); `OSI_LIVE_TESTS=1 pytest backend/tests/test_integrations.py -k live`.
