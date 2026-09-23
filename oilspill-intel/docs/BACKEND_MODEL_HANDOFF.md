# Backend/model handoff — 2026-09-07

## What was completed

- Downloaded GlobalOSD-SAR point metadata to `backend/runtime/eval` and the user package in Downloads.
- Streamed and calibrated 19 real Sentinel-1 GeoTIFF test chips covering geographically different oil and look-alike labels.
- Added the same test pack to `demo/samples/globalosd_diverse20` for direct upload to the website.
- Reproduced a new benchmark of the current detector and saved its per-case CSV and JSON summary.
- Downloaded five POSEatSea JPEG/reference-mask pairs.
- Downloaded `poseatsea_mitb2_5class.pth` and verified that it strictly loads into U-Net + MiT-B2 with no missing or unexpected tensors and returns a finite `1×5×512×512` output for a `1×3×512×512` input.
- Added `backend/training/infer_poseatsea.py`, a standalone backend command that produces five-class colour masks from JPG/PNG inputs without changing the website's geospatial detector.
- Re-ran all five reference images. Against the bundled checkpoint-produced reference masks, the reproduced micro oil IoU was 0.7425, precision 0.9552 and recall 0.7692. This checks interface reproducibility only; it is not independent model accuracy. Per-case results are in `demo/samples/pretrained_reference/reproduction_metrics.json`.
- Kept that external checkpoint separate from the active OSI model because its 3-channel, five-class, 512×512 preprocessing contract is incompatible with OSI's two-channel VV/VH tiled binary interface. Silently renaming it to `unet_s1_oil.pt` would be wrong.

## Newly measured current-detector result

Command:

```powershell
cd backend
& '..\.venv\Scripts\python.exe' training\benchmark_globalosd.py --n 10 --seed 17 --subcat Ships --factor 8 --half 0.08 --tag diverse20_baseline
```

Usable sample: 10 oil labels and 9 look-alikes; one look-alike scene was unavailable.

| Metric | Result |
|---|---:|
| Detection rate at score ≥ 0.50 | 80.0% |
| False-alarm rate at score ≥ 0.50 | 44.4% |
| Oil-likelihood ROC AUC | 0.622 |
| Raw segmentation-score ROC AUC | 0.744 |

This is a small point-level screening benchmark, not pixel-mask accuracy. It shows why a learned look-alike-aware model is the next priority.

## Local packages

Project test pack:

`demo/samples/globalosd_diverse20`

User download package:

`C:\Users\HARSH TIWARI\Downloads\OilSpill-Backend-Data`

Contents:

- `testing/geotiff`: 19 uploadable GeoTIFFs plus expected results.
- `testing/reference_jpg`: five reference JPEG/mask pairs.
- `metadata/globalosd`: GlobalOSD point shapefiles and supporting files.
- `models/poseatsea_mitb2_5class.pth`: external pretrained comparison checkpoint.

## What was not done and why

A new trustworthy segmentation model was not trained on this laptop. The required public two-channel image/mask archive is roughly 94 GB before extraction, while the machine had about 11 GB free and PyTorch reported CPU-only execution. Training on the 19 point-labelled chips would not produce pixel masks and would create misleading overfit accuracy.

Use `backend/training/train_unet.py` after placing the complete image/mask dataset on a drive with sufficient space and preferably a CUDA GPU. The script produces `backend/models/unet_s1_oil.pt`, which the current application automatically selects.

Run the downloaded external comparison checkpoint on the five lightweight examples:

```powershell
cd backend
& '..\.venv\Scripts\python.exe' training\infer_poseatsea.py ..\demo\samples\pretrained_reference
```

## Immediate next implementation steps

1. Obtain external storage (allow at least 150 GB working space) and a CUDA GPU or cloud notebook.
2. Download/extract Zenodo Parts I–III using their published train/validation/test roles.
3. Audit the file-to-mask pairing and split by event/geography.
4. Train the existing U-Net baseline; save metrics and checkpoint hash.
5. Run untouched pixel-mask evaluation plus the 19-chip GlobalOSD screening benchmark.
6. Integrate only the winning model whose preprocessing contract exactly matches inference.
7. Then validate drift on known release/drifter data and candidate ranking on confirmed cases.

## Similar repositories

- `https://github.com/m7mdehab/oil-spill-detection` — close end-to-end SAR segmentation/API/map reference with held-out model comparisons.
- `https://github.com/23f2003521/SIH2026` — same SIH problem, trained SAR/AIS/trajectory components; no hindcast drift model.
- `https://github.com/Misash/Oill-Spill-Detection`
- `https://github.com/Halyjo/slicksmith-ttom`
- `https://github.com/niranjansgitbuh/Oil_Spill_Detection`

Do not copy model claims without reproducing the authors' preprocessing, split and evaluation.
