# Sample inputs for the real-connector walkthrough

`synthetic_scene_gulf_of_mexico_2023-06-15.tif` — **SYNTHETIC** SAR-like scene (same generator as `demo/data`),
georeferenced off the Mississippi delta (≈ 89.75°W 28.65°N) with sensing time 2023-06-15 12:05 UTC.
The slick is not real. It exists so you can run the *real* connectors end-to-end without a CDSE account:

1. Dashboard → "New real-data investigation"; Satellite page → upload this file → analyse.
2. Drift page → "Build real forcing" → HYCOM GLBy0.08 reanalysis currents + Open-Meteo ERA5 wind (no account).
3. AIS page → "Fetch AIS" (MarineCadastre, 2023 file ≈ 330 MB, cached) → reconstruct + correlate.

The AIS vessels returned are real 2023 traffic; the slick is synthetic, so **any "candidate" produced here is a
pipeline demonstration, not evidence against a real vessel.**
