"""Backward / forward surface-oil drift.

Two engines behind one interface:

* `builtin`  – a compact, dependency-free stochastic Lagrangian tracker
  (Euler–Maruyama, 2-D surface layer). Physics identical in form to the
  OpenDrift *OceanDrift/OpenOil* surface advection:
        dx = (u_current + f_w · u_wind10) dt + sqrt(2·K_h·dt)·ξ
  with f_w sampled per particle from the literature wind-drift range and
  Gaussian perturbations on currents/wind (same magnitudes as OpenDrift
  defaults). Stokes drift is folded into f_w (NAS 2005: ≈2/3 of the ~3 %
  wind factor is Stokes drift). No weathering – irrelevant for pure
  hindcast positioning and *cannot* be inverted backwards anyway.
* `opendrift` – adapter that runs OpenDrift's OceanDrift with a negative
  time step when the package is installed (GPLv2 – kept as a separate
  optional process boundary; see licence audit).

Backward integration = same equation with dt < 0. IMPORTANT scientific note:
backward stochastic integration gives a *distribution of possible source
positions under the age hypothesis*, not a trajectory. We therefore run the
hindcast for a set of age hypotheses (1..N hours) and return, per hypothesis,
particle cloud + 50/90 % confidence ellipses. Spill age is not observable from
one SAR image (report §6), so the origin is reported as a *time-window × area*
envelope, never a point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from .. import config

R_EARTH = 6371008.8


@dataclass
class Forcing:
    """Gridded surface currents and 10 m wind on a regular lon/lat/time grid."""
    lon: np.ndarray
    lat: np.ndarray
    time: np.ndarray             # float hours since epoch0
    epoch0: datetime
    uo: np.ndarray               # (T, Y, X) m/s eastward current
    vo: np.ndarray
    u10: np.ndarray              # (T, Y, X) m/s eastward wind
    v10: np.ndarray
    source: Dict[str, str] = field(default_factory=dict)

    def _interp(self, arr):
        return RegularGridInterpolator((self.time, self.lat, self.lon), arr,
                                       bounds_error=False, fill_value=None)

    def sample(self, t: datetime, lon: np.ndarray, lat: np.ndarray):
        th = (t - self.epoch0).total_seconds() / 3600.0
        pts = np.column_stack([np.full_like(lon, th), lat, lon])
        if not hasattr(self, "_f"):
            self._f = {k: self._interp(getattr(self, k)) for k in ("uo", "vo", "u10", "v10")}
        return tuple(self._f[k](pts) for k in ("uo", "vo", "u10", "v10"))

    def wind_at(self, t: datetime, lon: float, lat: float) -> float:
        _, _, u, v = self.sample(t, np.array([lon]), np.array([lat]))
        return float(np.hypot(u[0], v[0]))

    @classmethod
    def from_netcdf(cls, path: str) -> "Forcing":
        import xarray as xr
        ds = xr.open_dataset(path)
        t = ds["time"].values.astype("datetime64[s]").astype("int64")
        epoch0 = datetime.fromtimestamp(int(t[0]), tz=timezone.utc)
        return cls(lon=ds["lon"].values.astype(float), lat=ds["lat"].values.astype(float),
                   time=(t - t[0]) / 3600.0, epoch0=epoch0,
                   uo=ds["uo"].values, vo=ds["vo"].values, u10=ds["u10"].values, v10=ds["v10"].values,
                   source=dict(ds.attrs))


@dataclass
class DriftResult:
    direction: str                          # "backward" | "forward"
    hypotheses: List[dict]                  # per age-hour: cloud + ellipse + centre
    centre_track: List[Tuple[float, float, str]]   # (lon, lat, iso time) of cloud mean per step
    params: dict


def _ellipse(lon: np.ndarray, lat: np.ndarray, k: float) -> dict:
    """k-sigma covariance ellipse in local metres, returned as GeoJSON polygon."""
    lat0, lon0 = lat.mean(), lon.mean()
    x = np.radians(lon - lon0) * R_EARTH * np.cos(np.radians(lat0))
    y = np.radians(lat - lat0) * R_EARTH
    cov = np.cov(np.vstack([x, y]))
    vals, vecs = np.linalg.eigh(cov)
    vals = np.maximum(vals, 1.0)
    ang = np.linspace(0, 2 * np.pi, 48)
    circ = np.vstack([np.cos(ang), np.sin(ang)]) * (k * np.sqrt(vals))[:, None]
    pts = vecs @ circ
    plon = lon0 + np.degrees(pts[0] / (R_EARTH * np.cos(np.radians(lat0))))
    plat = lat0 + np.degrees(pts[1] / R_EARTH)
    return {"type": "Polygon", "coordinates": [[[float(a), float(b)] for a, b in zip(plon, plat)]],
            "semi_axes_km": [float(k * np.sqrt(vals[1]) / 1e3), float(k * np.sqrt(vals[0]) / 1e3)]}


def _land_tester(seed_lon, seed_lat, hours):
    """Return f(lon, lat) -> bool array (True = on land) using the Natural Earth 10 m polygons, or None."""
    try:
        from ..sar.preprocess import _land_geoms
        from shapely.geometry import box
        from shapely import contains_xy, union_all
        polys, tree = _land_geoms()
        if not polys:
            return None
        reach = 0.02 * hours + 0.5                           # generous search radius in degrees (~2 km/h + margin)
        bb = box(float(np.min(seed_lon)) - reach, float(np.min(seed_lat)) - reach, float(np.max(seed_lon)) + reach, float(np.max(seed_lat)) + reach)
        hits = [polys[i] for i in tree.query(bb)]
        if not hits:
            return None
        geom = union_all([g.intersection(bb) for g in hits])
        if geom.is_empty:
            return None
        return lambda lo, la: contains_xy(geom, lo, la)
    except Exception:
        return None


def simulate(forcing: Forcing, seed_lon: np.ndarray, seed_lat: np.ndarray, t0: datetime,
             hours: float, backward: bool, n_particles: int = config.DRIFT_N_PARTICLES,
             dt_s: int = config.DRIFT_DT_SECONDS, rng_seed: int = 42) -> DriftResult:
    rng = np.random.default_rng(rng_seed)
    idx = rng.integers(0, len(seed_lon), n_particles)
    lon = seed_lon[idx].astype(float).copy()
    lat = seed_lat[idx].astype(float).copy()
    fw = rng.uniform(*config.WIND_DRIFT_FACTOR, n_particles)
    # Coriolis deflection of the wind-driven component: clockwise (to the right) in the northern hemisphere,
    # anticlockwise in the southern. Per-particle angle sampled from the configured range.
    hemi = 1.0 if float(np.mean(seed_lat)) >= 0 else -1.0
    ang = np.radians(rng.uniform(*config.WIND_DEFLECTION_DEG, n_particles)) * hemi
    cos_a, sin_a = np.cos(ang), np.sin(ang)
    land = _land_tester(seed_lon, seed_lat, hours)
    stranded = np.zeros(n_particles, bool)
    sign = -1.0 if backward else 1.0
    dt = sign * dt_s
    n_steps = int(round(hours * 3600 / dt_s))
    t = t0
    track = [(float(lon.mean()), float(lat.mean()), t.isoformat())]
    hyps = []
    steps_per_hour = int(3600 / dt_s)
    for s in range(1, n_steps + 1):
        uo, vo, u10, v10 = forcing.sample(t, lon, lat)
        uo = uo + rng.normal(0, config.CURRENT_UNCERTAINTY, n_particles)
        vo = vo + rng.normal(0, config.CURRENT_UNCERTAINTY, n_particles)
        u10 = u10 + rng.normal(0, config.WIND_UNCERTAINTY, n_particles)
        v10 = v10 + rng.normal(0, config.WIND_UNCERTAINTY, n_particles)
        # rotate wind vector by -ang (right of wind when ang>0): (u,v) -> (u cos a + v sin a, -u sin a + v cos a)
        wu = u10 * cos_a + v10 * sin_a
        wv = -u10 * sin_a + v10 * cos_a
        u = np.where(stranded, 0.0, uo + fw * wu)
        v = np.where(stranded, 0.0, vo + fw * wv)
        diff = np.sqrt(2 * config.HORIZONTAL_DIFFUSIVITY * dt_s)
        dx = u * dt + diff * rng.normal(size=n_particles)
        dy = v * dt + diff * rng.normal(size=n_particles)
        new_lat = lat + np.degrees(dy / R_EARTH)
        new_lon = lon + np.degrees(dx / (R_EARTH * np.cos(np.radians(new_lat))))
        if land is not None:
            hit = land(new_lon, new_lat) & ~stranded
            stranded |= hit                                  # beached particles stop (no re-floating)
            new_lon = np.where(hit, lon, new_lon); new_lat = np.where(hit, lat, new_lat)
        lon, lat = new_lon, new_lat
        t = t + timedelta(seconds=dt)
        track.append((float(lon.mean()), float(lat.mean()), t.isoformat()))
        if s % steps_per_hour == 0:
            hyps.append({
                "age_hours": s // steps_per_hour,
                "time": t.isoformat(),
                "centre": [float(lon.mean()), float(lat.mean())],
                "ellipse50": _ellipse(lon, lat, 1.177),   # 50 % of a 2-D Gaussian
                "ellipse90": _ellipse(lon, lat, 2.146),   # 90 %
                "particles": np.column_stack([lon, lat]).round(5).tolist()[:150],
                "stranded_fraction": round(float(stranded.mean()), 3),
                "spread_km": float(np.std(np.hypot(
                    np.radians(lon - lon.mean()) * R_EARTH * np.cos(np.radians(lat.mean())),
                    np.radians(lat - lat.mean()) * R_EARTH)) / 1e3),
            })
    return DriftResult("backward" if backward else "forward", hyps, track, {
        "engine": "builtin-lagrangian", "n_particles": n_particles, "dt_s": dt_s,
        "wind_drift_factor": list(config.WIND_DRIFT_FACTOR),
        "wind_deflection_deg": list(config.WIND_DEFLECTION_DEG), "hemisphere": "N" if hemi > 0 else "S",
        "land_interaction": "beaching (Natural Earth 10 m)" if land is not None else "none (no coastline data)",
        "horizontal_diffusivity_m2s": config.HORIZONTAL_DIFFUSIVITY,
        "current_uncertainty_ms": config.CURRENT_UNCERTAINTY, "wind_uncertainty_ms": config.WIND_UNCERTAINTY,
        "forcing_source": forcing.source, "weathering": "none (not invertible backward)"})


def simulate_opendrift(forcing_nc: str, seed_lon, seed_lat, t0, hours, backward):  # pragma: no cover
    """Optional adapter. Requires `pip install opendrift` (GPLv2)."""
    from opendrift.models.oceandrift import OceanDrift
    from opendrift.readers import reader_netCDF_CF_generic
    o = OceanDrift(loglevel=30)
    o.add_reader(reader_netCDF_CF_generic.Reader(forcing_nc))
    o.set_config("drift:wind_drift_factor", 0.03)
    o.set_config("drift:horizontal_diffusivity", config.HORIZONTAL_DIFFUSIVITY)
    o.seed_elements(lon=seed_lon, lat=seed_lat, time=t0, number=len(seed_lon))
    o.run(duration=timedelta(hours=hours), time_step=(-1 if backward else 1) * config.DRIFT_DT_SECONDS)
    return o
