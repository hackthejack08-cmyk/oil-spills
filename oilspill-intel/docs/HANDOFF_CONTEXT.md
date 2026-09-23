# Context for the next chat — Oil Spill Intelligence

## Start here

The user is developing an SIH oil-spill investigation prototype and a six-slide proposal. They asked the previous assistant to improve the UI of their GitHub repository, explain how the implementation works, compare it against the proposed goal/PPT, and produce this handoff. UI changes and a comparison report have been made locally. **Do not start over or assume the backend science is complete.**

First read this file and `docs/UI_AND_PROJECT_REVIEW.md`, then inspect `git status --short` and the current diff. Preserve the existing changes. The user has not requested a GitHub push, deployment, framework migration, training run, paid data subscription or new chat creation.

## User preferences

- Keep chat answers direct and easy to understand. Put lengthy detail in an attached/local document when useful.
- Explain unfamiliar technical terms in parentheses.
- For UI, they want a deliberate, practical design, not generic gradient cards or unnecessary animations.
- Keep the oil-spill project separate from their other two ideas: CCTV border surveillance (SIH26187) and MediKiosk healthcare. Some earlier PPT screenshots accidentally mixed CCTV content into the oil-spill deck.
- Use the installed Ponytail skill at full intensity for coding tasks. Prefer the existing stack, native CSS and small changes. Do not apply Ponytail to unrelated prose tasks.
- Do not spawn agents unless explicitly requested or required by an applicable instruction.

## Correct project and goal

**PS SIH26143, NTRO:** use satellite imagery to detect suspected oil slicks, characterise geometry, estimate possible source locations/times using environmental drift, predict future movement, reconstruct historical AIS vessel traffic, rank possible source vessels, and show explainable evidence through a geospatial interface.

SAR = synthetic aperture radar (satellite radar imaging). In the user's PPT, EO/optical refers to visible/near-infrared imagery. AIS = Automatic Identification System (vessel identity/position broadcasts). Hindcast = model backwards in time. Forecast = model future movement.

Proposed chain:

Satellite SAR + optional optical evidence → preprocessing → slick detection/segmentation → feature extraction → currents/wind hindcast and forecast → historical AIS matching → explainable candidate ranking → investigator dashboard/report.

Never frame a correlation score as proof that a vessel discharged oil. The current oil-likelihood and vessel scores are not calibrated probabilities. Exact spill age/thickness are not measured by this implementation.

## Repository / environment

- Remote: `https://github.com/Dakshshyahs3525/oil-spills`
- Local clone: `C:\Users\HARSH TIWARI\Documents\Codex\2026-08-30\ex\review-oil-spills-20260906-190155`
- **Active application:** `...\review-oil-spills-20260906-190155\oilspill-intel`
- A divergent older `osi_clean` folder also exists. It was not edited in this task.
- Starting commit: `c5c9d49ea2e789fd6ccff9d728598232572446a9`. No new commit or push was made.
- Windows PowerShell, Node available at `C:\Program Files\nodejs\node.exe`.
- Existing isolated review Python environment: `...\review-oil-spills-20260906-190155\.review-venv\Scripts\python.exe`. It uses Python 3.10 with required scientific packages and pytest. Do not reinstall heavy PyTorch dependencies unnecessarily.
- Local app address used for testing: `http://127.0.0.1:18765`. Check whether the server is still running before starting another. Background process IDs/session IDs are transient; do not blindly kill a stored PID.
- Standard repo launcher `app.py` creates its own `.venv` if absent and binds to `0.0.0.0`. Prefer the explicit localhost command below because the app has no authentication.

From `oilspill-intel/backend`:

```powershell
& '..\..\.review-venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 18765
& '..\..\.review-venv\Scripts\python.exe' -m pytest -q -rs
```

From `oilspill-intel`:

```powershell
node --check frontend/static/app.js
git diff --check
```

## Actual architecture, not aspirational labels

- Frontend: plain HTML/CSS/JavaScript, bundled Leaflet; no React, Next.js, Tailwind, Motion or Lucide installation.
- Backend: Python/FastAPI. `main.py` exposes APIs; `pipeline.py` connects stages.
- Detection: adaptive radar dark-region threshold baseline plus look-alike heuristics. U-Net inference/training code exists; `backend/models/unet_s1_oil.pt` is absent.
- Geometry: Rasterio/Shapely/pyproj/numpy-based polygons and geodesic measurements.
- Drift: custom stochastic Lagrangian particle integration. `pipeline.hindcast()` directly calls built-in `drift.simulate()`. The optional OpenDrift adapter is not the active route; OpenOil is not integrated.
- Matching: AIS CSV cleaning/tracks/gaps plus heuristic weighted scoring. Weights in `config.py`: spatial .40, temporal .15, trajectory .15, behaviour .15, vessel type .10, AIS quality .05; correlation code also applies conditions/gates, so do not describe this as an independently calibrated probability formula.
- Storage: SQLite with GeoJSON fields, not PostGIS. Evidence payloads have SHA-256 content hashes. Hashes do not create legal chain of custody, signatures or blockchain security.
- Live connector code: Planetary Computer/CDSE Sentinel-1, HYCOM/Open-Meteo environmental data, optional CMEMS, MarineCadastre/Danish AIS archives, live aisstream. No live downloads were run in this UI pass.

## Files changed in this task

1. `frontend/static/index.html`: new shell, compact stage navigation, central map, bottom candidate table, right controls/evidence, top metadata, inline status, empty state, NetCDF upload, JSON export and land-data warning.
2. `frontend/static/app.css`: replacement light workspace/dark rail style, responsive desktop/mobile breakpoints, focus indicators, restrained native transitions, reduced-motion support.
3. `frontend/static/app.js`: connects the new controls to real backend outputs; renders candidates, radar preview, selected tracks and metadata; escapes key dynamic text; uses /100 evidence-match scores; adds state cleanup, case-action locking, missing-data notices, saved-case recovery and export link.
4. `backend/app/pipeline.py`: persists complete `scene_result`, `drift_result` and `ais_result` response snapshots using existing evidence storage. Rebuilds old scene responses from existing detection evidence if necessary. Only returns mutually compatible current scene/drift/AIS snapshots. Restored AIS context is scoped to the latest scene. Scene re-analysis clears in-memory downstream context.
5. `backend/app/main.py`: health field `land_mask_available`; downloadable JSON attachment route `GET /api/evidence/{inv_id}/export`, with 404 for missing case.
6. `backend/tests/test_pipeline.py`: recovery-after-cache-clear checks; new scene invalidation; legacy scene recovery; export payload/header and missing-case checks.
7. `docs/UI_AND_PROJECT_REVIEW.md`: detailed assessment, changes, code walkthrough, PPT corrections and next priorities.
8. This handoff file.

Existing data was not purged. Tests create local synthetic investigations, so the saved-case list contains extra demo and legacy-regression cases. Do not delete them unless the user asks or a safe isolated-test cleanup is explicitly in scope.

## Important fixes and limits

- The old saved-case UI assigned the whole API wrapper to `S.inv` and created an incomplete scene, producing undefined IDs/image paths and Leaflet errors. It now consumes `inv.investigation`, `inv.scene`, `inv.drift` and `inv.ais`.
- New stage snapshots survive clearing `_CACHE` / process restart. Old cases without drift/AIS snapshots restore the scene and ask the user to rerun downstream stages. Do not invent missing saved results.
- Changing selected detection clears downstream UI results. Case-changing actions are locked while in flight, so a new request cannot overwrite another case's displayed data.
- AIS gaps now render dashed connections; these represent unobserved intervals, not recorded routes. Backend dead reckoning inside gaps remains an assumption and is disclosed in evidence.
- Removed an arbitrary drawn wind vector. The optional displayed net-drift vector is explicitly schematic.
- Export is a JSON evidence bundle, including prior-run ledger items and a disclaimer. **PDF reporting is still not implemented.** Initial blob-download testing in the in-app browser did not yield a download event, so export was moved to a normal server attachment endpoint. The final server-backed export produced an observed browser download event and passed attachment-header/payload regression checks.

## Verified / how to reproduce UI checks

The final regression suite, including the export endpoint checks, passed **16 tests, with 4 skipped**, in 24.20 seconds. Rerun after further code changes. Three skips are opt-in live tests; one is the absent Natural Earth land dataset. Nine numpy/rasterio compatibility/deprecation warnings remain; tests passed despite them. `node --check` and `git diff --check` passed after the final frontend edit.

Browser observations already confirmed: full synthetic pipeline, selected candidate evidence, saved complete case reopened without the old error, zero JS console errors in tested flows, desktop 1440×960 without document overflow, mobile 390px without horizontal document overflow and a 390px map height. Responsive viewport overrides were reset.

Known complete saved case from this local review: `inv_d0925f3da8` (synthetic Arabian Sea). Some later test cases deliberately replace the scene, so their missing drift/AIS after reopen is expected, not a regression.

Expected bundled outputs: two suspected objects; selected probable slick ≈14.20 km²; SYN-TANKER-ALPHA/419000101 first candidate with ≈75/100 evidence match; FOXTROT ≈71/100. ALPHA has a 39-minute AIS gap. These are synthetic test results, not real-world accuracy metrics.

For UI testing use the provided CUA browser API; browser tabs can expire at a new turn. Reacquire/create a fresh tab in the existing selected browser rather than assuming old tab IDs persist. Mark a final app tab as a deliverable if it should remain open. Do not use a generated mockup as a screenshot of working software.

## Missing pieces / next priorities

1. Matched real incident data: SAR, currents, wind and historical AIS for the same location/time plus independent truth.
2. Missing `oilspill-intel/demo/data/ne_10m_land.geojson`: land masking/beaching cannot be trusted in this installation. The coarser visible coastline line asset is not a substitute. A copy exists in the other app folder according to earlier inspection, but verify licensing/content/path before bringing it over; do not silently modify the science during a UI-only task.
3. Forcing coverage/units/finite-value validation. `RegularGridInterpolator(..., bounds_error=False, fill_value=None)` currently extrapolates beyond the available time/space grid.
4. Raster CRS/display bounds: scene bbox is constructed from raster transform coordinates without explicit conversion of display bounds to WGS84. Projected/rotated uploads need validation/handling.
5. Trained segmentation checkpoint; held-out mask evaluation and false-alarm analysis. Optical/Sentinel-2 validation is absent.
6. Drift validation with known release/drifter data and empirical uncertainty calibration. Backwards particle clouds are hypotheses, not proven sources.
7. Attribution evaluation with confirmed incidents and AIS. Indian-water archive access is not established by US/Danish connectors.
8. Persistent analyst notes, review/sign-off, formatted PDF, trustworthy source/processing provenance.
9. Authentication/access controls, robust uploads, bounded jobs, backups and multi-user validation before public deployment. Heavy snapshots in SQLite are an explicit laptop-MVP compromise.

`docs/EVALUATION.md` contains earlier claimed real-data metrics (small sample, about 45 chips), but the referenced raw `backend/runtime/eval` artifacts were absent. Those numbers were not reproduced in this task. Do not repeat them as verified performance. Do not assert no drifter data exists or no alternative platform exists based on those repo documents.

## PPT corrections

- U-Net/SegFormer → adaptive SAR baseline; trained model planned.
- EO validation → planned.
- OpenDrift/OpenOil → custom particle model; integration planned.
- PostGIS → SQLite/GeoJSON MVP; PostGIS as scaling option.
- Exact estimated age → possible release-time window.
- Culprit confidence → uncalibrated evidence-match score; human review.
- PDF/audit report → hashed evidence records + JSON export; formatted PDF planned.
- Remove CCTV content from the oil-spill impact/references slide.
- Six slides including title, retaining the user's provided SIH headings: title; idea/proposed solution; technical approach (merge duplicate diagrams); feasibility/viability; impact/benefits; research/references. Remove instruction slide from submitted PDF. Do not claim to have edited a PPTX: only screenshots were available for this comparison.

## Design references and earlier preview

The user supplied Motion, Lucide, Kokonut, Watermelon UI, Realtime Colors, Haikei, Impeccable, Emil Kowalski's skills, design.md, Godly, recent.design and several admin templates. Recommendation was one restrained admin structure + consistent icons + native CSS first; don't combine whole component systems or migrate to React just for styling.

Earlier generated preview (not a real application screenshot):
`C:\Users\HARSH TIWARI\.codex\generated_images\01a052f1-bbc3-7e40-9ba2-d9a143ceeee7\exec-7683765a-d668-4979-bef4-d6e53ac8cc03.png`

The actual UI deliberately uses real returned radar/map data rather than the generated satellite-like illustration. It has an offline coastline rather than a downloaded photographic basemap. A shared playback timeline and persisted notes pictured in the concept are not all implemented; existing AIS-time and age-hypothesis sliders remain separate.

Primary references already opened: `https://opendrift.github.io/`, `https://documentation.dataspace.copernicus.eu/`, `https://motion.dev/docs/quick-start`, `https://lucide.dev/icons/`, `https://impeccable.style/`.

## Backend/model/data update — 2026-09-07

Continue with `docs/BACKEND_MODEL_HANDOFF.md`, `docs/PRD_BACKEND_MODEL_AND_DATA.md` and `docs/MASTER_PROMPT_BACKEND.md`. A new 19-scene GlobalOSD/Planetary Computer GeoTIFF pack is in `demo/samples/globalosd_diverse20` and in `C:\Users\HARSH TIWARI\Downloads\OilSpill-Backend-Data`. The newly reproduced active-baseline result is 80.0% detection and 44.4% false alarms at score 0.50 on 10 oil and 9 usable look-alike point labels; AUC is 0.622. This is small point-level evidence, not segmentation IoU.

An external POSEatSea U-Net + MiT-B2 five-class checkpoint was downloaded to `backend/models/poseatsea_mitb2_5class.pth` and strict-load verified. It remains intentionally inactive because it expects 3-channel 512×512 imagery, while OSI expects tiled 2-channel VV/VH sigma-zero. The full supervised two-channel dataset is about 94 GB before extraction and was not downloaded or trained locally; only about 11 GB was free and PyTorch is CPU-only.

## Suggested prompt to paste with this file

> Continue my SIH26143 oil-spill project using this handoff and `UI_AND_PROJECT_REVIEW.md`. First inspect the existing local changes and confirm the current working prototype. Do not rebuild it, mix in the healthcare/CCTV projects, or push/deploy anything. Explain the highest-priority remaining gap in simple terms and help me choose the next bounded implementation step. Keep Ponytail full for coding work.
