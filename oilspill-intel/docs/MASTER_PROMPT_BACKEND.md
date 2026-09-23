# Master prompt for a separate backend/model chat

Copy the text below into a new chat and attach this repository or give it access to the local project.

```text
Continue my SIH26143 Oil Spill Intelligence backend from the existing repository. The active project is:
C:\Users\HARSH TIWARI\Documents\Codex\2026-08-30\ex\review-oil-spills-20260906-190155\oilspill-intel

First read docs/HANDOFF_CONTEXT.md, docs/BACKEND_MODEL_HANDOFF.md, docs/PRD_BACKEND_MODEL_AND_DATA.md, docs/EVALUATION.md and git status/diff. Preserve all existing user changes. Keep the oil-spill project separate from my CCTV and MediKiosk projects.

Goal: build an evidence-support backend that accepts georeferenced Sentinel-1 SAR, segments suspected oil versus look-alikes, calculates slick geometry, hindcasts a possible release window using current/wind data, forecasts drift, correlates historical AIS tracks, ranks candidate vessels with explainable factors, and exports auditable evidence. Never call a score proof of responsibility or a calibrated probability unless calibration was actually performed.

Current truth:
- FastAPI/Python backend and plain HTML/CSS/JS frontend.
- Active detector is an adaptive dark-region baseline, not a trained model.
- A compatible two-channel U-Net training script exists at backend/training/train_unet.py.
- The custom particle drift model is active; OpenDrift/OpenOil is only a future option.
- SQLite/GeoJSON is active, not PostGIS.
- 19 real uploadable Sentinel-1 GeoTIFFs plus expected results are in demo/samples/globalosd_diverse20.
- A separate external 3-channel five-class MiT-B2 checkpoint is at backend/models/poseatsea_mitb2_5class.pth. It strict-loads successfully, but it is not interface-compatible with OSI and must not be renamed or silently activated.
- backend/training/infer_poseatsea.py can run that external checkpoint on JPG/PNG files and save colour masks; it is a comparison tool, not the active website detector.
- Latest reproduced baseline benchmark: n=10 oil, n=9 look-alike; detection 0.80 and false-alarm 0.444 at score 0.50; oil-likelihood AUC 0.622. These are small point-level metrics, not segmentation IoU.
- The full supervised dataset is about 94 GB before extraction and is not downloaded because local free space was about 11 GB. Do not pretend the model was trained.

Before implementing anything, state the single bounded next step and why it improves scientific validity. Prefer the smallest compatible change. For model work, require exact preprocessing, scene/geographic leakage control, untouched mask evaluation, look-alike false-alarm measurement, model/version hash and reproducible commands. Use only data whose license and provenance are recorded.

Useful commands:
cd backend
& '..\.venv\Scripts\python.exe' -m pytest -q -rs
& '..\.venv\Scripts\python.exe' training\benchmark_globalosd.py --n 10 --seed 17 --subcat Ships --factor 8 --half 0.08 --tag diverse20_baseline

Do not push, deploy, purchase data, delete existing work, or download the 94 GB dataset without my explicit approval and a verified destination with enough free space.
```
