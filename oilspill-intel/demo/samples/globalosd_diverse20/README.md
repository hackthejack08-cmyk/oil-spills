# GlobalOSD diverse test pack

This folder contains **19 real, georeferenced Sentinel-1 GeoTIFF chips** fetched from Microsoft Planetary Computer around labels from GlobalOSD-SAR. The requested twentieth location had no intersecting scene for that date, so there is deliberately no invented replacement file.

## Use in the prototype

1. Start the application with `python app.py`.
2. Open **Satellite**.
3. In **Upload GeoTIFF**, select one `.tif` file from this folder.
4. Select **Run preprocessing + segmentation**.
5. Compare the result with `expected_results.csv`.

The web form accepts one scene at a time. Test the files individually; do not upload the whole folder.

## Labels and expected behaviour

- Ten filenames correspond to an **oil-slick centre** label. A useful detector should find a suspicious polygon close to the labelled centre.
- Nine filenames correspond to a **look-alike** label. A useful detector should reject or strongly down-rank the dark feature.
- `expected_results.csv` records the label, centre coordinate, source Sentinel-1 scene, detector score and the result produced by the current baseline.
- `benchmark_summary.json` records aggregate point-level detection and false-alarm measurements.

These are point labels, not pixel masks. They support screening/detection benchmarking but **cannot measure segmentation IoU or train a trustworthy pixel-segmentation model by themselves**.

Source: GlobalOSD-SAR, DOI `10.5281/zenodo.15286918`, CC BY 4.0. Pixel chips were streamed from Microsoft Planetary Computer and calibrated to sigma-zero by this repository.
