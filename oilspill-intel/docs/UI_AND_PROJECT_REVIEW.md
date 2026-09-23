# Oil Spill Intelligence: UI update and proposal comparison

Reviewed 6 September 2026. Scope: `oilspill-intel/` in the local clone of `Dakshshyahs3525/oil-spills`, starting from commit `c5c9d49ea2e789fd6ccff9d728598232572446a9`. The separate `osi_clean/` copy was not edited. No GitHub push or deployment was performed.

## Bottom line

This is a functioning research prototype of the proposed evidence chain, not just a static UI. It is **not yet a validated oil-spill detection and attribution system**. The main remaining work is reliable, matched real-world data and evaluation—not more animation.

The PPT comparison uses the screenshots and requirements you shared in this chat, not an editable PPTX file. References to future features must stay labelled as planned.

## What changed in this pass

- Replaced the all-dark, left-control-panel layout with compact navigation, a large central map, a white right-hand analysis panel and a persistent candidate table.
- Added case identity, sensing time, detector name, suspected area and review status at the top.
- Kept the actual Leaflet map, radar previews and backend results. The earlier generated mockup is **not** used as a fake working screen.
- Added clear empty states, inline pipeline status/error messages, keyboard-focus indicators, responsive layouts and reduced-motion support. Animation is limited to brief panel transitions.
- Candidate buttons select real returned records and highlight their tracks. Long recorded AIS gaps are dashed, not drawn as continuously observed routes.
- Removed the arbitrary schematic wind arrow; optional net drift direction remains explicitly schematic.
- Added NetCDF forcing upload to the interface, reusing the existing upload endpoint.
- Added a downloadable JSON evidence bundle. This is not a polished PDF, signed legal record or blockchain ledger.
- Fixed saved-investigation reopening: the old frontend confused the wrapper object with the investigation and constructed incomplete scene objects. Full stage responses are now saved and recovered from SQLite evidence records, including after server-memory loss.
- Older cases can recover their scene from stored detection evidence. If old drift/AIS response snapshots do not exist, rerun those stages; the UI explains this limitation.
- Clear stale scene/drift/ranking display state when changing case, scene or selected detection. Prevent overlapping case-changing actions in the UI.
- Escape case/vessel text in the changed displays; show correlation as a score out of 100 rather than suggesting a probability of guilt.
- Warn when the land polygon dataset is absent. This warning does not fix the missing dataset.

No React migration, new frontend dependency, paid service, satellite download or training run was introduced. Following the Ponytail skill, this reuses the existing implementation.

## How the project is built

| Stage | What the code actually does | Main files |
|---|---|---|
| Browser | HTML supplies controls; CSS handles appearance; JavaScript calls the API and updates Leaflet (the interactive map library). | `frontend/static/index.html`, `app.css`, `app.js` |
| API | FastAPI (Python web framework) receives uploads and stage requests. | `backend/app/main.py` |
| Radar preparation | Rasterio reads GeoTIFF (an image with geographic metadata); arrays are normalised and prepared for detection. | `sar/preprocess.py` |
| Detection | Adaptive dark-region thresholding is the shipped fallback. Look-alike rules adjust a heuristic oil-likelihood score. U-Net (a segmentation neural network) has a code path, but no trained checkpoint ships in this checkout. | `sar/segment.py`, `sar/lookalike.py` |
| Geometry | Converts detected pixels into polygons and calculates location, area, perimeter, dimensions and orientation. | `geometry/slick.py` |
| Drift | A custom Lagrangian model (moving simulated particles through currents and wind) generates possible past origins and future positions. | `drift/engine.py` |
| Vessel history | AIS (ship identity and position broadcasts) CSV records are cleaned, grouped into tracks, checked for gaps and filtered by place/time. | `ais/pipeline.py` |
| Matching | Rule-weighted proximity, timing, trajectory, behaviour, vessel type and AIS quality rank candidates. These weights are assumptions, not a trained attribution model. | `ais/correlate.py`, `config.py` |
| Persistence | SQLite (a local file database) stores cases and evidence; SHA-256 hashes identify payload content. | `db.py`, `pipeline.py` |

The actual flow is:

**Scene → suspected slick polygon → wind/current drift hypotheses → historical AIS comparison → ranked candidates → evidence review.**

The UI does not perform the scientific calculations. It sends requests to Python and displays the returned results. The synthetic demo exercises all these stages using fabricated inputs with a known scenario.

## Proposal/PPT versus the implementation

| Proposed element | Current state | What remains |
|---|---|---|
| Satellite SAR detection | Working baseline; demo verified | Trained checkpoint and independent segmentation evaluation; quantify false alarms. |
| Sentinel-2 / optical validation | Not implemented | Optical ingestion, cloud checks, time alignment and a defensible validation method. Passing optical bands through the SAR pipeline is not optical validation. |
| Spill feature extraction | Location/area/shape/orientation implemented | Validate geometry on independently labelled scenes; explicitly handle projected/rotated raster coordinates in the display pipeline. |
| Spill age | Hypothesised time window, not measured age | Do not promise an exact age. Add multi-observation constraints only if data supports them. |
| Thickness / volume | Not implemented | Remove from demonstrated outputs. These are not measured by the current radar baseline. |
| Hindcast and forecast | Custom particle model works on demo forcing | Validate origin error, forecast error and uncertainty coverage against known releases/drifters. |
| OpenDrift / OpenOil | Not the active pipeline | The pipeline calls the built-in simulation. An optional adapter is not proof that OpenOil is integrated. |
| Currents/wind inputs | NetCDF input and live connector code exist | Reject/warn on missing time/location coverage, units and non-finite values. Interpolation currently permits extrapolation beyond the forcing grid. |
| Land masking / beaching | Code exists, required `ne_10m_land.geojson` missing in this folder | Bundle a licensed, verified polygon file and run the currently skipped land test. The visible coastline line file is a different asset. |
| Historical AIS | Upload, cleaning, tracks, gap handling and matching implemented | Obtain an appropriate Indian-water source for the same incident/time. US/Danish archive connectors do not establish Indian coverage. |
| Vessel attribution | Explainable rule-based ranking | Independent confirmed cases, top-k evaluation, uncertainty calibration and investigator review. AIS silence is not itself proof of pollution. |
| Geospatial storage / PostGIS | SQLite with GeoJSON | PPT should say SQLite MVP; PostGIS is a scaling option, not currently installed storage. |
| Dashboard/report | Interactive UI; JSON export added in this pass | Formatted PDF, analyst notes with persistence, review/sign-off workflow and stronger provenance. |
| Operational deployment | Local prototype | Authentication, access control, upload hardening, concurrency/load testing, retention, backup and monitoring. Keep it local until those are addressed. |

## Most important missing pieces, in order

1. **One trustworthy end-to-end incident dataset:** satellite scene, wind/current fields, AIS and a credible reference event that overlap in place and time. Otherwise the interface can produce a convincing but unsupported ranking.
2. **Input safety and geographic/time validity:** forcing bounds/units checks, explicit sensing time, raster CRS handling, and the missing land dataset. Inspect `Forcing._interp` and the scene bbox construction before claiming broad real-data support.
3. **Evaluation:** held-out labelled masks for detection; known release/drifter observations for drift; confirmed cases for ranking. Report precision/recall and segmentation IoU (overlap of predicted and labelled masks), plus origin/forecast distance errors and top-k retrieval. Do not substitute demo success for these metrics.
4. **Trained detector and optical support:** train/test the model with event/geographic splits to reduce leakage, then add optical validation if feasible. A baseline-only MVP is acceptable if labelled honestly.
5. **Investigator workflow:** durable notes, uncertainty and provenance summaries, PDF report, rejected-candidate reasons and human sign-off.
6. **Deployment/scaling:** only after the scientific chain is useful, add access control, bounded jobs, durable storage and PostGIS if measured workload warrants it.

## Corrections to make in your PPT

- Change **“U-Net / SegFormer model”** to **“Adaptive SAR baseline; trained segmentation model planned”**, unless you actually provide and evaluate weights.
- Change **“EO validation”** to **“Optical validation — planned.”**
- Change **“OpenDrift / OpenOil”** to **“Built-in particle drift model; OpenDrift integration planned.”**
- Change **“PostGIS database”** to **“SQLite + GeoJSON MVP; PostGIS for scaling.”**
- Change **“estimated age”** to **“possible release-time window.”**
- Change **“confidence of culprit”** to **“uncalibrated evidence-match score; investigator review required.”**
- Change **“audit report / PDF export”** to **“hashed evidence records + JSON export; formatted PDF planned.”**
- Remove CCTV/person-detection/number-plate content from the oil-spill impact/reference slide shown earlier. That content belongs to your other project.
- Keep six slides using the provided headings: title; proposed solution/innovation; technical approach with one combined architecture diagram; feasibility/risks/mitigation; impact/benefits; research/references. Remove the duplicate architecture and instruction slide from the submitted PDF.

## How to demonstrate it

1. Open the local app and choose **Run synthetic demo**.
2. Explain that the scene, AIS and environmental inputs are fabricated test data.
3. Review the selected slick's shape and area in **Satellite**.
4. Move **Age hypothesis** in **Drift model**; explain why there are multiple possible origins.
5. Select two candidates below the map and compare their evidence. Point out dashed AIS gaps and the assumptions used inside them.
6. Export the JSON evidence bundle, then reopen the saved case to demonstrate persistence.
7. Finish by showing the missing-features table instead of claiming operational accuracy.

Short presentation wording:

> We built a modular research prototype that detects suspected slicks in radar imagery, models possible origins using currents and wind, and ranks historical vessel tracks by consistency with that evidence. The current build uses a baseline detector, a custom particle model and rule-based scoring. It supports explainable review, not automatic proof of responsibility.

## Verification and limitations of this review

- Backend test suite after the changes: 16 passed, 4 skipped. Three skipped tests require live-service opt-in; one requires the missing land polygon file. Dependency/deprecation warnings remain.
- Regression checks include full synthetic chain, persisted scene/drift/AIS recovery after clearing memory, old-case scene recovery, and invalidation after new scene analysis.
- JavaScript syntax checked with `node --check`; `git diff --check` passed.
- The server-backed evidence export passed attachment/payload tests and triggered an observed browser download event.
- Browser checks: synthetic demo, candidate evidence, saved case reopening, desktop/mobile layout sizing. The 1440×960 desktop layout had no document overflow; the 390px mobile layout kept a 390px map height and no horizontal document overflow. Backend scores match the rendered candidate rows.
- No live external dataset retrieval or model training was performed in this pass.
- Existing `docs/EVALUATION.md` reports earlier real-data experiments. Those results were not reproduced here; the referenced raw `backend/runtime/eval` artifacts were absent in this checkout. Treat them as repository-reported results, not independently verified accuracy.
- The original `app.py` launcher binds to all network interfaces. For this review the server was deliberately bound only to `127.0.0.1:18765`; do not publicly expose this unauthenticated research API.

## Research and implementation references

- [OpenDrift documentation](https://opendrift.github.io/) — particle drift framework, backward simulation and oil-specific models; useful for a future validated integration, not a description of the current active engine.
- [Copernicus Data Space documentation](https://documentation.dataspace.copernicus.eu/) — official catalogue/data-access reference for satellite ingestion.
- [Lucide](https://lucide.dev/icons/), [Motion JavaScript documentation](https://motion.dev/docs/quick-start), [Impeccable](https://impeccable.style/) — earlier visual/interaction references. This pass uses native CSS and existing controls, not installed copies of these libraries.

Run the tested local environment from this clone's `oilspill-intel/backend` directory:

```powershell
& '..\..\.review-venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 18765
```

That virtual environment is local review tooling, not a committed dependency. On another machine, install the repository's requirements into your own environment first.
