"""Pure-Python Sentinel-1 GRD → σ⁰ (dB) GeoTIFF, without SNAP.

Steps (ESA GRD calibration procedure, see S1 Product Specification):
  1. read measurement DN (uint16 amplitude) for VV and VH
  2. thermal-noise subtraction using annotation/calibration/noise-*.xml
     (range + azimuth noise vectors, bilinear-interpolated to the full grid)
  3. σ⁰ = max(DN² − noise, 0) / A_σ² using annotation/calibration/calibration-*.xml LUT
  4. downsample by `factor` (block mean in linear power) to keep RAM bounded
  5. geocode with the annotation geolocation grid (GCPs) → gdal.Warp to EPSG:4326
  6. write 2-band float32 dB GeoTIFF + tags (sensing time, platform, pass)

Simplifications versus SNAP: no precise-orbit update (GRD GCPs already give
~10 m geolocation for ocean use) and no border-noise removal (we mask the
outer 200 px instead). Adequate for slick detection; documented in report §7.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.warp import reproject, Resampling, calculate_default_transform
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import zoom


def _vectors(xml_path: Path, list_tag: str, value_tag: str):
    root = ET.parse(xml_path).getroot()
    lines, raw = [], []
    for v in root.iter(list_tag):
        px = np.array(v.find("pixel").text.split(), int)
        val = np.array(v.find(value_tag).text.split(), float)
        if len(px) < 2 or len(px) != len(val):
            continue
        lines.append(int(v.find("line").text)); raw.append((px, val))
    if not raw:
        return np.array([]), None, np.array([])
    pixels = max((px for px, _ in raw), key=len)          # common range grid = densest vector
    vals = [np.interp(pixels, px, val) for px, val in raw]  # every vector resampled onto it (old IPF grids differ per line)
    return np.array(lines), pixels, np.array(vals)


def _lut_full(lines, pixels, vals, shape, factor):
    """Interpolate an (lines × pixels) LUT onto the downsampled grid."""
    h, w = shape[0] // factor, shape[1] // factor
    f = RegularGridInterpolator((lines, pixels), vals, bounds_error=False, fill_value=None)
    rr = (np.arange(h) * factor + factor / 2); cc = (np.arange(w) * factor + factor / 2)
    R, C = np.meshgrid(rr, cc, indexing="ij")
    return f(np.stack([R.ravel(), C.ravel()], 1)).reshape(h, w)


def _azimuth_noise(noise_xml: Path, shape, factor, win=None):
    """Azimuth noise (IW GRD ≥ IPF 2.9): per-swath blocks with azimuth vectors. Window-local (float32)."""
    root = ET.parse(noise_xml).getroot()
    r0, r1, c0, c1 = win if win else (0, shape[0], 0, shape[1])
    h, w = (r1 - r0) // factor, (c1 - c0) // factor
    out = np.ones((h, w), np.float32)
    rows_full = r0 + np.arange(h) * factor
    for blk in root.iter("noiseAzimuthVector"):
        l0, l1 = int(blk.find("firstAzimuthLine").text), int(blk.find("lastAzimuthLine").text)
        p0, p1 = int(blk.find("firstRangeSample").text), int(blk.find("lastRangeSample").text)
        ln = np.array(blk.find("line").text.split(), int); nz = np.array(blk.find("noiseAzimuthLut").text.split(), float)
        rsel = (rows_full >= l0) & (rows_full <= l1)
        cs, ce = max((p0 - c0) // factor, 0), min((p1 - c0) // factor + 1, w)
        if not rsel.any() or ce <= cs:
            continue
        out[rsel, cs:ce] = np.interp(rows_full[rsel], ln, nz)[:, None].astype(np.float32)
    return out


def _gcps(annotation_xml: Path, factor):
    root = ET.parse(annotation_xml).getroot()
    g = []
    for p in root.iter("geolocationGridPoint"):
        g.append(GroundControlPoint(row=int(p.find("line").text) / factor, col=int(p.find("pixel").text) / factor,
                                    x=float(p.find("longitude").text), y=float(p.find("latitude").text)))
    meta = {"sensing_time": root.find(".//startTime").text, "platform": root.find(".//missionId").text,
            "pass": root.find(".//pass").text, "mode": root.find(".//mode").text}
    return g, meta


def _window_from_gcps(gcps, bbox, shape, factor, margin=0.05):
    """Pixel window (r0,r1,c0,c1) in FULL-resolution coordinates covering bbox (lon/lat), from the GCP grid."""
    lon0, lat0, lon1, lat1 = bbox
    sel = [g for g in gcps if lon0 - margin <= g.x <= lon1 + margin and lat0 - margin <= g.y <= lat1 + margin]
    if len(sel) < 4:                                   # AOI may only clip the tilted footprint: widen once
        sel = [g for g in gcps if lon0 - 0.25 <= g.x <= lon1 + 0.25 and lat0 - 0.25 <= g.y <= lat1 + 0.25]
    if len(sel) < 4:
        raise ValueError("AOI does not intersect the scene footprint")
    rows = [g.row * factor for g in sel]; cols = [g.col * factor for g in sel]
    # GCP grid spacing is ~1 km; expand by one grid cell so the polygon edge is covered
    all_rows = sorted({g.row * factor for g in gcps}); all_cols = sorted({g.col * factor for g in gcps})
    dr = all_rows[1] - all_rows[0] if len(all_rows) > 1 else 0; dc = all_cols[1] - all_cols[0] if len(all_cols) > 1 else 0
    r0 = int(max(min(rows) - dr, 0)); r1 = int(min(max(rows) + dr, shape[0])); c0 = int(max(min(cols) - dc, 0)); c1 = int(min(max(cols) + dc, shape[1]))
    return r0, r1, c0, c1


def _lut_window(lines, pixels, vals, r0, r1, c0, c1, factor):
    """Bilinear LUT interpolation onto the (downsampled) window, done separably in float32:
    first along range (pixels) for each LUT line, then along azimuth (lines). Memory ~ h*w*4 bytes."""
    h, w = (r1 - r0) // factor, (c1 - c0) // factor
    rr = r0 + np.arange(h) * factor + factor / 2; cc = c0 + np.arange(w) * factor + factor / 2
    # range interpolation for each LUT line -> (n_lines, w)
    rows = np.stack([np.interp(cc, pixels, v) for v in vals]).astype(np.float32)
    # azimuth interpolation: weights between bracketing LUT lines
    idx = np.clip(np.searchsorted(lines, rr) - 1, 0, len(lines) - 2)
    l0, l1 = lines[idx], lines[idx + 1]
    t = np.clip((rr - l0) / np.maximum(l1 - l0, 1), 0, 1).astype(np.float32)
    out = rows[idx] * (1 - t)[:, None] + rows[idx + 1] * t[:, None]
    return out


def _geocode(arr, gcps_full, r0, c0, factor):
    """Geocode (bands, rows, cols) onto a regular EPSG:4326 grid using the annotation geolocation grid.

    Fits a 2nd-order polynomial lon,lat → (row,col) on the GCPs that surround the window (least squares, typically
    100–300 points, residual RMS reported in pixels) and resamples with bilinear map_coordinates. This replaces
    GDAL's GCP warper, which is fragile for sub-windows. Ocean scenes have no relief so no DEM is needed.
    """
    from scipy.ndimage import map_coordinates
    nb, h, w = arr.shape
    pts = [g for g in gcps_full if r0 - 3000 <= g.row <= r0 + h * factor + 3000 and c0 - 3000 <= g.col <= c0 + w * factor + 3000]
    lon = np.array([g.x for g in pts]); lat = np.array([g.y for g in pts])
    rr = (np.array([g.row for g in pts]) - r0) / factor; cc = (np.array([g.col for g in pts]) - c0) / factor
    lon0, lat0 = lon.mean(), lat.mean()
    def design(lo, la):
        x, y = lo - lon0, la - lat0
        return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], -1)
    A = design(lon, lat)
    cr, *_ = np.linalg.lstsq(A, rr, rcond=None); cx, *_ = np.linalg.lstsq(A, cc, rcond=None)
    rms = float(np.sqrt(np.mean((A @ cr - rr) ** 2 + (A @ cx - cc) ** 2)))
    # corner footprint of the window → output grid at ~pixel size
    corners = design(np.array([0, w, 0, w], float), np.array([0, 0, h, h], float))  # dummy, replaced below
    inside = (rr >= -1) & (rr <= h + 1) & (cc >= -1) & (cc <= w + 1)
    if inside.sum() >= 4:
        blon, blat = lon[inside], lat[inside]
    else:
        blon, blat = lon, lat
    # pixel size in degrees from the GCP spacing
    dlat = (blat.max() - blat.min()) / max(h, 1); dlon = (blon.max() - blon.min()) / max(w, 1)
    res = float(min(dlat, dlon)) if min(dlat, dlon) > 0 else 1e-4
    res = max(res, 5e-5)
    lons = np.arange(blon.min(), blon.max(), res, dtype=np.float64); lats = np.arange(blat.max(), blat.min(), -res, dtype=np.float64)
    H, W = len(lats), len(lons)
    dst = np.full((nb, H, W), np.nan, np.float32)
    validity = [np.isfinite(arr[b]).astype(np.float32) for b in range(nb)]
    filled = [np.nan_to_num(arr[b], nan=0.0).astype(np.float32) for b in range(nb)]
    for a in range(0, H, 512):                      # row-tiled to keep peak memory ~ tens of MB
        b_ = min(a + 512, H)
        LO, LA = np.meshgrid(lons, lats[a:b_])
        D = design(LO.ravel(), LA.ravel())
        R = (D @ cr).reshape(LO.shape).astype(np.float32); C = (D @ cx).reshape(LO.shape).astype(np.float32); del D, LO, LA
        for b in range(nb):
            vals = map_coordinates(filled[b], [R, C], order=1, mode="constant", cval=0.0)
            valid = map_coordinates(validity[b], [R, C], order=1, mode="constant", cval=0.0)
            dst[b, a:b_] = np.where(valid > 0.99, vals / np.maximum(valid, 1e-6), np.nan)
        del R, C
    from rasterio.transform import from_origin
    transform = from_origin(float(lons[0]) - res / 2, float(lats[0]) + res / 2, res, res)
    return dst, transform, rms


def calibrate_product(files: dict, out_tif: Path, factor: int = 4, bbox=None, progress=None, source_note: str = "") -> Path:
    """Calibrate a GRD product given per-polarisation file locations.

    files = {"vv": {"tif": <local path or /vsicurl/ URL>, "cal": Path, "noise": Path, "ann": Path}, "vh": {...}}
    Remote COGs (Planetary Computer, CDSE S3) are read with decimation `factor` so only the overview level and the
    AOI window travel over the network. `bbox` (lon0,lat0,lon1,lat1) optionally restricts processing to an AOI.
    Note: decimated reads average *amplitude* (DN) before squaring; block-mean of DN² is used for local files.
    The resulting bias is < 0.5 dB for homogeneous ocean (Assumption – Not verified against SNAP).
    """
    bands, meta, gcps, win = [], {}, None, None
    for pol in ("vv", "vh"):
        if pol not in files:
            continue
        f = files[pol]
        if gcps is None:
            gcps_full, meta = _gcps(Path(f["ann"]), 1)
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2"):
            with rasterio.open(f["tif"]) as ds:
                shape = (ds.height, ds.width)
                if win is None:
                    win = _window_from_gcps(gcps_full, bbox, shape, 1) if bbox else (0, shape[0], 0, shape[1])
                r0, r1, c0, c1 = win
                r1 = r0 + ((r1 - r0) // factor) * factor; c1 = c0 + ((c1 - c0) // factor) * factor
                h, w = (r1 - r0) // factor, (c1 - c0) // factor
                if progress: progress(pol, 0, h)
                remote = str(f["tif"]).startswith("/vsicurl/") or str(f["tif"]).startswith("http")
                if remote or factor == 1:
                    amp = ds.read(1, window=((r0, r1), (c0, c1)), out_shape=(h, w), resampling=Resampling.average).astype(np.float32)
                    power = amp * amp; del amp
                else:
                    power = np.zeros((h, w), np.float32)
                    for a in range(r0, r1, 1024 * factor):
                        b = min(a + 1024 * factor, r1)
                        dn = ds.read(1, window=((a, b), (c0, c1))).astype(np.float64)
                        power[(a - r0) // factor:(b - r0) // factor] = (dn ** 2).reshape((b - a) // factor, factor, w, factor).mean(axis=(1, 3))
                        if progress: progress(pol, (b - r0) // factor, h)
        ln, px, nv = _vectors(Path(f["noise"]), "noiseRangeVector", "noiseRangeLut")
        if len(ln) == 0:                                   # IPF < 2.90 (before 2018-03): old tag names, range-only noise
            ln, px, nv = _vectors(Path(f["noise"]), "noiseVector", "noiseLut")
        if len(ln) == 0:
            raise ValueError("no thermal-noise vectors found in " + str(f["noise"]))
        n_rg = _lut_window(ln, px, nv, r0, r1, c0, c1, factor)
        try:
            n_az = _azimuth_noise(Path(f["noise"]), shape, factor, (r0, r1, c0, c1))
        except Exception:
            n_az = 1.0
        power -= (n_rg * n_az).astype(np.float32); del n_rg, n_az
        np.maximum(power, 0.0, out=power)
        ln, px, sv = _vectors(Path(f["cal"]), "calibrationVector", "sigmaNought")
        a2 = _lut_window(ln, px, sv, r0, r1, c0, c1, factor).astype(np.float32); a2 *= a2
        sigma0 = power / a2; del power, a2
        if c0 == 0: sigma0[:, : max(200 // factor, 1)] = np.nan
        if c1 >= shape[1] - factor: sigma0[:, -max(200 // factor, 1):] = np.nan
        with np.errstate(divide="ignore", invalid="ignore"):
            sigma0[sigma0 <= 0] = np.nan
            np.log10(sigma0, out=sigma0); sigma0 *= 10
            bands.append(sigma0.astype(np.float32, copy=False))
        if progress: progress(pol, h, h)
    if not bands:
        raise FileNotFoundError("no measurement files")
    r0, r1, c0, c1 = win
    arr = np.stack(bands)
    dst, transform, geo_rms_px = _geocode(arr, gcps_full, r0, c0, factor)
    height, width = dst.shape[1:]
    meta["geo_rms_px"] = geo_rms_px
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_tif, "w", driver="GTiff", height=height, width=width, count=dst.shape[0], dtype="float32",
                       crs="EPSG:4326", transform=transform, nodata=np.nan, compress="deflate", tiled=True) as o:
        o.write(dst)
        o.update_tags(platform=meta.get("platform", "S1"), sensing_time=meta.get("sensing_time"), orbit_pass=meta.get("pass"),
                      polarisation=",".join(p.upper() for p in ("vv", "vh") if p in files)[: 5 if dst.shape[0] == 2 else 2], units="sigma0 dB",
                      processing="OSI pure-python calibration: thermal-noise removal, sigma0 LUT, decimation x%d, polynomial GCP geocoding (fit RMS %.2f px)%s" % (factor, meta.get("geo_rms_px", -1), source_note),
                      attribution="Contains modified Copernicus Sentinel data")
    return out_tif


def calibrate_safe(safe: Path, out_tif: Path, factor: int = 4, pols=("vv", "vh"), progress=None, bbox=None) -> Path:
    """Local .SAFE directory → σ⁰ GeoTIFF (wrapper around calibrate_product)."""
    files = {}
    for pol in pols:
        tif = next((safe / "measurement").glob(f"*-{pol}-*.tif*"), None)
        if tif is None:
            continue
        files[pol] = {"tif": str(tif), "cal": next((safe / "annotation" / "calibration").glob(f"calibration-*-{pol}-*.xml")),
                      "noise": next((safe / "annotation" / "calibration").glob(f"noise-*-{pol}-*.xml")),
                      "ann": next((safe / "annotation").glob(f"*-{pol}-*.xml"))}
    return calibrate_product(files, out_tif, factor=factor, bbox=bbox, progress=progress, source_note=" (local SAFE)")
