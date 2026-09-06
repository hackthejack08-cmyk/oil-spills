"""Connector tests. Network-free by default (mocked); set OSI_LIVE_TESTS=1 to also hit the real public services."""
import io
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app import jobs
from app.integrations import ais_sources, cdse, metocean

LIVE = os.getenv("OSI_LIVE_TESTS") == "1"


# ---------------------------------------------------------------- jobs
def test_job_manager_runs_and_reports_progress():
    def work(n, progress):
        for i in range(n):
            progress(i + 1, n, f"step {i+1}")
        return {"ok": True}
    j = jobs.submit("unit", work, 3)
    import time
    for _ in range(50):
        if jobs.get(j["id"])["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    got = jobs.get(j["id"])
    assert got["status"] == "done" and got["result"] == {"ok": True} and got["progress"] == 1.0


def test_job_manager_captures_errors():
    def bad(progress):
        raise ValueError("boom")
    j = jobs.submit("unit", bad)
    import time
    for _ in range(50):
        if jobs.get(j["id"])["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert jobs.get(j["id"])["status"] == "error" and "boom" in jobs.get(j["id"])["error"]


# ---------------------------------------------------------------- MarineCadastre (mocked zip)
def test_marinecadastre_filters_bbox_and_time(tmp_path, monkeypatch):
    df = pd.DataFrame({"MMSI": [1, 1, 2, 3], "BaseDateTime": ["2023-06-15T03:00:00", "2023-06-15T20:00:00", "2023-06-15T04:00:00", "2023-06-15T05:00:00"],
                       "LAT": [28.5, 28.5, 28.5, 40.0], "LON": [-89.5, -89.5, -89.5, -70.0], "SOG": [5, 5, 0, 1], "COG": [90, 90, 0, 0], "Heading": [90, 90, 0, 0],
                       "VesselName": ["A", "A", None, "C"], "IMO": ["IMO1", "IMO1", None, None], "CallSign": ["x", "x", None, None], "VesselType": [80, 80, None, 30],
                       "Status": [0, 0, 0, 0], "Length": [200, 200, None, 20], "Width": [30, 30, None, 5], "Draft": [10, 10, None, 1], "Cargo": [80, 80, None, None]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AIS_2023_06_15.csv", df.to_csv(index=False))
    zpath = tmp_path / "AIS_2023_06_15.zip"; zpath.write_bytes(buf.getvalue())
    monkeypatch.setattr(ais_sources, "_fetch_zip", lambda url, cache, progress=None: zpath)
    r = ais_sources.marinecadastre_subset([-90, 28, -89, 29], datetime(2023, 6, 15, 2, tzinfo=timezone.utc), datetime(2023, 6, 15, 12, tzinfo=timezone.utc),
                                          tmp_path, tmp_path / "out.csv")
    out = pd.read_csv(tmp_path / "out.csv")
    assert r["n_in_bbox"] == 2 and r["n_vessels"] == 2       # vessel 3 outside bbox, second msg of vessel 1 outside time window
    assert set(out.MMSI) == {1, 2}


def test_marinecadastre_missing_day_gives_clear_error(tmp_path, monkeypatch):
    import requests
    class R: status_code = 404
    def boom(url, cache, progress=None):
        raise requests.HTTPError(response=R())
    monkeypatch.setattr(ais_sources, "_fetch_zip", boom)
    with pytest.raises(FileNotFoundError, match="AccessAIS"):
        ais_sources.marinecadastre_subset([-90, 28, -89, 29], datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, 6, tzinfo=timezone.utc), tmp_path, tmp_path / "o.csv")


# ---------------------------------------------------------------- forcing builder (mocked HYCOM + Open-Meteo)
def _fake_currents(bbox, t0, t1):
    lon = np.linspace(bbox[0], bbox[2], 6); lat = np.linspace(bbox[1], bbox[3], 5)
    time = pd.date_range(t0.replace(tzinfo=None), t1.replace(tzinfo=None), freq="3h")
    uo = np.full((len(time), len(lat), len(lon)), 0.2, dtype="float32"); uo[:, 0, 0] = np.nan   # one "land" cell
    return xr.Dataset({"uo": (("time", "lat", "lon"), uo), "vo": (("time", "lat", "lon"), np.full_like(uo, -0.1))}, coords={"time": time, "lat": lat, "lon": lon})


def _fake_wind(bbox, t0, t1, n=4):
    lon = np.linspace(bbox[0], bbox[2], n); lat = np.linspace(bbox[1], bbox[3], n)
    time = pd.date_range(t0.replace(tzinfo=None), t1.replace(tzinfo=None), freq="1h")
    u = np.full((len(time), len(lat), len(lon)), 5.0, dtype="float32")
    return xr.Dataset({"u10": (("time", "lat", "lon"), u), "v10": (("time", "lat", "lon"), u * 0.5)}, coords={"time": time, "lat": lat, "lon": lon})


def test_build_forcing_produces_drift_compatible_netcdf(tmp_path, monkeypatch):
    monkeypatch.setattr(metocean, "_hycom_subset", _fake_currents)
    monkeypatch.setattr(metocean, "_openmeteo_wind", _fake_wind)
    out = tmp_path / "f.nc"
    r = metocean.build_forcing([70, 19, 71, 20], datetime(2025, 3, 14, 1, 12, tzinfo=timezone.utc), 12, 6, out, currents="hycom")
    assert out.exists() and r["grid"][0] == 5
    from app.drift.engine import Forcing
    f = Forcing.from_netcdf(str(out))
    w = f.wind_at(datetime(2025, 3, 14, 1, 12, tzinfo=timezone.utc), 70.5, 19.5)
    assert 5.5 < w < 5.7                                     # |(5, 2.5)| = 5.59 m/s
    ds = xr.open_dataset(out)
    assert not np.isnan(ds.uo.values).any()                  # land cell filled, no NaN reaches the drift model


def test_hycom_dataset_selection_by_date():
    assert "ESPC" in metocean._hycom_url(datetime(2025, 3, 14, tzinfo=timezone.utc))[0]
    assert "GLBy0.08 expt_93.0" in metocean._hycom_url(datetime(2023, 6, 15, tzinfo=timezone.utc))[0]
    with pytest.raises(ValueError):
        metocean._hycom_url(datetime(2015, 1, 1, tzinfo=timezone.utc))


# ---------------------------------------------------------------- CDSE
def test_cdse_download_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("OSI_CDSE_USER", raising=False)
    with pytest.raises(PermissionError, match="OSI_CDSE_USER"):
        cdse._token()


# ---------------------------------------------------------------- live (opt-in)
@pytest.mark.skipif(not LIVE, reason="set OSI_LIVE_TESTS=1")
def test_live_cdse_search():
    prods = cdse.search(72.6, 18.9, "2025-03-01T00:00:00Z", "2025-03-31T23:59:59Z", top=5)
    assert prods and all(p["name"].startswith("S1") for p in prods)


@pytest.mark.skipif(not LIVE, reason="set OSI_LIVE_TESTS=1")
def test_live_forcing(tmp_path):
    r = metocean.build_forcing([69.5, 18.5, 71.5, 20.5], datetime(2025, 3, 14, 1, 12, tzinfo=timezone.utc), 6, 3, tmp_path / "f.nc")
    assert r["mean_wind_ms"] > 0 and (tmp_path / "f.nc").exists()


# ---------------------------------------------------------------- calibration internals (no network)
def test_lut_window_matches_bilinear_reference():
    from app.integrations.s1_calibrate import _lut_window
    lines = np.array([0, 100, 200]); pixels = np.array([0, 50, 100])
    vals = np.array([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [3.0, 4.0, 5.0]])
    out = _lut_window(lines, pixels, vals, 0, 200, 0, 100, 4)
    assert out.shape == (50, 25) and out.dtype == np.float32
    assert abs(out[0, 0] - (1.0 + 2 / 100 + 2 / 50)) < 1e-4       # (row 2, col 2) bilinear from the corner
    assert np.all(np.diff(out, axis=0) >= 0) and np.all(np.diff(out, axis=1) >= 0)


def test_geocode_roundtrip_regular_grid():
    """A synthetic GCP grid on a pure lon/lat affine grid must be reproduced with ~0 error."""
    from rasterio.control import GroundControlPoint
    from app.integrations.s1_calibrate import _geocode
    h, w = 200, 300
    gcps = [GroundControlPoint(row=r, col=c, x=70 + c * 1e-3, y=20 - r * 1e-3) for r in range(0, h + 1, 25) for c in range(0, w + 1, 25)]
    arr = np.tile(np.linspace(-25, -5, w, dtype=np.float32), (h, 1))[None]
    dst, transform, rms = _geocode(arr, gcps, 0, 0, 1)
    assert rms < 1e-3
    v = dst[0][np.isfinite(dst[0])]
    assert v.min() > -25.5 and v.max() < -4.5 and np.isfinite(dst[0]).mean() > 0.9


def test_land_mask_marks_mumbai_and_not_open_sea():
    from rasterio.transform import from_origin
    from rasterio.crs import CRS
    from app.sar import preprocess
    if not (preprocess.config.DEMO_DIR / "ne_10m_land.geojson").exists():
        pytest.skip("Natural Earth land file absent")
    tr = from_origin(72.0, 19.5, 0.01, 0.01)                      # 72–73.5E, 18–19.5N
    m = preprocess.land_mask((150, 150), tr, CRS.from_epsg(4326), buffer_km=0.0)
    assert m[int((19.5 - 19.2) / 0.01), int((73.1 - 72.0) / 0.01)]        # 73.1E 19.2N (Thane/Kalyan, inland) → land
    assert not m[int((19.5 - 19.0) / 0.01), int((72.3 - 72.0) / 0.01)]    # 72.3E 19.0N → open sea


@pytest.mark.skipif(not LIVE, reason="set OSI_LIVE_TESTS=1")
def test_live_planetary_search_and_calibrate(tmp_path):
    from app.integrations import planetary, s1_calibrate
    prods = planetary.search(72.6, 18.9, "2025-03-01T00:00:00Z", "2025-03-31T23:59:59Z", top=1)
    assert prods and prods[0]["id"].startswith("S1")
    files = planetary.stage_annotations(prods[0]["assets"], tmp_path / "pc", planetary.sas_token())
    out = s1_calibrate.calibrate_product(files, tmp_path / "s.tif", factor=8, bbox=[72.4, 18.8, 72.6, 19.0])
    import rasterio
    with rasterio.open(out) as d:
        a = d.read(1); v = a[np.isfinite(a)]
    assert -30 < np.median(v) < -5                                 # plausible ocean σ⁰ in dB
