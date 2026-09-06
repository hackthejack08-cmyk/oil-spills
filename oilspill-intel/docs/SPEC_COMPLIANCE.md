# SIH 26143 – specification compliance matrix (verified 2026-09-05)

Legend: ✅ working & verified live in this build · 🟡 working with stated limitation · ❌ not possible / not claimed

| PS requirement | Status | Where | Evidence / limitation |
|---|---|---|---|
| **(a) Detect oil spill from SAR imagery** | ✅ | `sar/preprocess.py`, `sar/segment.py` | Real S1A IW GRD scene (2025-03-20, off Mumbai) streamed from Planetary Computer, calibrated to σ⁰ (thermal noise, LUT, GCP geocoding RMS 0.97 px) and segmented in 5 s. Detector = adaptive dark-spot baseline (no trained weights shipped); U-Net path activates when `models/unet_s1_oil.pt` exists (`training/train_unet.py`, CC BY 4.0 dataset). **Measured on real data** (docs/EVALUATION.md): GlobalOSD-SAR ship-slick benchmark n=24/21 → detection 83 % @ possible_oil, false-alarm 29 %, AUC 0.90 (small sample, CI wide); Wakashio 2020 & Taylor MC-20 slicks now `probable_oil`. |
| EO (optical) imagery | 🟡 | — | Architecture accepts any 1–2 band GeoTIFF but no optical-specific model; SAR is the primary sensor per literature (§4 report). Not claimed. |
| Characterise: geometric properties | ✅ | `geometry/slick.py` | Geodesic area (local AEQD), perimeter, length/width/orientation, elongation, compactness, centroid, distance-to-shore, mean contrast dB. |
| Look-alike discrimination | ✅ | `sar/lookalike.py` | Rules R1–R5, contrast-aware since phase 4 (coastal penalties scale down at ≥4 dB; sea-only background ring for contrast). Known residual false positives: harbour wind-shadow (Mumbai), river plumes (Mississippi). Wind window check (2.5–10 m/s) from real ERA5. |
| Age estimation "if feasible" | 🟡 | Drift page "Origin window" | **No single-image age method exists (verified literature)**. System outputs an *age-hypothesis set* (1 h … N h) each with its own origin ellipse; never a fake age. |
| **(b) Trace slick back to origin (point + time)** | ✅ | `drift/engine.py`, `pipeline.hindcast` | Backward Lagrangian particles (wind factor 2–4 %, 10–20° Coriolis deflection hemisphere-aware, beaching on Natural Earth coastline) on **real HYCOM currents + real ERA5 wind** (built in ~3 s, no account); output = per-hour origin hypotheses with 50/90 % ellipses; stated as modelled, not exact. |
| Predict future flow | ✅ | same, `forward_hours` | Forward run on the same forcing incl. HYCOM 8-day forecast when t_obs is recent. |
| Oceanographic + meteorological data | ✅ | `integrations/metocean.py` | HYCOM (2018-12→now+8 d), Open-Meteo ERA5 (1940→now-5 d); CMEMS optional with free account. |
| **(c) Historic AIS retrieval** | 🟡 | `integrations/ais_sources.py`, upload | MarineCadastre (US, verified 2023) and DMA (DK) fetched automatically; **no free global archive exists** → other regions via CSV upload/licensed feed (documented). aisstream = live only. |
| Reconstruct traffic in space-time window | ✅ | `ais/pipeline.py` | Cleaning (dupes, impossible speed, bad MMSI), track building, gap detection, dead-reckoning across gaps, spatio-temporal window from origin hypotheses. Verified on 91 684 real messages / 454 vessels. |
| Filter irrelevant traffic | ✅ | `ais/correlate.py` | Window filter + spatial-consistency gate (necessary condition). |
| Score suspects: proximity, trajectory, behaviour | ✅ | `ais/correlate.py` | Evidence terms: spatial, temporal, trajectory alignment with slick axis, behavioural anomalies (AIS gap, speed drop, course change), type prior. Weighted, explainable, with limitations list per candidate. |
| Uncertainty + explainability | ✅ | Ranking / Evidence pages | Per-candidate evidence breakdown, ellipses per hypothesis, correlation ≠ proof banner, evidence ledger with SHA-256 of inputs. |
| Visual interface | ✅ | `frontend/` | 7 pages, Leaflet, offline-capable, live connector status, background jobs with progress. |
| Automated pipeline | ✅ | `/api/demo/run`, jobs | One-click demo; real chain is 5 clicks (search → fetch → analyse → forcing → hindcast). |
| Zero-cost / open source | ✅ | `LICENSE_AUDIT` §22 | All deps permissive/GPL-compatible; all data sources free; nothing can bill without exceeding free quotas. |
| Runs on user's device | ✅ | `app.py`, `run.sh`, `run.bat`, Docker | Verified clean-venv start; 2 GB RAM sandbox handles a 0.6° S1 AOI at 40 m. |

## Known gaps (honest)
1. No trained CNN weights are shipped; the measured numbers (docs/EVALUATION.md) are for the untrained adaptive baseline on n≈45 chips – indicative, not operational accuracy. Segmentation IoU unmeasured (mask dataset too large to fetch here).
1b. Hindcast positional error and attribution precision are **unvalidated** (no free ground truth); see EVALUATION.md §5.
2. Indian-waters AIS requires a data feed the team must obtain; the software side is ready.
3. Calibration geolocation vs SNAP not cross-validated (expected ≲ 1 pixel at 40 m, from GCP fit RMS).
4. Drift model is a built-in Lagrangian scheme (wind factor 2–4 % with deflection, current 100 %, diffusion, beaching; no weathering); OpenDrift/PyGNOME adapters are documented but not bundled (heavy deps).
