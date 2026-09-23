# Evaluation on real Sentinel-1 data (honest, reproducible)

_Last run: 2026-09-05. Every number below was produced by code in this repository on real Sentinel-1 GRD data
streamed from Microsoft Planetary Computer (no account). Nothing is copied from papers._

_Additional independent rerun: 2026-09-07. See §7 for the newly packaged geographically diverse sample._

## 1. What was evaluated

| Component | Data | Method |
|---|---|---|
| Detector (segmentation + look-alike rules) | **GlobalOSD-SAR** (Zenodo 15286918, CC BY 4.0): 100 329 oil-slick centre points (ships/platforms/seeps) + 100 134 look-alike points, each with S1 acquisition date | `backend/training/benchmark_globalosd.py` – random sample, 0.3°×0.3° chip per point, calibrated σ⁰ VV at 40 m, full OSI pipeline, chip = "hit" if an object with `oil_likelihood ≥ thr` lies within 8 km of the labelled centre |
| Detector – known coastal spills | MV Wakashio (Mauritius, S1B 2020-08-10), Taylor Energy MC-20 (Gulf of Mexico, S1A 2018-10-20), Volgoneft/Kerch (S1A 2024-12-18), Mumbai harbour (2025, no known spill) | Visual inspection of overlays + label of the largest objects (`EVAL*` investigations in the DB) |
| Drift model | No drifter ground truth available at zero cost | Physics audit only – see §4 |
| Attribution | No labelled culprit cases with public AIS | Synthetic demo + GoM MarineCadastre plausibility run only – see §5 |

Detector in use: **baseline adaptive-threshold segmenter** (no trained U-Net weights ship with the repo; the
U-Net path is available but untrained – training data is the 96 GB Zenodo set that cannot be pulled at the
sandbox's 0.7 MB/s).

## 2. GlobalOSD-SAR benchmark (ship-slick positives vs look-alikes)

Command: `python training/benchmark_globalosd.py --n 25 --seed 1 --subcat Ships`
Result file: `backend/runtime/eval/benchmark_ships_n25_s1.json` (+ per-chip CSV).

| Metric | Value | n |
|---|---|---|
| Positives processed / negatives processed | 24 / 21 (5 chips skipped: no IW scene indexed for that date) | |
| Any dark object found near label (segmenter recall, before rules) | **87.5 %** (pos) vs 38.1 % (neg) | 24 / 21 |
| Detection rate @ oil_likelihood ≥ 0.35 (`possible_oil`) | **83.3 %** | 24 |
| False-alarm rate @ ≥ 0.35 | **28.6 %** | 21 |
| Detection rate @ ≥ 0.50 | 79.2 % | 24 |
| False-alarm rate @ ≥ 0.50 | 14.3 % | 21 |
| Detection rate @ ≥ 0.60 (`probable_oil`) | 58.3 % | 24 |
| False-alarm rate @ ≥ 0.60 | 14.3 % | 21 |
| AUC (chip score = max oil_likelihood near label) | **0.897** | 45 |
| AUC using segmentation confidence only (rules disabled) | 0.893 | 45 |

**How to read this.** With n≈45 the 95 % binomial interval on 83 % is roughly 63–95 %, on 29 % roughly
11–52 %. These are *small-sample* numbers on a *dataset that is not the one the PS is judged on*; they show the
pipeline is clearly better than chance on real ship-discharge slicks and that the rule layer adds only a little
over raw segmentation (AUC 0.897 vs 0.893). They are **not** a claim of operational accuracy. Increase `--n`
(≈20 s per chip) to tighten the interval; use `--seed` for a fresh sample.

Known systematic issues visible in the per-chip CSV:
* Two positives scored 0 – the segmenter found nothing within 8 km (one in 2018-10, one in 2016-11). Likely
  thin/old slicks below the adaptive threshold at 40 m sampling. `--factor 2` (20 m) may recover these at 4× RAM.
* False alarms at ≥ 0.5 were all high-contrast coastal or wind-shadow patches (rules `R4_coastal`, `R4b`).
  These are exactly the cases where the trained CNN classifier (not shipped) would help.
* GlobalOSD centre points are slick centroids from the authors' own detector; a handful may be mislocated
  or on a different orbital pass than the one we pick (first IW scene of that date covering the point).

## 3. Known coastal spills (qualitative, but real)

| Scene | Before phase-4 fixes | After fixes | Ground truth |
|---|---|---|---|
| Wakashio 2020-08-10, SE Mauritius | slicks found but labelled `probable_lookalike` L=0.34 (penalised `R2_blob_shape` + `R4_coastal`) | 4 × `probable_oil` L=0.60–0.66, 4.5–4.9 dB, along Pointe d'Esny reef | Grounding 2020-07-25, ~1 000 t oil released 6–10 Aug (public record) |
| Taylor Energy MC-20, 2018-10-20 | best L=0.41 | 2 × `probable_oil` (13 km², 6.0 dB; 7.6 km², 5.8 dB) at 28.9–29.0 N / 89.0–89.4 W | Chronic leak site 28.94 N 88.97 W (NOAA) – **plus** a large 205 km² river-plume/wind-shadow object off the Mississippi delta still scored 0.52 → a false alarm that only a learned classifier or a plume mask would remove |
| Volgoneft/Kerch 2024-12-18 | all coastal/weak | 3 × `possible_oil` 0.38–0.48, all ≤ 2.2 dB contrast | Mazut sank/was heavy; SAR signature weak at 40 m – honest "low confidence" output |
| Mumbai harbour 2025-03-20 (no spill) | 4 × probable_oil (6–12 dB "contrast" against land) | still 4 × `probable_oil` 0.60–0.61, contrast now 4.0–5.0 dB | Harbour wind-shadow / calm water → **known false positive**, flagged in the UI by `R4_coastal` |

## 4. What was found wrong and fixed in this pass

| # | Problem | Fix | File |
|---|---|---|---|
| 1 | Contrast was measured against a 251-px box mean that included **land** (10–20 dB brighter) and other dark objects → inflated coastal contrast, biased rules | Background = median of a sea-only ring around each object (land + all dark objects excluded) | `geometry/slick.py` |
| 2 | `R4_coastal`/`R4b` and `R2_blob_shape` fired regardless of contrast → genuine coastal spills (Wakashio, 6–7 dB) demoted | Penalties scaled by contrast (×0.6 at ≥4 dB, ×0.35 at ≥6 dB); blob rule off when contrast strong; new `R3_modest_contrast` band 1.5–2.5 dB | `sar/lookalike.py` |
| 3 | Calibrator crashed on **all products before IPF 2.90 (≈ March 2018)**: noise XML used `noiseVector/noiseLut`, and per-line pixel grids differ → `need at least one array to stack` / `inhomogeneous shape` | Fallback tag names; every LUT line resampled onto the common densest grid | `integrations/s1_calibrate.py` |
| 4 | STAC bbox intersection returned scenes whose tilted footprint did not actually contain the AOI → "AOI does not intersect" | Point-in-footprint test on the STAC geometry; GCP window widened once before failing; benchmark tries each pass of the day | `integrations/planetary.py`, `s1_calibrate.py` |
| 5 | Drift model ignored **Coriolis deflection** of the wind-driven component (literature 10–20°, right in NH / left in SH) | Per-particle deflection sampled from `WIND_DEFLECTION_DEG`, hemisphere-aware | `drift/engine.py`, `config.py` |
| 6 | Drift particles moved **through land** (Wakashio forward run drifted across Mauritius) | Beaching against Natural Earth 10 m polygons; `stranded_fraction` reported per hypothesis and shown in the UI | `drift/engine.py`, `app.js` |

Regression after the fixes: `pytest` 16 passed / 3 skipped; Playwright demo run 0 JS errors; demo culprit
SYN-TANKER-ALPHA still top (0.754); GoM MarineCadastre run HARVEY INTERVENTION 0.54.

## 5. What is still **not** verified (do not claim otherwise)

* **Segmentation IoU / pixel accuracy** – GlobalOSD has no masks; the Zenodo mask set (9.9 GB, single solid
  7z) could not be downloaded here. `training/train_unet.py` documents the Zenodo layout; a mask-IoU evaluation is the intended next step when bandwidth allows.
* **Hindcast positional error** – no drifter or known-release-point comparison was possible. The Wakashio
  backward run puts the 9-h origin 10 km ENE of the wreck; the wreck itself is on the reef, so this is at best
  a plausibility check, not validation.
* **Attribution precision/recall** – only the synthetic demo (culprit ranked #1) and one real-AIS plausibility
  run exist. No public dataset pairs a confirmed polluter with AIS.
* **Age estimation** – still unavailable by design (single-image SAR cannot measure it).
* **Trained CNN** – the U-Net/classifier weights are not shipped; every number above is the untrained baseline.

## 6. Reproduce

```bash
cd backend
# 1. GlobalOSD points (≈90 MB, CC BY 4.0) – fetch once
mkdir -p runtime/eval && cd runtime/eval && for f in positives negatives; do for e in shp shx dbf; do
  curl -L -o $f.$e https://zenodo.org/api/records/15286918/files/$f.$e/content; done; done; cd ../..
# 2. run (needs internet to Planetary Computer; ~20 s/chip; RAM < 600 MB)
python training/benchmark_globalosd.py --n 25 --seed 1 --subcat Ships
python training/benchmark_globalosd.py --n 25 --seed 2 --subcat Platforms
```

## 7. Packaged diverse rerun (2026-09-07)

Command:

```bash
python training/benchmark_globalosd.py --n 10 --seed 17 --subcat Ships --factor 8 --half 0.08 --tag diverse20_baseline
```

The run processed 10 ship-related oil points and 9 look-alike points; one requested look-alike location had no indexed intersecting scene for that date. At `oil_likelihood >= 0.50`, detection was **80.0%** and false alarms were **44.4%**. Oil-likelihood ROC AUC was **0.622**; segmentation-score AUC was **0.744**. This small rerun is worse than the earlier sample and is the stronger warning against presenting a single convenient sample as model accuracy.

The exact input GeoTIFFs and per-case outputs are preserved at `demo/samples/globalosd_diverse20`. These labels identify centre points, not pixel masks, so this section still does not measure segmentation IoU.

## 8. Integration and deployment audit (2026-09-19)

* Offline suite: **17 passed, 3 skipped**. The Natural Earth 10 m land polygons are installed, so the land-mask and beaching regression now runs instead of skipping.
* Opt-in live suite: **3 passed** — catalogue search, real Sentinel-1 pixel calibration, and HYCOM/Open-Meteo forcing construction.
* HYCOM access was moved from the unreliable full OPeNDAP dataset open to the NetCDF Subset Service, limiting each request to the investigation bounding box and time window.
* Public judge build: https://osi-sih26143.vercel.app. It is intentionally read-only and contains a generated snapshot of the synthetic case. The real-data ingestion backend is not exposed publicly until authentication, authorization, encryption, retention policy and agency AIS access are configured.
* Browser verification: the deployed case reaches the evidence view, renders the radar/map layers and reports no console warnings. Its 75/100 vessel value is an explainable evidence-match score, not model accuracy or legal proof.
