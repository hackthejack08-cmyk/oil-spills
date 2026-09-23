# Start here — OilSpill backend data package

## Website test

Open `testing/geotiff`, choose one `.tif` file, and upload it on the prototype's **Satellite** page. The website accepts one GeoTIFF at a time. Use `expected_results.csv` to see whether the source label is oil or look-alike and what the current baseline returned.

## External checkpoint test

The five JPG inputs are in `testing/reference_jpg`. Their new colour outputs are in `testing/reference_predictions`.

From the project `backend` directory:

```powershell
& '..\.venv\Scripts\python.exe' training\infer_poseatsea.py '..\demo\samples\pretrained_reference'
```

The colours are black sea, cyan oil, red look-alike, brown ship and green land. This checkpoint is an external comparison model and is not automatically used by the website.

## Training

`metadata/globalosd` contains global point labels for benchmarking, not pixel masks. It is not enough to train the required segmentation model. The supervised two-channel GeoTIFF/mask dataset is about 94 GB before extraction and was not downloaded because there was insufficient local disk space. Follow `PRD_BACKEND_MODEL_AND_DATA.md` and `BACKEND_MODEL_HANDOFF.md` before starting training.
