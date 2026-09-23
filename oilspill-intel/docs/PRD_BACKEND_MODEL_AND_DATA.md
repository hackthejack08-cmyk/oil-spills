# PRD — Oil-spill detection, drift and vessel-attribution backend

Status: implementation plan for the SIH26143 research prototype
Product: OSI — Oil Spill Intelligence
Primary user: maritime investigator or response analyst

## 1. Problem

Satellite SAR can reveal dark formations that may be oil, but calm water, algae, rain cells and coastal wind shadows can look similar. By the time a spill is observed it may have drifted away from its release point. The system must therefore combine imagery, environmental forcing and historical AIS tracks to produce an explainable investigation hypothesis—not an automatic accusation.

## 2. Product outcome

Given a georeferenced Sentinel-1 scene and its observation time, the backend should:

1. preprocess calibrated SAR data;
2. segment suspected oil and distinguish common look-alikes;
3. produce slick polygons and geometry;
4. estimate a range of possible source locations/times and forecast movement;
5. correlate historical vessel tracks with that origin window;
6. return ranked evidence matches with a factor-by-factor explanation;
7. preserve inputs, processing versions and outputs in an exportable evidence bundle.

The output is a decision-support package for analyst review. A vessel score is not proof of discharge.

## 3. MVP scope

### Inputs

- Required: one georeferenced Sentinel-1 GRD-derived GeoTIFF, VV or VV+VH, sigma-zero in dB or linear units.
- Required metadata: acquisition UTC time and WGS84 footprint, read from the raster where possible.
- Optional: wind speed/vector, surface-current grids, analyst-selected origin window.
- Attribution: historical AIS CSV with MMSI, timestamp, latitude, longitude, SOG and COG.
- Later: Sentinel-2 optical evidence and authenticated live/global historical AIS.

### Outputs

- Per-pixel oil probability or five-class mask.
- Oil polygons with centroid, area, perimeter, orientation, elongation and contrast.
- Look-alike flags and explanations.
- Hindcast origin cloud/ellipses and forward forecast tracks with uncertainty.
- Ranked candidate vessels with spatial, temporal, trajectory, behaviour, vessel-type and AIS-quality factors.
- Versioned JSON evidence export with source and checksum provenance.

### Explicit non-goals for the hackathon MVP

- Declaring legal responsibility automatically.
- Estimating oil thickness, volume or exact spill age from one SAR image.
- Claiming global live AIS coverage without a licensed feed.
- Calling an uncalibrated score a probability.

## 4. Current implementation

| Stage | Current state | Production target |
|---|---|---|
| SAR ingestion | GeoTIFF upload and Planetary Computer/CDSE connectors | validated CRS, units, metadata and bounded uploads |
| Detection | adaptive local-darkness baseline | trained, held-out evaluated segmentation model |
| Look-alikes | rule-based shape/contrast/coastal penalties | learned multi-class model plus environmental checks |
| Geometry | Rasterio/Shapely/pyproj | retain, add uncertainty and QA flags |
| Drift | custom stochastic particle integration | validate against drifters; optionally use OpenDrift/OpenOil |
| AIS | CSV plus regional/live connectors | licensed Indian/global history or authorised receiver feed |
| Ranking | transparent weighted rules | calibrate on confirmed incidents; preserve explanations |
| Storage/report | SQLite + GeoJSON + hashed JSON export | analyst notes, signatures, role access and formatted PDF |

## 5. Model plan

### Baseline currently active

The application detects pixels darker than their local sea background and applies morphology plus look-alike rules. It is deterministic and useful for testing the full pipeline, but it is not trained.

### Target model

- Task: SAR semantic segmentation.
- Recommended first production candidate: U-Net with a small ResNet encoder, two input channels (VV/VH), binary oil output.
- Recommended comparison: five-class SegFormer or U-Net (sea, oil, look-alike, ship, land).
- Training crop: 256×256 for the two-channel repository model; use SAR-safe flips, rotations and mild radiometric jitter.
- Split: by event/scene and geographic cell, never random neighbouring tiles.
- Loss: Dice + binary cross-entropy for binary masks, or class-weighted cross-entropy + Dice for five classes.
- Selection metric: oil-class IoU and recall, accompanied by false alarms per scene. Pixel accuracy alone is misleading because most pixels are sea.

### Model acceptance gates

- Reproducible train/validation/test split and recorded seed.
- No scene or geographic leakage between splits.
- Oil IoU, Dice, precision, recall and PR-AUC reported on untouched masks.
- Separate look-alike false-alarm result.
- At least one external geographic test set.
- Model hash, preprocessing contract and threshold stored with each result.
- CPU inference completes within the agreed demonstration budget.

## 6. Data plan

| Package | Purpose | Local status |
|---|---|---|
| GlobalOSD-SAR point metadata | broad geographic screening benchmark | downloaded; 100,329 oil labels and 100,134 look-alikes |
| Diverse GeoTIFF pack | drag-and-drop prototype testing | 19 real chips downloaded; ten oil, nine look-alike |
| POSEatSea reference JPG/masks | checkpoint/interface smoke testing | five image/mask pairs downloaded |
| POSEatSea MiT-B2 checkpoint | external pretrained comparison | downloaded and strict-load verified; not active in OSI |
| Trujillo-Acatitla Parts I–III | supervised two-channel segmentation training | not downloaded; about 94 GB before extraction, larger than available disk |

Point labels are not substitutes for segmentation masks. The 19-chip pack is for testing and model comparison, not sufficient training material.

## 7. API contract

Minimum scene-analysis response:

```json
{
  "scene_id": "string",
  "detector": "model-or-baseline-name",
  "model_sha256": "string-or-null",
  "detections": [
    {
      "oil_likelihood": 0.0,
      "seg_confidence": 0.0,
      "classification": "possible_oil",
      "geometry": {"centroid_lon": 0.0, "centroid_lat": 0.0, "area_km2": 0.0},
      "lookalike_penalties": []
    }
  ],
  "warnings": []
}
```

Every later drift/AIS result must reference the scene and selected detection from which it was produced.

## 8. Quality and safety requirements

- Reject unreadable rasters, non-finite arrays and unsupported CRS/units with actionable errors.
- Do not extrapolate environmental grids silently beyond their time/space coverage.
- Preserve UTC throughout.
- Mark missing AIS intervals as gaps; never draw them as observed routes.
- Require analyst confirmation before language implying responsibility.
- Keep raw source attribution, processing parameters, model hash and exported evidence checksum.
- Add authentication, access control, upload limits and retention rules before public deployment.

## 9. Definition of done

The backend is ready for an SIH demonstration when a judge can upload a provided GeoTIFF, see a labelled detector/version, inspect polygons, run hindcast/forecast using explicit environmental inputs, upload or fetch matching AIS, review the ranked evidence factors, and export a reproducible JSON bundle. The demo must visibly distinguish synthetic data, real inputs, assumptions and unvalidated outputs.

It is ready for an operational pilot only after independent segmentation, drift and attribution validation plus authorised AIS access and security review.
