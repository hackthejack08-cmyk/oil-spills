"""Generate the fully SYNTHETIC offline demo scenario.

Everything produced here is labelled synthetic in metadata and file names.
Scenario: Arabian Sea shipping lane off Gujarat/Mumbai (realistic geography,
fabricated events). A tanker ("SYN-TANKER-ALPHA") discharges while underway,
switches AIS off for 35 min, and the slick drifts ~4 h before a synthetic
Sentinel-1-like scene "observes" it.

Outputs (demo/data/):
  synthetic_s1_scene.tif           2-band (VV,VH) sigma0 dB GeoTIFF, EPSG:4326
  synthetic_forcing.nc             currents (uo,vo) + wind (u10,v10), hourly, 0.05°
  synthetic_ais.csv                MarineCadastre-style AIS
  scenario.json                    ground truth for evaluation (origin, time, culprit)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
import xarray as xr

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(7)

T_OBS = datetime(2025, 3, 14, 1, 12, 0, tzinfo=timezone.utc)   # S1 descending pass ~01 UTC local night
T_SPILL = T_OBS - timedelta(hours=4, minutes=10)
ORIGIN = (70.55, 19.35)            # lon, lat  (true release start, synthetic)

# --- 1. forcing: steady NE-going current 0.25 m/s + SW wind 6 m/s -------------
lon = np.arange(69.0, 72.51, 0.05); lat = np.arange(18.0, 21.01, 0.05)
times = pd.date_range(T_OBS - timedelta(hours=14), T_OBS + timedelta(hours=14), freq="1h", tz="UTC")
LON, LAT = np.meshgrid(lon, lat)
uo = 0.18 + 0.06 * np.sin(np.radians(LAT * 40)); vo = 0.17 + 0.05 * np.cos(np.radians(LON * 30))
u10 = 4.5 + 0.8 * np.sin(np.radians(LON * 20)); v10 = 3.8 + 0.5 * np.cos(np.radians(LAT * 25))
def stack(f, amp): return np.stack([f * (1 + amp * np.sin(k / 6)) for k in range(len(times))]).astype("float32")
ds = xr.Dataset({"uo": (("time", "lat", "lon"), stack(uo, 0.15)), "vo": (("time", "lat", "lon"), stack(vo, 0.15)),
                 "u10": (("time", "lat", "lon"), stack(u10, 0.2)), "v10": (("time", "lat", "lon"), stack(v10, 0.2))},
                coords={"time": times.tz_convert(None).values, "lat": lat, "lon": lon},
                attrs={"title": "SYNTHETIC forcing for offline demo", "synthetic": "true",
                       "note": "Not real ocean data. Real mode uses Copernicus Marine (currents) + ERA5/Open-Meteo (wind)."})
ds.to_netcdf(OUT / "synthetic_forcing.nc")

# --- 2. forward-simulate the slick to the observation time ------------------
R = 6371008.8
def drift(lon0, lat0, t0, t1, n=1, k_h=5.0, fw=0.03):
    p_lon = np.full(n, lon0, float); p_lat = np.full(n, lat0, float); t = t0
    while t < t1:
        dt = min(600, (t1 - t).total_seconds())
        i = np.argmin(np.abs((times - pd.Timestamp(t)).total_seconds()))
        j = np.abs(lat - p_lat.mean()).argmin(); k = np.abs(lon - p_lon.mean()).argmin()
        u = ds.uo.values[i, j, k] + fw * ds.u10.values[i, j, k]; v = ds.vo.values[i, j, k] + fw * ds.v10.values[i, j, k]
        p_lon += np.degrees((u * dt + np.sqrt(2 * k_h * dt) * rng.normal(size=n)) / (R * np.cos(np.radians(p_lat))))
        p_lat += np.degrees((v * dt + np.sqrt(2 * k_h * dt) * rng.normal(size=n)) / R)
        t += timedelta(seconds=dt)
    return p_lon, p_lat

# discharge over 25 min while tanker steams at 12 kn on course 335°
crs_deg = 335.0; kn = 12.0
release_pts = []
for m in range(0, 26, 1):
    t_rel = T_SPILL + timedelta(minutes=m)
    d_km = kn * 1.852 * m / 60
    rl_lat = ORIGIN[1] + (d_km * np.cos(np.radians(crs_deg))) / 111.32
    rl_lon = ORIGIN[0] + (d_km * np.sin(np.radians(crs_deg))) / (111.32 * np.cos(np.radians(rl_lat)))
    pl, pa = drift(rl_lon, rl_lat, t_rel, T_OBS, n=60)
    release_pts.append(np.column_stack([pl, pa]))
slick = np.vstack(release_pts)

# --- 3. synthetic SAR scene (VV,VH dB) 2048x2048 @ ~10 m --------------------
H = W = 2048; px = 0.0001              # ≈11 m at this latitude
c_lon, c_lat = slick[:, 0].mean(), slick[:, 1].mean()
west, north = c_lon - W / 2 * px, c_lat + H / 2 * px
transform = from_origin(west, north, px, px)
yy, xx = np.mgrid[0:H, 0:W]
plon = west + (xx + 0.5) * px; plat = north - (yy + 0.5) * px
# sea clutter: gamma-distributed speckle (ENL≈4.4 for IW GRD) around −17 dB VV, −26 dB VH; slow wind texture
tex = 1.0 + 0.08 * np.sin(yy / 90.0) * np.cos(xx / 140.0)
vv_lin = 10 ** (-17 / 10) * tex * rng.gamma(4.4, 1 / 4.4, (H, W))
vh_lin = 10 ** (-26 / 10) * tex * rng.gamma(4.4, 1 / 4.4, (H, W))
# oil: damp by 6–9 dB with soft edges; rasterise particle cloud with gaussian splat
oil = np.zeros((H, W), np.float32)
cols = ((slick[:, 0] - west) / px).astype(int); rows = ((north - slick[:, 1]) / px).astype(int)
ok = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
oil[rows[ok], cols[ok]] = 1
from scipy import ndimage as ndi
oil = ndi.gaussian_filter(oil, 14); oil = np.clip(oil / np.percentile(oil, 99.7), 0, 1)
oil = ndi.gaussian_filter(np.where(oil > 0.15, 1.0, 0.0), 6)
damp = 10 ** (-(6 + 3 * oil) * oil / 10)
vv_lin *= damp; vh_lin *= np.where(oil > 0.2, 10 ** (-3 * oil / 10), 1)   # VH sits near noise floor -> weaker contrast
# a low-wind look-alike patch (round, weak contrast) to exercise the rejection rules
la_r = np.hypot(yy - 380, xx - 1600) < 240
vv_lin[la_r] *= 10 ** (-4.5 / 10)
# bright ship targets (culprit tanker is ~ 4 h away by now; put two random ships)
for (r, c) in [(1500, 300), (700, 1800)]:
    vv_lin[r - 3:r + 3, c - 8:c + 8] *= 300
vv_db = 10 * np.log10(vv_lin).astype("float32"); vh_db = 10 * np.log10(vh_lin).astype("float32")
gt_mask = (oil > 0.35).astype("uint8")
with rasterio.open(OUT / "synthetic_s1_scene.tif", "w", driver="GTiff", height=H, width=W, count=2, dtype="float32",
                   crs="EPSG:4326", transform=transform, compress="deflate", tiled=True) as dst:
    dst.write(vv_db, 1); dst.write(vh_db, 2)
    dst.update_tags(synthetic="true", sensing_time=T_OBS.isoformat(), platform="SYNTHETIC-S1-LIKE",
                    polarisation="VV,VH", units="sigma0 dB", note="Synthetic scene for offline demo – NOT real Sentinel-1 data")
with rasterio.open(OUT / "synthetic_gt_mask.tif", "w", driver="GTiff", height=H, width=W, count=1, dtype="uint8",
                   crs="EPSG:4326", transform=transform, compress="deflate") as dst:
    dst.write(gt_mask, 1)

# --- 4. synthetic AIS ---------------------------------------------------------
rows_ais = []
def add_track(mmsi, name, vtype, imo, start_lon, start_lat, course, sog, t0, hours, step_min=3,
              gap=None, sog_drop=None, turn=None, noise=0.0004):
    t = t0; lonp, latp = start_lon, start_lat; crs_ = course
    while t < t0 + timedelta(hours=hours):
        if turn and turn[0] <= t < turn[1]:
            crs_ = course + turn[2]
        elif turn and t >= turn[1]:
            crs_ = course
        spd = sog
        if sog_drop and sog_drop[0] <= t < sog_drop[1]:
            spd = sog_drop[2]
        d_km = spd * 1.852 * step_min / 60
        latp += d_km * np.cos(np.radians(crs_)) / 111.32
        lonp += d_km * np.sin(np.radians(crs_)) / (111.32 * np.cos(np.radians(latp)))
        t += timedelta(minutes=step_min)
        if gap and gap[0] <= t < gap[1]:
            continue
        rows_ais.append(dict(MMSI=mmsi, BaseDateTime=t.strftime("%Y-%m-%dT%H:%M:%S"),
                             LAT=round(latp + rng.normal(0, noise), 5), LON=round(lonp + rng.normal(0, noise), 5),
                             SOG=round(spd + rng.normal(0, 0.2), 1), COG=round(crs_ % 360 + rng.normal(0, 1.5), 1),
                             Heading=int(crs_ % 360), VesselName=name, IMO=imo, VesselType=vtype, Status=0,
                             Length=rng.integers(80, 330), Width=rng.integers(14, 60), synthetic=True))

t_win0 = T_OBS - timedelta(hours=8)
# culprit: passes ORIGIN exactly at T_SPILL, course 335, 12 kn; AIS gap 5 min before -> 30 min after discharge start
back_km = 12 * 1.852 * (T_SPILL - t_win0).total_seconds() / 3600
s_lat = ORIGIN[1] - back_km * np.cos(np.radians(335)) / 111.32
s_lon = ORIGIN[0] - back_km * np.sin(np.radians(335)) / (111.32 * np.cos(np.radians(s_lat)))
add_track(419000101, "SYN-TANKER-ALPHA", 80, "IMO9000001", s_lon, s_lat, 335, 12, t_win0, 10,
          gap=(T_SPILL - timedelta(minutes=5), T_SPILL + timedelta(minutes=30)),
          sog_drop=(T_SPILL - timedelta(minutes=10), T_SPILL + timedelta(minutes=35), 7.5))
# innocent tanker on parallel lane 18 km west, steady
add_track(419000102, "SYN-TANKER-BRAVO", 84, "IMO9000002", s_lon - 0.17, s_lat, 335, 13, t_win0, 10)
# cargo crossing the area 1.5 h after spill (near slick but wrong time)
add_track(419000103, "SYN-CARGO-CHARLIE", 70, "IMO9000003", ORIGIN[0] + 0.35, ORIGIN[1] - 0.05, 270, 14,
          T_SPILL + timedelta(minutes=60), 5)
# cargo passing origin 3 h BEFORE spill
add_track(419000104, "SYN-CARGO-DELTA", 71, "IMO9000004", ORIGIN[0] - 0.02, ORIGIN[1] - 0.6, 10, 15,
          T_SPILL - timedelta(hours=3, minutes=30), 6)
# container ship with a course deviation but far away (40 km east)
add_track(419000105, "SYN-CNTR-ECHO", 79, "IMO9000005", ORIGIN[0] + 0.45, ORIGIN[1] - 0.4, 20, 18, t_win0, 9,
          turn=(T_SPILL, T_SPILL + timedelta(minutes=40), 35))
# fishing vessels loitering 25 km SW (slow, erratic)
for k in range(3):
    add_track(419000110 + k, f"SYN-FISH-{k}", 30, "", ORIGIN[0] - 0.22 - 0.03 * k, ORIGIN[1] - 0.18 + 0.02 * k,
              rng.uniform(0, 360), 3.5, t_win0, 10, step_min=5, noise=0.002)
# passenger ferry far north
add_track(419000120, "SYN-FERRY-GOLF", 60, "IMO9000006", ORIGIN[0] - 0.5, ORIGIN[1] + 1.1, 95, 20, t_win0, 8)
# tanker that came through ORIGIN 55 min after the spill with a short AIS gap – the hard negative
add_track(419000106, "SYN-TANKER-FOXTROT", 81, "IMO9000007", ORIGIN[0] + 0.02, ORIGIN[1] - 0.35, 355, 11,
          T_SPILL - timedelta(minutes=50), 6, gap=(T_SPILL + timedelta(minutes=50), T_SPILL + timedelta(minutes=68)))
# junk rows to exercise cleaning
rows_ais += [dict(MMSI=0, BaseDateTime="2025-03-14T00:00:00", LAT=91, LON=181, SOG=102.3, COG=360, Heading=511,
                  VesselName="BAD", IMO="", VesselType=0, Status=15, Length=0, Width=0, synthetic=True)]
rows_ais.append(dict(rows_ais[5]))          # exact duplicate
pd.DataFrame(rows_ais).to_csv(OUT / "synthetic_ais.csv", index=False)

json.dump({"synthetic": True, "t_obs": T_OBS.isoformat(), "t_spill_start": T_SPILL.isoformat(),
           "origin_lon": ORIGIN[0], "origin_lat": ORIGIN[1], "culprit_mmsi": 419000101,
           "true_age_hours": 4.17, "description": "Synthetic Arabian Sea discharge scenario for SIH26143 offline demo"},
          open(OUT / "scenario.json", "w"), indent=2)
print("demo data written to", OUT, "slick centre", c_lon, c_lat, "gt px", int(gt_mask.sum()))
