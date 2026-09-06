"""Ocean-current and wind connectors → CF NetCDF forcing cube (uo, vo, u10, v10).

Currents
  * HYCOM ESPC-D-V02 (Aug-2024 → now, 3-hourly, 1/12°) and GLBy0.08 expt_93.0
    (Dec-2018 → Sep-2024) via OPeNDAP – **no account**  [verified live].
  * Copernicus Marine (CMEMS) via the official `copernicusmarine` toolbox –
    free account (OSI_CMEMS_USER / OSI_CMEMS_PASSWORD); hourly 1/12° surface.
Wind
  * Open-Meteo Historical/ERA5 archive – no key, CC BY 4.0 [verified live];
    a small grid of point queries is assembled into a field.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr

HYCOM = {
    "espc": {"u": "https://tds.hycom.org/thredds/dodsC/ESPC-D-V02/u3z", "v": "https://tds.hycom.org/thredds/dodsC/ESPC-D-V02/v3z",
             "start": datetime(2024, 8, 10, tzinfo=timezone.utc)},
    "glby93": {"uv": "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/uv3z",
               "start": datetime(2018, 12, 4, tzinfo=timezone.utc), "end": datetime(2024, 9, 5, tzinfo=timezone.utc)},
}


def _hycom_url(t0: datetime) -> tuple[str, dict]:
    """Pick the HYCOM dataset covering t0 → (source label, {var: url})."""
    if t0 >= HYCOM["espc"]["start"]:
        return "HYCOM ESPC-D-V02", {"water_u": HYCOM["espc"]["u"], "water_v": HYCOM["espc"]["v"]}
    if t0 >= HYCOM["glby93"]["start"]:
        return "HYCOM GLBy0.08 expt_93.0", {"water_u": HYCOM["glby93"]["uv"], "water_v": HYCOM["glby93"]["uv"]}
    raise ValueError("HYCOM OPeNDAP archive here starts 2018-12-04; use CMEMS GLORYS for earlier dates")


def _hycom_subset(bbox, t0: datetime, t1: datetime) -> xr.Dataset:
    import netCDF4
    lon0, lat0, lon1, lat1 = bbox
    src, urls = _hycom_url(t0)
    out = {}
    for var, url in urls.items():
        ds = netCDF4.Dataset(url)
        lon = ds["lon"][:]; lat = ds["lat"][:]; tm = ds["time"]
        tvals = netCDF4.num2date(tm[:], tm.units, only_use_cftime_datetimes=False)
        tvals = np.array([pd.Timestamp(t).tz_localize("UTC") for t in tvals])
        # HYCOM lon is 0..360
        qlon0, qlon1 = lon0 % 360, lon1 % 360
        i0, i1 = np.searchsorted(lon, qlon0) - 1, np.searchsorted(lon, qlon1) + 1
        j0, j1 = np.searchsorted(lat, lat0) - 1, np.searchsorted(lat, lat1) + 1
        k0 = max(int(np.searchsorted(tvals, pd.Timestamp(t0))) - 1, 0); k1 = min(int(np.searchsorted(tvals, pd.Timestamp(t1))) + 1, len(tvals))
        data = ds[var][k0:k1, 0, j0:j1, i0:i1].astype("float32")          # depth index 0 = surface
        data = np.ma.filled(data, np.nan)
        out[var] = xr.DataArray(data, dims=("time", "lat", "lon"),
                                coords={"time": [t.tz_convert(None) for t in tvals[k0:k1]], "lat": lat[j0:j1],
                                        "lon": ((lon[i0:i1] + 180) % 360) - 180})
        ds.close()
    res = xr.Dataset({"uo": out["water_u"], "vo": out["water_v"]}).sortby("lon")
    res.attrs["current_source"] = src + " (OPeNDAP, tds.hycom.org)"
    return res


def _cmems_subset(bbox, t0, t1) -> xr.Dataset:
    import copernicusmarine as cm
    user, pw = os.getenv("OSI_CMEMS_USER"), os.getenv("OSI_CMEMS_PASSWORD")
    if not user or not pw:
        raise PermissionError("Set OSI_CMEMS_USER / OSI_CMEMS_PASSWORD (free account at marine.copernicus.eu)")
    ds = cm.open_dataset(dataset_id="cmems_mod_glo_phy_anfc_merged-uv_PT1H-i", username=user, password=pw,
                         variables=["uo", "vo"], minimum_longitude=bbox[0], maximum_longitude=bbox[2],
                         minimum_latitude=bbox[1], maximum_latitude=bbox[3], start_datetime=t0.isoformat(), end_datetime=t1.isoformat(),
                         minimum_depth=0, maximum_depth=1)
    ds = ds.isel(depth=0, drop=True).rename({"latitude": "lat", "longitude": "lon"})
    ds.attrs["current_source"] = "E.U. Copernicus Marine Service Information; https://doi.org/10.48670/moi-00016"
    return ds[["uo", "vo"]]


def _openmeteo_wind(bbox, t0: datetime, t1: datetime, n: int = 4) -> xr.Dataset:
    lons = np.linspace(bbox[0], bbox[2], n); lats = np.linspace(bbox[1], bbox[3], n)
    LON, LAT = np.meshgrid(lons, lats)
    # archive API for past dates, forecast API (with past_days) for the last ~5 days
    recent = (datetime.now(timezone.utc) - t1) < timedelta(days=5)
    if recent:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {"past_days": 7, "forecast_days": 2}
    else:
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {"start_date": (t0 - timedelta(days=1)).date().isoformat(), "end_date": (t1 + timedelta(days=1)).date().isoformat()}
    params |= {"latitude": ",".join(f"{v:.4f}" for v in LAT.ravel()), "longitude": ",".join(f"{v:.4f}" for v in LON.ravel()),
               "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "timezone": "GMT"}
    r = requests.get(url, params=params, timeout=90); r.raise_for_status()
    js = r.json(); js = js if isinstance(js, list) else [js]
    times = pd.to_datetime(js[0]["hourly"]["time"])
    U = np.full((len(times), n, n), np.nan, np.float32); V = U.copy()
    for k, item in enumerate(js):
        spd = np.array(item["hourly"]["wind_speed_10m"], float); d = np.radians(np.array(item["hourly"]["wind_direction_10m"], float))
        # meteorological direction = where wind comes FROM
        U[:, k // n, k % n] = -spd * np.sin(d); V[:, k // n, k % n] = -spd * np.cos(d)
    ds = xr.Dataset({"u10": (("time", "lat", "lon"), U), "v10": (("time", "lat", "lon"), V)}, coords={"time": times, "lat": lats, "lon": lons})
    ds.attrs["wind_source"] = "Open-Meteo (ERA5/IFS reanalysis; CC BY 4.0)"
    return ds


def build_forcing(bbox, t_obs: datetime, hours_back: int, hours_fwd: int, out_nc: Path, currents: str = "hycom") -> dict:
    t0, t1 = t_obs - timedelta(hours=hours_back + 3), t_obs + timedelta(hours=hours_fwd + 3)
    cur = _cmems_subset(bbox, t0, t1) if currents == "cmems" else _hycom_subset(bbox, t0, t1)
    wind = _openmeteo_wind(bbox, t0, t1)
    # common hourly time axis, common grid = current grid
    times = pd.date_range(t0.replace(minute=0, second=0, tzinfo=None), t1.replace(minute=0, second=0, tzinfo=None), freq="1h")
    cur = cur.interp(time=times, method="linear", kwargs={"fill_value": "extrapolate"})
    wind = wind.interp(time=times, method="linear", kwargs={"fill_value": "extrapolate"}).interp(lat=cur.lat, lon=cur.lon, kwargs={"fill_value": None})
    # land / missing cells: fill with the spatial mean of each time slice (drift over land is masked elsewhere)
    for v in ("uo", "vo"):
        cur[v] = cur[v].fillna(cur[v].mean(("lat", "lon")))
    for v in ("u10", "v10"):
        wind[v] = wind[v].fillna(wind[v].mean(("lat", "lon")))
    ds = xr.merge([cur, wind]).fillna(0.0)
    ds.attrs.update({"title": "OSI forcing cube", "synthetic": "false", "current_source": cur.attrs.get("current_source", ""),
                     "wind_source": wind.attrs.get("wind_source", ""), "bbox": str(bbox), "built_at": datetime.now(timezone.utc).isoformat()})
    out_nc.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_nc)
    return {"path": str(out_nc), "current_source": ds.attrs["current_source"], "wind_source": ds.attrs["wind_source"],
            "time_range": [str(times[0]), str(times[-1])], "grid": [int(ds.lat.size), int(ds.lon.size)],
            "mean_current_ms": float(np.hypot(ds.uo, ds.vo).mean()), "mean_wind_ms": float(np.hypot(ds.u10, ds.v10).mean())}
