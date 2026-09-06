# SIH26143 — Oil-Spill Detection & Vessel Attribution Platform
## Research, Architecture and Implementation Specification (v1.0, 2026-09-05)

**Problem:** Leveraging satellite imagery to determine oil spills at sea along with AIS data correlations to identify the vessel responsible (NTRO, Software, Disaster Management).

**Evidence tags used throughout:** **[V]** verified against a primary source · **[R]** engineering recommendation · **[A]** assumption · **[X]** experimental · **[NV]** not verified.

A runnable reference implementation accompanies this document (`oilspill-intel/`). Everything described in §17–§24 exists as code and passes its test-suite; the only component not shipped is the *trained* CNN checkpoint, because the 90 GB training dataset must be downloaded and trained by the team (the pipeline falls back to a classical detector and says so on-screen).

---

## 1. Executive summary

Build a **local, open-source, offline-capable investigation platform** whose evidence chain is:

Sentinel-1 GRD σ⁰ (VV+VH, dB) → minimal preprocessing → **U-Net (ResNet-18 encoder) segmentation** → **rule-based look-alike rejection** with wind/shape/contrast rules → **geodesic slick geometry** → **stochastic backward Lagrangian hindcast** over a 1–12 h age window driven by Copernicus-Marine currents + ERA5 wind (built-in engine; OpenDrift optional) → **origin envelope (time window × uncertainty ellipses)** → **AIS cleaning + trajectory reconstruction** (MarineCadastre / DMA / synthetic) → **explainable rule-based correlation score** across six evidence families → **GIS evidence dashboard** (FastAPI + Leaflet, SQLite, Docker).

Key honesty points the judges will hear from us: spill *age* is not observable from a single SAR scene, so we return a window, not a number; the origin is a probability cloud, not a point; the vessel score is *correlation with evidence*, not proof; and until the CNN is trained on the Zenodo dataset the demo runs on a classical detector that is clearly labelled as such.

---

## 2. Problem decomposition & dependencies

| Module | Responsibility | Depends on | Implementation |
|---|---|---|---|
| A Satellite acquisition | CDSE (real) / bundled GeoTIFF (demo) | – | manual download + `/api/satellite/upload` |
| B SAR preprocessing | dB conversion, clip/normalise, sea mask, tiling | A | `app/sar/preprocess.py` |
| C Detection / D Segmentation | pixel probability map, connected objects | B | `app/sar/segment.py` |
| E Look-alike rejection | rules R1–R5 (+ optional classifier) | D, F, I | `app/sar/lookalike.py` |
| F Geometry | polygon, geodesic area, axes, orientation | D | `app/geometry/slick.py` |
| G Age estimation | declare unobservable; provide window | – | `pipeline.analyze_scene` |
| H Ocean data / I Met data | currents, 10 m wind (CF NetCDF) | – | `Forcing.from_netcdf` |
| J Hindcast / K Forecast | backward/forward particle clouds, ellipses | F, H, I | `app/drift/engine.py` |
| L AIS acquisition | CSV (MarineCadastre schema) | – | upload / demo file |
| M Cleaning / N Filtering / O Trajectories | validation, gaps, spatio-temporal filter | L, J | `app/ais/pipeline.py` |
| P Correlation / Q Ranking / R Uncertainty | evidence families, weights, limitations | J, O, F | `app/ais/correlate.py` |
| S GIS / T Web | 6-page dashboard, REST API | all | `frontend/`, `app/main.py` |
| U Offline demo | synthetic scene+forcing+AIS, one-click run | all | `demo/make_demo_data.py`, `/api/demo/run` |
| V Evaluation | metrics, tests, training script | C–Q | `tests/`, `training/train_unet.py` |
| W Security/integrity | upload validation, SHA-256 ledger | T | `main.py`, `db.py` |

Critical path: A→B→D→F→J→N→P→S. E and G are side branches that *modify confidence* rather than block the chain.

---

## 3. Research findings (Sentinel-1 / SAR)

* **[V]** Sentinel-1 is a C-band SAR; IW mode is the default over coastal seas; IW GRD-High has ~20×22 m resolution at 10×10 m pixel spacing; EW GRD-H 50 m / 25 m pixels. Pixel spacing ≠ resolution (ESA STEP forum, ESA product spec) [ESA-1].
* **[V]** Noise-equivalent σ⁰ (NESZ) is specified at −22 dB or better (SentiWiki) [ESA-2]. Ocean cross-pol (VH) backscatter is typically near this floor, so **VH contributes little discrimination for oil vs. sea; VV carries the signal**. This is why the reference dataset (Krestenitis) is VV-only and why our model treats VH as an auxiliary channel [R].
* **[V]** Oil damps Bragg waves → dark patches; detection is reliable roughly in the **2–3 m/s to ~10 m/s wind window**; below it, calm sea produces dark look-alikes; above it, thin oil disperses (Brekke & Solberg 2005 via Yang et al. 2024; Nature Sci. Rep. 2025) [LIT-1, LIT-2]. Our R1 rule encodes exactly this.
* **[V]** SAR is cloud-independent and day/night (Copernicus Observer) [CO-1].
* **[V]** GRD vs SLC: SLC keeps phase and needs debursting/multilooking; for oil-slick detection the community, EMSA CleanSeaNet and all public datasets use GRD amplitude. **Decision: GRD only** [R].
* **[V]** Thermal-noise removal + radiometric calibration to σ⁰ are standard GRD processing steps (e.g. Sentinel-Hub/Planet processing chain applies thermal-noise removal to all GRD) [SH-1]. Terrain correction is irrelevant over flat sea except for geocoding (ellipsoid-corrected geocoding suffices) [R].
* **Minimum pipeline decision [R]:** σ⁰ (thermal-noise-removed, ellipsoid-geocoded) → dB → clip [−35, 5] dB → normalise → land mask → 256 px tiles (32 px overlap). Speckle filtering: **not** applied before the CNN (the network learns it; Krestenitis-family baselines don't filter); optional 5×5 median only for the classical fallback. Incidence-angle normalisation: **[X]** optional flag; the Zenodo training data is not normalised, so applying it at inference alone would introduce a domain shift.
* Comparison of inputs: VV only (proven, all benchmarks) vs VV+VH (Zenodo dataset ships both; VH adds a weak but real cue on ships/land) vs derived polarimetric features (H/α, needs SLC, not on GRD) vs texture (GLCM helps some studies, e.g. OSDA-SAM uses GLCM homogeneity/variance [LIT-3], but adds preprocessing cost). **Decision: VV+VH dB, no hand-crafted texture** — the CNN learns texture; keep texture as an ablation.

---

## 4. Dataset comparison

| Dataset | Images | Res./pixel | Pol. | Labels | Coverage | Oil / no-oil / look-alike | Licence | Size | Suitability |
|---|---|---|---|---|---|---|---|---|---|
| **Trujillo-Acatitla et al. 2024 (Zenodo I/II/III)** **[V]** | Train/val: 1200 oil + 685 no-oil + 685 look-alike; Test: 150+150+150 (2048×2048×2) | S1 GRD, σ⁰ dB, georeferenced | VV+VH | Binary pixel masks (oil=1; look-alike/no-oil masks all 0) | NOAA + EMSA CleanSeaNet events (global) | Yes / Yes / Yes | **CC BY 4.0** | 40.7 + 45.9 + 9.9 GB | **Primary train/val/test** |
| Krestenitis et al. 2019 (MKLab/M4D) **[V]** | 1112 (1250×650), fixed 1002/110 split | S1 GRD | VV | 5-class (sea, oil, look-alike, ship, land) | Mediterranean, EMSA 2015-2017 | Yes / – / Yes | **On request via supervisor e-mail; not open** | small | Benchmark reference only; request in parallel |
| Yang & Singha 2025 Eastern-Med (PANGAEA) **[V]** | 1365 oil patches (3225 objects) + 2290 look-alike/other patches, 8-bit JPG + Pascal-VOC XML boxes | S1 2019 | VV | Bounding boxes (not masks) + k-means look-alike sub-groups | Eastern Mediterranean 2019 | Yes / – / Yes | Published via PANGAEA/ESSD (CC BY expected; **[NV]** exact terms – check landing page) | moderate | **External generalisation + look-alike classifier test** |
| Deep-SAR Oil Spill (SOS) / Refined SOS (Zenodo 15298010) **[V]** | 3877 PALSAR + 4193 S1 patches; refined masks (38 %/50 % corrected) | PALSAR HH 12.5 m (Gulf of Mexico 2010) + S1 (Persian Gulf) | HH / VV | Binary masks | Two regions | Yes / Yes / – | CC BY 4.0 (refined Zenodo) | 1.2 GB | Secondary; S1 part useful for extra training; PALSAR is L-band → domain shift |
| GlobalOSD-SAR (Zenodo 15286918) **[V]** paper; contents **[NV]** | 100 329 oil + 100 134 look-alike images 2014-2020 | S1 | VV | thresholded + snowballed masks | Global | Yes / Yes / Yes | **[NV]** | large | Promising for scale-up; verify licence before use |

**Split policy [R]:** Zenodo Parts I+II → train/val split **by scene id and 1°×1° cell** (implemented in `train_unet.py::split_by_group`), Part III = held-out test exactly as published; PANGAEA Eastern-Med = external test. Never random-split tiles: tiles from the same 2048² scene share speckle statistics, wind field and event → optimistic IoU. Also exclude any Part III event dates from training if you augment with SOS-S1.

---

## 5. Model comparison

Published numbers on the Krestenitis benchmark (mIoU over 5 classes; oil-class IoU in brackets) **[V]**: U-Net(ResNet-101) 64.97 (53.8); LinkNet 64.79 (51.5); PSPNet 55.60; DeepLabv3+(MobileNetV2) 65.06 (53.4), 117 ms/img; YOLOv8-SAM 64.89; SAM-OIL 69.52 (51.6); FA-MobileUNet (MobileNetV3, 14.9 M) 78.93 (70.4) [LIT-4, LIT-5, LIT-6]. Caveat: numbers come from different papers with different training regimes; treat as indicative.

Scoring 1–5 (higher = better) on the 20 criteria condensed into groups; ★ = our final.

| Candidate | Accuracy evidence on S1 | Look-alike handling | Pretrained weights | CPU feasibility | Size | Licence | Integration | Credibility | Demo suitability | Total (max 45) |
|---|---|---|---|---|---|---|---|---|---|---|
| ★ **U-Net, ResNet-18/34 enc. (smp)** | 4 | 3 (+rules) | 5 (ImageNet enc.) | 5 | 5 (14–24 M) | 5 (MIT) | 5 | 5 | 5 | **42** |
| U-Net++ (smp) | 4 | 3 | 5 | 4 | 4 | 5 | 5 | 4 | 4 | 38 |
| DeepLabV3+ MobileNetV2 (smp) | 4 | 3 | 5 | 5 | 5 | 5 | 5 | 5 | 4 | 41 |
| FA-MobileUNet (paper) | 5 | 4 | 1 (no public weights **[NV]**) | 5 | 5 | ? | 2 | 3 | 3 | 33 |
| SegFormer-B0/B2 | 4 | 3 | 3 | 3 | 4 | **2** (NVIDIA weights non-commercial; HF Apache code) | 4 | 4 | 3 | 30 |
| SAM / SAM2 zero-shot | 2 (needs prompts; poor on low-res overhead imagery [LIT-7]) | 2 | 5 | 1 (ViT-B ~ 90 M+, slow CPU) | 1 | 4 (Apache-2) | 2 | 3 | 2 | 22 |
| Domain-adapted SAM (OSDA-SAM / SAM-OIL) | 4 | 4 | 2 (research code) | 1 | 1 | ? | 1 | 4 | 2 | 23 |
| YOLOv8/11-seg (Ultralytics) | 3 | 3 | 5 | 4 | 4 | **1 (AGPL-3)** | 4 | 3 | 4 | 31 |
| EO foundation models (Prithvi, Clay, DOFA) | 2 (optical-centric; S1 SAR support partial) | 2 | 4 | 2 | 2 | 4 | 2 | 3 | 2 | 23 |

* **BEST ACCURACY:** a domain-adapted SAM two-stage system (ConvNeXt-T screen + OSDA-SAM) per 2026 literature — but GPU-bound and not reproducible from public weights.
* **BEST BALANCED / FINAL:** **U-Net with ResNet-18 encoder, 2-channel input, from `segmentation_models_pytorch` (MIT)**, plus a rule-based look-alike stage. Reasons: proven family on S1 benchmarks, ImageNet-pretrained encoder (adaptable to 2-ch via smp), trains in hours on a free Colab/Kaggle GPU or overnight on CPU, ~45 MB weights, 0.3–0.6 s per 256² tile on a laptop CPU, permissive licence, one-line model definition → reproducible.
* **BEST LOW-HARDWARE:** same U-Net with `timm-mobilenetv3_large_100` encoder (swap one string).
* **Classification / detection stage:** a patch-level classifier (the two-stage design of the Zenodo authors) is worthwhile *after* the segmenter is working; we reserve it as Approach C add-on (hook `classifier_prob` already exists in `lookalike.assess`).

---

## 6. Final ML architecture

```
σ⁰ VV,VH (dB) ─► normalise ─► 256² tiles ─► U-Net(ResNet-18) ─► prob map ─► threshold τ ─► morphology ─► objects
                                                                                              │
                     wind (forcing/ERA5) ─┐                                                   ▼
                     geometry (F) ────────┴─► look-alike rules R1–R5 (+opt. classifier) ─► oil-likelihood + label
```
Fallback: if no checkpoint is present, an adaptive dark-spot detector (local-background contrast, logistic score) runs instead, its confidence is capped at 0.75 and the UI shows "baseline detector". This guarantees the demo never dead-ends and makes the *upgrade* (drop a `.pt` file) trivial.

Loss: Dice+BCE (class imbalance). Optimiser: AdamW 3e-4, OneCycle, batch 16, 40 epochs, early stop 8, AMP on GPU, oil-centred crop oversampling 60 %, flips/rot90 only (SAR-safe), mild radiometric jitter — all **starting configurations [A]** to be tuned; threshold τ chosen by validation-IoU sweep (script prints it).

---

## 7. SAR processing pipeline (detailed)

1. **Acquire** (real mode): Copernicus Data Space Ecosystem, free account; S3/OData download within free quota (CDSE documents quota-managed S3 access; Esri notes 12 TB/month free S3 quota **[V]** [CDSE-1, CDSE-2]). Select `S1*_IW_GRDH_1SDV`. Alternative: NASA ASF (free Earthdata login) **[V, widely documented]**.
2. **Calibrate** to σ⁰ with thermal-noise removal: ESA SNAP `gpt` graph (Apply-Orbit → ThermalNoiseRemoval → Calibration σ⁰ → Ellipsoid-Correction-GG → LinearToFromdB → write GeoTIFF) *or* pure-Python `xarray-sentinel` (reads calibration LUTs; Apache-2) **[V]** [XS-1]. Pre-processed alternative: Google Earth Engine `COPERNICUS/S1_GRD` is already σ⁰ dB with thermal-noise removal & terrain correction (free for research **[V]**, requires account and internet — optional).
3. **Read** 2-band GeoTIFF; auto-detect linear vs dB; NaN = nodata.
4. **Sea mask**: NaN + coastline polygon (Natural Earth 10 m or OSM land polygons) rasterised with `rasterio.features.geometry_mask`; the reference code additionally masks σ⁰ > −5 dB (ships/land) for the fallback path.
5. **Normalise** clip [−35, 5] dB → [0,1] (covers slick minima ~−30 dB and bright sea ~0 dB).
6. **Tile** 256×256, 32 px overlap, average logits on overlaps.
7. Output probability map, quicklook PNG, provenance (`sha256` of scene, sensing time, orbit direction, polarisation) stored in `satellite_scenes`.

---

### 7.4 Pure-Python GRD calibration used in real-data mode (Engineering recommendation)

`backend/app/integrations/s1_calibrate.py` avoids SNAP entirely: it reads the GRD measurement TIFF with rasterio,
bilinearly interpolates the **noise LUT** (`annotation/calibration/noise-*.xml`) and the **sigmaNought LUT**
(`calibration-*.xml`), computes σ⁰ = (DN² − η)/A², block-averages by a user factor (default 4 → ~40 m pixels,
which is what the detector needs), converts to dB and geocodes from the annotation **GCP grid** (gdal GCP →
polynomial). Simplifications vs SNAP/xarray-sentinel (state them in any evaluation): no precise-orbit
refinement, no terrain correction (irrelevant over open sea), 200 px border crop instead of border-noise removal,
GCP polynomial geolocation (expected error ≈ tens of metres at 40 m pixels — **Not verified** against SNAP output).

### 7.5 Real-data connectors verified live (Verified fact, 2026-09-05)

CDSE OData search (no account), HYCOM GLBy0.08/ESPC OPeNDAP, Open-Meteo ERA5, MarineCadastre 2023 daily zips all
responded and were integrated end-to-end through the UI (screenshots 7–9). CDSE download, CMEMS, DMA and aisstream
are implemented but unexercised. MarineCadastre 2024+ URLs returned 404 during verification (handled with a clear
error). A synthetic slick relocated to the Gulf of Mexico (`demo/samples`) was used because no real spill scene
could be downloaded without credentials; the real-AIS ranking it produces demonstrates the pipeline only and
is explicitly **not** evidence against any listed vessel. Note the behaviour of the correlation scorer on real
traffic: 454 vessels → spatial consistency is a necessary gate (score × (0.25 + 0.75·s_spatial)), otherwise
vessel-type priors made distant tankers rank first (a defect found and fixed during this test).

## 8. Segmentation → objects

Threshold τ (default 0.5, tuned) → binary opening (1) → closing (8) → hole fill → connected components → drop < 60 px (≈0.006 km² at 10 m) → per-object stats (mean prob, mean contrast dB vs 400-px background). Implemented in `segment.py`.

---

## 9. Look-alike rejection

SAR dark phenomena researched: low-wind cells, biogenic surfactant films, rain cells, internal waves, current fronts/upwelling, ship wakes, wind shadow near coasts, grease ice, atmospheric fronts (Alpers et al. 2017; Brekke & Solberg 2005; Yang & Singha 2025 group look-alikes by k-means exactly for this) **[V]**.

| Approach | Verdict |
|---|---|
| A single segmenter | Baseline; oil/look-alike confusion remains (Krestenitis look-alike IoU ≈ 40–55 %) |
| B detector + segmenter | Good for big scenes (screening); doubles training effort |
| C segmenter + secondary classifier | Strong; the Zenodo authors' own approach; needs patch classifier training |
| **D segmenter + physical/geospatial rules** | **Cheapest, explainable, uses wind & geometry the model never sees** |
| E ensemble/hybrid | Best but heaviest |

**Decision: D now, C as phase-2 add-on (hook present).** Rules (each with penalty and human-readable reason): R1 wind < 2.5 or > 10 m/s; R2 round & large (elongation < 1.6, > 5 km²); R3 contrast < 1.5 dB; R4 touches land mask; R5 very thin linear feature with bright tip (wake vs. fresh discharge — *flag only*). Final `oil_likelihood = seg_conf × Π(1−penalty)`; labels `probable_oil ≥ 0.6`, `possible_oil ≥ 0.35`, else `probable_lookalike`. In the demo scene the injected low-wind patch is correctly downgraded by R2+R3 while the discharge is kept.

---

## 10. Spill geometry

Pixels → `rasterio.features.shapes` in scene CRS → WGS-84 → `pyproj.Geod(ellps="WGS84").geometry_area_perimeter` for **geodesic** area & perimeter → local azimuthal-equidistant projection at the centroid for PCA major/minor axes (length, width, bearing 0–180° from north), elongation, compactness 4πA/P², bbox, pixel count, mean probability, mean contrast, land contact. Unit test verifies a 0.1°×0.1° equatorial square = 123.6 km² within 2 %.

---

## 11. Spill age — verdict

**Not scientifically estimable from a single SAR scene.** Spreading (Fay) depends on unknown volume and oil type; weathering changes damping ratio non-monotonically; SAR cannot measure thickness (Nature 2025 review) **[V]** [LIT-2]. Multi-temporal collocation (S1/S2/S3/Landsat) can bound drift over 4–32 h (Remote Sens. 2024 Qatar study) **[V]** [LIT-8] — that is *tracking*, not ageing. Therefore the system outputs `age_estimate.status = "unavailable"` and works with a **1–12 h age-hypothesis window** (configurable). Shape heuristics (thin straight = fresh discharge along track; feathered/patchy = older) are shown as qualitative notes only **[A]**.

---

## 12. Oceanographic & meteorological data

| Source | Access | Hist. | Res. | Vars | Licence | Offline | Cost class |
|---|---|---|---|---|---|---|---|
| **Copernicus Marine (CMEMS) GLOBAL_ANALYSISFORECAST_PHY_001_024 / GLORYS reanalysis** **[V]** | free account; `copernicusmarine` toolbox (EUPL) | forecast archive & reanalysis to 1993 | 1/12°, hourly surface (analysis) / daily | uo, vo (surface), also Stokes via WAV product | CMEMS licence: free, derivative works for any purpose, attribution "Generated using E.U. Copernicus Marine Service Information; DOI" **[V]** [CM-1] | subset → NetCDF, yes | REQUIRES ACCOUNT / FREE |
| HYCOM GOFS 3.1 **[V]** | OPeNDAP/THREDDS, no account; AWS Open Data reanalysis (`--no-sign-request`) | 1994– | 1/12°, 3-hourly | u, v | US-Gov open | yes | FREE |
| **ERA5 via Open-Meteo Historical API** **[V]** | HTTP, no key, non-commercial free tier | 1940– | 0.25° (9 km IFS since 2017), hourly | u10, v10 | CC BY 4.0 [OM-1] | JSON → cache | FREE WITH LIMITATIONS |
| ERA5 via CDS (cdsapi) **[V]** | free account | 1940– | 0.25° hourly | u10, v10, waves | C3S licence | NetCDF, yes | REQUIRES ACCOUNT |
| Open-Meteo Marine API | HTTP | limited | – | waves, ocean current (model-dependent) | CC BY 4.0 | – | optional |
| GEBCO bathymetry | download | – | 15″ | depth | free | yes | for beaching/shallow masks (optional) |

**Minimum variables:** surface `uo, vo` and `u10, v10` on a lon/lat/time grid (CF NetCDF). Stokes drift is *optional* (folded into wind factor). **Decision: CMEMS currents + ERA5/Open-Meteo wind; HYCOM as no-account fallback.**

---

## 13. Drift / hindcast architecture

| Framework | Backward | Oil module | Uncertainty | Python | Licence | Weight |
|---|---|---|---|---|---|---|
| **OpenDrift (OceanDrift/OpenOil)** **[V]** | yes (negative time step) | OpenOil w/ ADIOS oils | current/wind perturbation, diffusivity | native; `pip install opendrift` (heavy deps: cartopy, roaring-landmask, adios-db) | **GPLv2** [OD-1] | reference model |
| PyGNOME (NOAA) **[V]** | `run_backwards=True` (issue #191 shows caveats) | full weathering | spatial uncertainty movers | conda NOAA channel | Public domain | heavy build |
| OceanParcels | negative dt | generic | custom kernels | pip | MIT | generic |
| PyLag | limited | – | – | – | – | niche |
| **Built-in 2-D stochastic Lagrangian (ours)** | yes | none (not needed backward) | per-particle wind factor U(2–4 %), current σ 0.05 m/s, wind σ 0.5 m/s, K_h 10 m²/s | zero deps | MIT (ours) | default |

Physics: `dx = (u_c + f_w·u_10)·dt + √(2K_h·dt)·ξ`. Wind factor literature: ~3 % of U10 (2.5–4.4 % range; ~⅔ of it is Stokes drift; NAS 2005, Reed et al. 1994) **[V]** [LIT-9]. Weathering is *not* invertible backward, so omitting it for hindcast is correct, not a shortcut [R]. Coriolis deflection 10–20° is omitted (commonly done) **[A]**.

**Decision: built-in engine as default (zero install risk, MIT), OpenDrift adapter provided (`simulate_opendrift`) for teams that install it; GPL isolation by running it as a separate process/service if redistributed.** Outputs per age hour: particle cloud, 50 %/90 % covariance ellipses, spread σ (km), time. Forward forecast uses the same engine with dt > 0. Every result stores engine, parameters, forcing provenance.

---

## 14. AIS pipeline

Sources **[V]**: NOAA MarineCadastre (US waters, 2009–2025, daily CSV + 2024/25 GeoParquet, fields MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign, VesselType, Status, Length, Width, Draft, Cargo, TransceiverClass; CC0/public domain) [MC-1, MC-2]; Danish Maritime Authority (`web.ais.dk/aisdata`, daily CSV, Danish waters) [DMA-1]; aisstream.io (free key, **real-time only, no history**) [AIS-1]; Global Fishing Watch API (account, non-commercial terms) — optional. **No free historical AIS for Indian waters exists** → for Indian scenarios use NTRO/DG-Shipping data at deployment and the synthetic set for demos.

Pipeline (implemented): load → timestamp parse (UTC) → MMSI range check (201–775 M) → lat/lon validity, (0,0) and 91/181 sentinels → SOG 102.3 / COG 360 / HDG 511 sentinels → NaN → exact duplicate drop → sort → implied-speed filter (> 60 kn) → tracks per MMSI → gap list (> 20 min) → spatio-temporal filter (≥1 fix within radius of origin envelope during window **or** a gap bracketing the window — silence is evidence) → candidates. Interpolation is linear only across gaps ≤ 20 min; inside long gaps we *dead-reckon* between the two real fixes and attach ±0.1 km/min uncertainty, reported explicitly as an assumption. Cleaning report (`n_raw/n_kept/dropped{reason}`) is part of the evidence.

---

## 15. Vessel correlation & 16. Explainable ranking

Six evidence families, each 0–1 with a sentence of explanation and raw values:

| Family | Weight | Computation |
|---|---|---|
| spatial | 0.40 | min over age hypotheses h and ±20 min of `exp(−½·(d/σ_h)²)` where d = distance from vessel position at t_obs−h to the h-cloud, σ_h its spread (dead-reckoned positions ×0.9) |
| temporal | 0.15 | `exp(−|Δt|/20 min)` at the best hypothesis |
| trajectory | 0.15 | 1 − angle(track bearing near slick, slick major axis)/90° |
| behaviour | 0.15 | +0.45 AIS gap in window, +0.30 speed change > 4 kn, +0.25 course change > 30° |
| vessel type | 0.10 | tanker 1.0, cargo 0.7, passenger/other 0.4, fishing 0.3 |
| AIS quality | 0.05 | reporting completeness vs 3-min expectation |

Score = weighted mean. Methods compared: rule-based (chosen for MVP — transparent, works with zero labelled cases), logistic regression / random forest / LightGBM (require labelled attributions we do not have; feature vector is already produced, so training is a drop-in), Isolation Forest (useful for the behaviour family on large AIS volumes [X]), graph-based (over-engineering here). **Language:** "high-correlation candidate", "correlation 82 %", never "responsible". Demo result: the synthetic culprit ranks #1 (0.82) ahead of the hard negative that crossed the origin 55 min late (0.78), with the evidence panel showing *why* (dead-reckoned position across a 39-min gap, speed drop, track aligned within 4° of the slick axis).

---

## 16. Uncertainty framework

| Output | Representation |
|---|---|
| Segmentation | per-pixel probability map; per-object mean prob; detector name; baseline capped at 0.75 |
| Boundary/area | threshold sweep ±0.1 gives area interval (phase-2 UI); pixel count and geodesic area reported |
| Look-alike | multiplicative penalties with rules listed |
| Age | window 1–12 h, "unavailable" flag |
| Origin | per-hypothesis 50/90 % ellipses + spread σ; particles rendered |
| Drift params | all stochastic parameters persisted in `drift_runs.params` |
| Vessel score | six sub-scores + explanations + `limitations[]` (gaps, dead-reckoning, sparse AIS, uncalibrated weights) |
| Provenance | SHA-256 for scene, weights, every evidence row |

---

## 17. Complete system architecture

```
┌──────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│ Sentinel-1   │──►│ SAR prep     │──►│ U-Net seg +    │──►│ Look-alike   │
│ GRD σ⁰ (CDSE)│   │ dB/mask/tile │   │ objects        │   │ rules R1–R5  │
└──────────────┘   └──────────────┘   └────────────────┘   └──────┬───────┘
                                                                  ▼
┌──────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│ CMEMS/HYCOM  │──►│ Forcing      │──►│ Backward/forward│◄──│ Geometry     │
│ ERA5/OpenMeteo│  │ CF NetCDF    │   │ Lagrangian      │   │ geodesic     │
└──────────────┘   └──────────────┘   └───────┬─────────┘   └──────────────┘
                                              ▼ origin window × ellipses
┌──────────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│ AIS CSV      │──►│ Clean/tracks │──►│ Spatio-temporal │──►│ Correlation  │
│ (MC/DMA/syn) │   │ gaps         │   │ filter          │   │ + ranking    │
└──────────────┘   └──────────────┘   └────────────────┘   └──────┬───────┘
                                                                  ▼
                       SQLite (evidence ledger, SHA-256) ◄──► FastAPI ◄──► Leaflet dashboard (6 pages)
```
Single Python process; no queue/broker needed (demo chain runs in ~4 s on CPU; a real 25 k×17 k GRD scene takes minutes → run analysis as a `BackgroundTask` with a job id in phase 2).

---

## 18. Web application

**Backend:** FastAPI + Pydantic validation; endpoints:

| Endpoint | Input | Output | Validation / errors | Time |
|---|---|---|---|---|
| `POST /api/investigations` | name, mode | id | 422 on schema | ms |
| `POST /api/satellite/upload` | multipart kind+file | upload_id, sha256 | suffix + TIFF magic bytes, 600 MB cap (413) | s |
| `POST /api/satellite/analyze` | investigation_id, scene('demo'|upload_id), wind_ms | scene, detections[], quicklooks, age_estimate | 400 bad id, 404 missing, 500 wrapped | 3 s (2048²) – minutes (full GRD) |
| `GET /api/spills/{id}` | – | geometry + penalties | 404 | ms |
| `POST /api/drift/hindcast` / `forecast` | detection_id, forcing, hours, forward_hours | hypotheses, ellipses, tracks, params, window | 404 | ~1 s |
| `POST /api/ais/analyze` | ais, radius_km | cleaning report, candidates[], tracks[] | 409 if no hindcast | ~1 s / 10⁴ msgs |
| `GET /api/vessels/candidates`, `/api/vessels/{mmsi}` | investigation_id | ranked evidence | 404 | ms |
| `GET /api/evidence/{inv}` | – | ledger with SHA-256 | – | ms |
| `POST /api/demo/run` | – | whole chain | – | ~4 s |

**Frontend:** vanilla JS + Leaflet 1.9.4 (BSD-2), bundled locally, **no tile server** (Natural Earth coastline + graticule → truly offline). Six pages as specified: Mission, Satellite, Drift (age slider, ellipses, vectors, forward), AIS (time slider, type filters, gaps), Ranking table, Evidence panel + ledger. React/MapLibre were considered; rejected for MVP because they add a build step and (MapLibre) need vector tiles for any basemap — Leaflet with image overlays covers every requirement.

---

## 19. Database schema (SQLite, WAL)

`investigations 1─n satellite_scenes 1─n model_runs 1─n spill_detections 1─1 spill_geometries 1─n drift_runs 1─n origin_estimates`; `investigations 1─n ais_messages`, `vessels 1─n vessel_trajectories`, `vessel_correlations(investigation, detection, mmsi, rank, evidence JSON)`, `evidence(kind, ref_id, payload, sha256)`, `system_logs`. Indexes: `(mmsi,time_utc)`, `(lon,lat)`, bbox columns on geometries, `(investigation_id, rank)`. Upgrade path: PostGIS with `GEOGRAPHY` columns and GiST when multi-user or > 10⁷ AIS rows.

---

## 20. Offline demo architecture

`demo/make_demo_data.py` synthesises: a 2048² two-band σ⁰ GeoTIFF with gamma-distributed speckle (ENL 4.4), a 25-min discharge forward-drifted 4 h 10 min with the same forcing, a round low-wind look-alike, two ship targets; a CF NetCDF forcing cube (0.05°, hourly, ±14 h); 10 synthetic vessels (culprit tanker with 35-min AIS gap + speed drop, hard-negative tanker crossing origin 55 min late, parallel-lane tanker, two cargo ships at wrong times, container ship with course deviation far away, three loitering fishing vessels, ferry, junk rows) and `scenario.json` ground truth. Every artefact carries `synthetic=true`. One click (`▶ Run offline demo`) or `POST /api/demo/run` executes the full chain in ~4 s; the test-suite asserts the culprit ranks #1 and the 4 h hypothesis lies < 6 km from the true origin.

## 21. Technology stack (final)
Python 3.11 · FastAPI · PyTorch (CPU wheels) + segmentation_models_pytorch · NumPy/SciPy · rasterio · shapely · pyproj · xarray/netCDF4 · pandas · SQLite · Leaflet · Docker. Optional: OpenDrift, copernicusmarine, cdsapi, SNAP gpt, PostGIS.

## 22. Hardware
| Tier | Inference (2048² tile-set) | Full IW GRD (~25k×17k) | Training |
|---|---|---|---|
| Low-end laptop (4 GB, 2 cores) | baseline 3 s; U-Net ~60–90 s | 10–20 min windowed | not recommended |
| Student laptop (8–16 GB, 4–8 cores) | U-Net 20–40 s | 3–8 min | CPU overnight for ResNet-18 (~10 epochs) |
| Free GPU (Colab/Kaggle T4) or any 6 GB GPU | < 2 s | < 1 min | 40 epochs ≈ 2–4 h **[A]** |
Storage: code + demo 35 MB; dataset ~100 GB (download only Part I oil + subset of Part II if disk-limited).

## 23. Installation
```bash
git clone <repo> && cd oilspill-intel
python3 -m venv .venv && source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r backend/requirements.txt
python demo/make_demo_data.py            # regenerates synthetic scenario (optional, already bundled)
./run.sh                                  # → http://localhost:8000
# or
docker compose up --build
# tests
cd backend && pytest -q
# training (after downloading Zenodo Parts I–III)
python training/train_unet.py --root data/zenodo --epochs 40 --out models/unet_s1_oil.pt
```

## 24. Repository structure
```
oilspill-intel/
├─ backend/app/{config,db,pipeline,main}.py
│  ├─ sar/{preprocess,segment,lookalike}.py
│  ├─ geometry/slick.py · drift/engine.py · ais/{pipeline,correlate}.py
├─ backend/training/train_unet.py · backend/tests/test_pipeline.py · backend/requirements.txt
├─ frontend/static/{index.html,app.js,app.css,vendor/leaflet*}
├─ demo/make_demo_data.py · demo/data/{synthetic_s1_scene.tif,synthetic_forcing.nc,synthetic_ais.csv,scenario.json,ne_110m_coastline.geojson}
├─ docker/Dockerfile · docker-compose.yml · run.sh · docs/RESEARCH_REPORT.md
```

## 25. Roadmap
1. **Week 1** – download Zenodo Parts I–III, verify md5, build split manifest; train ResNet-18 U-Net on free GPU; threshold sweep; drop `.pt` into `backend/models/`.
2. **Week 2** – real-scene path: SNAP/xarray-sentinel σ⁰ GeoTIFF export script; CMEMS + Open-Meteo fetchers → forcing NetCDF; MarineCadastre CSV loader test on a real day (e.g. Gulf of Mexico).
3. **Week 3** – patch-level look-alike classifier (Approach C) trained on Part II look-alike vs oil; PANGAEA external test; background jobs for large scenes; area-uncertainty band.
4. **Week 4** – OpenDrift adapter validation vs built-in; calibration of correlation weights on the small set of public attributed discharges (EMSA reports) if obtainable; polish, docs, demo rehearsal.

## 26. Testing strategy
Unit (geometry geodesic area, look-alike rules, AIS cleaning, path traversal) · integration (full API chain) · ML (val IoU/Dice/P/R, threshold sweep, external test) · GIS (CRS round-trip, area at high latitude) · E2E (Playwright walk-through executed during development, zero console errors) · robustness matrix §28.

## 27. Evaluation methodology
Detection: scene-level P/R/F1, PR-AUC from max object likelihood. Segmentation: IoU, Dice, P, R, boundary F-score (2 px). Look-alike: FPR/FNR & confusion matrix on Part III look-alike vs oil sets and PANGAEA groups. Drift: origin distance error vs synthetic truth and vs real cases with known source (EMSA/press-documented incidents); drifter buoy validation is the gold standard (Baltic OpenDrift study) [LIT-10]. Attribution: Top-1/Top-3 recall, mean reciprocal rank, false-attribution rate on synthetic scenario families (`make_demo_data.py` is parameterised) — **we report these only after running them; no accuracy claims are made in this document.**

## 28. Failure modes & robustness
Low wind (< 2.5 m/s) → mass false positives (R1 downgrades, UI warns) · high wind → missed thin slicks · rain cells / fronts → R2/R3 partially cover; classifier needed · fragmented slicks → multiple objects; hindcast per object (largest first) · coastal wind shadow → R4 · noisy/EW 40 m data → resolution shift; retrain or downsample train data · incidence-angle gradient across 250 km swath → near-range darker: mitigate with large-window background normalisation (implemented) · AIS spoofing / missing → silence is scored as behaviour, never as position · dense traffic → several high correlations; UI shows ties honestly · forcing errors → ellipses under-estimate error; expose parameters, allow K_h/current σ overrides · time-zone bugs → everything UTC, enforced in parser.

## 29. Limitations (honest)
No trained weights shipped; classical fallback is far weaker than the CNN. Rule weights are uncalibrated. Hindcast uses surface-only 2-D physics; no Stokes-drift field, no Coriolis deflection, no beaching. No free historical AIS covers India — real Indian cases need institutional AIS. Single-scene age is unknowable. Synthetic demo is smooth-forcing and therefore optimistic.

## 30. Licence audit
| Component | Licence | Notes |
|---|---|---|
| Sentinel-1 data | Copernicus free/full/open | attribution "Contains modified Copernicus Sentinel data [year]" |
| Zenodo Trujillo-Acatitla dataset | CC BY 4.0 | cite |
| Krestenitis dataset | request-only | do not redistribute |
| PANGAEA Eastern-Med | **[NV]** check | test only |
| Refined SOS | CC BY 4.0 | |
| CMEMS | CMEMS licence (free, any purpose, attribution) | |
| ERA5 / Open-Meteo | C3S licence / CC BY 4.0 (Open-Meteo non-commercial free tier) | |
| HYCOM | US-Gov open | |
| MarineCadastre AIS | CC0 | |
| DMA AIS | open data (terms on dma.dk) | |
| PyTorch BSD-3 · smp MIT · timm Apache-2 · ImageNet ResNet weights (torchvision BSD) | permissive | |
| SegFormer NVIDIA weights | non-commercial | **avoided** |
| Ultralytics YOLO | AGPL-3 | **avoided** |
| SAM | Apache-2 | not used (performance) |
| OpenDrift | GPLv2 | optional; process isolation if redistributing |
| PyGNOME | public domain | optional |
| FastAPI MIT · rasterio BSD · shapely BSD · pyproj MIT · xarray Apache · Leaflet BSD-2 · Natural Earth public domain | permissive | |
| Our code | MIT (recommended) | |
Hidden-bill check: nothing in the default path needs a card; CDSE S3 beyond quota, Google Earth Engine commercial tier, Sentinel Hub, MarineTraffic/Spire AIS, cloud GPUs are all **optional/PAID** and marked so.

## 31. SIH demonstration (7 minutes)
0:00 `docker compose up` already running; open Mission page, show health (offline, CNN state). 0:30 click ▶ demo → Satellite page: σ⁰ quicklook, probability overlay, red slick polygon, geometry card (13.7 km², 10.1×1.9 km, axis 151°), the look-alike blob labelled `probable_lookalike` with rules R2+R3 — explain look-alikes. 2:00 Drift page: drag age slider 1→12 h, ellipses grow; explain "age unknowable → window"; toggle forward forecast. 3:30 AIS page: time slider, 10 tracks, magenta gap circles, cleaning report. 4:30 Ranking: culprit 82 % vs hard negative 78 % — explain what separates them. 5:30 Evidence: six cards, sub-score bars, limitations list, SHA-256 ledger. 6:30 Upload path: show GeoTIFF/CSV upload and that the same chain runs on real data; close with disclaimer language.

## 32. Final recommendation
| Decision | Choice | Evidence |
|---|---|---|
| MODEL | U-Net, ResNet-18 encoder, 2-ch, smp (MIT) | §5 benchmarks, licence, CPU feasibility |
| DATASET | Trujillo-Acatitla Zenodo I–III (CC BY 4.0); PANGAEA Eastern-Med external | §4 |
| SAR PIPELINE | GRD IW σ⁰ VV+VH → dB → clip/normalise → sea mask → 256² tiles | §3, §7 |
| LOOK-ALIKE | Rules R1–R5 (D) + classifier hook (C) | §9 |
| DRIFT ENGINE | Built-in stochastic Lagrangian (MIT); OpenDrift adapter | §13 |
| AIS SOURCE | MarineCadastre (CC0) for real validation; DMA secondary; labelled synthetic for demo | §14 |
| CORRELATION | Explainable weighted rule score over 6 evidence families; ML re-ranker later | §15 |
| BACKEND | FastAPI + SQLite | §18–19 |
| FRONTEND | Vanilla JS + Leaflet (bundled, offline) | §18 |
| DATABASE | SQLite (WAL) → PostGIS when needed | §19 |
| DEPLOYMENT | Docker Compose single service; `run.sh` fallback | §23 |
| HARDWARE | any 8 GB laptop for demo/inference; free Colab/Kaggle GPU for training | §22 |

---
### Sources
[ESA-1] ESA STEP forum, S1 GRD resolution vs pixel spacing · [ESA-2] SentiWiki S1 Mission (NESZ −22 dB) · [SH-1] Sentinel Hub / Planet S1 GRD processing docs · [CO-1] Copernicus Observer "Tracking oil spills" · [CDSE-1] dataspace.copernicus.eu quotas news 2023-12 · [CDSE-2] Esri ArcGIS blog on CDSE S3 quota · [XS-1] xarray-sentinel PyPI/GitHub · [LIT-1] Yang et al. 2024 IJRS near-real-time detection · [LIT-2] Sci. Rep. 2025 Suez Canal DL detection · [LIT-3] Remote Sens. 2026 OSDA-SAM · [LIT-4] Krestenitis et al. 2019 Remote Sens. · [LIT-5] SAM-OIL arXiv 2401.07502 · [LIT-6] FA-MobileUNet Sensors 2024 · [LIT-7] Ren et al. WACV 2024 "Segment anything, from space?" · [LIT-8] Remote Sens. 2024 multi-source slick evolution (Qatar) · [LIT-9] NAS 2005 Oil Spill Dispersants ch.4; Reed et al. 1994 · [LIT-10] Mar. Pollut. Bull. 2023 Baltic OpenDrift validation · [CM-1] CMEMS Service Commitments & Licence · [OM-1] open-meteo.com/en/licence · [OD-1] opendrift.github.io; GMD 2018 OpenDrift v1.0 · [MC-1] github.com/ocm-marinecadastre/ais-vessel-traffic · [MC-2] NOAA InPort 73064 · [DMA-1] web.ais.dk/aisdata · [AIS-1] aisstream.io documentation · Zenodo 8346860 / 8253899 / 13761290 / 15298010 record pages.
