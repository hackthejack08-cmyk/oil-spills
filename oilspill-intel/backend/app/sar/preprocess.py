"""Minimal Sentinel-1 GRD preprocessing for oil-spill segmentation.

Design decision (see report §7): the *minimum* pipeline for GRD IW dual-pol is
  1. read sigma0 (already calibrated when using CDSE/GEE-style GRD-sigma0 GeoTIFF
     or the Zenodo Trujillo-Acatitla dataset which ships sigma0 in dB)
  2. convert linear -> dB if needed
  3. clip + normalise to [0, 1]
  4. land mask (from vector coastline or NaN/no-data)
  5. tile with overlap

We deliberately do NOT do terrain correction (flat sea), and speckle filtering is
optional (learned filters inside the CNN cope with speckle; a 3x3 median is
offered as a config switch). Incidence-angle normalisation is left as an
*experimental* option because the training dataset itself is not normalised.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Tuple

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.crs import CRS

from .. import config


@dataclass
class Scene:
    """In-memory representation of a preprocessed scene."""
    db: np.ndarray            # (C, H, W) float32 sigma0 in dB (NaN = no data / land)
    transform: Affine
    crs: CRS
    meta: dict
    land: np.ndarray | None = None   # optional land/shore mask (True = land), from Natural Earth

    @property
    def shape(self) -> Tuple[int, int]:
        return self.db.shape[1], self.db.shape[2]


def linear_to_db(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 10.0 * np.log10(np.where(arr > 0, arr, np.nan))
    return out


def looks_linear(arr: np.ndarray) -> bool:
    """Heuristic: sigma0 linear ocean values are ~1e-3..1; dB values are negative."""
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return False
    return float(np.nanmedian(finite)) > -1.0 and float(np.nanmax(finite)) < 50


def read_scene(path: str) -> Scene:
    with rasterio.open(path) as ds:
        arr = ds.read().astype(np.float32)
        nodata = ds.nodata
        if nodata is not None:
            arr[arr == nodata] = np.nan
        transform, crs = ds.transform, ds.crs
        tags = ds.tags()
    if arr.shape[0] == 1:                      # single-pol -> duplicate so the 2-ch model still runs
        arr = np.concatenate([arr, arr], axis=0)
        tags["polarisation_note"] = "single-pol input duplicated to 2 channels"
    arr = arr[:2]
    if looks_linear(arr):
        arr = linear_to_db(arr)
    return Scene(db=arr, transform=transform, crs=crs if crs else CRS.from_epsg(4326), meta=tags)


def normalise(db: np.ndarray) -> np.ndarray:
    lo, hi = config.DB_CLIP
    x = np.clip(np.nan_to_num(db, nan=lo), lo, hi)
    return ((x - lo) / (hi - lo)).astype(np.float32)


_LAND = None


def _land_geoms():
    """Natural Earth 10 m land polygons (public domain), loaded once. Returns [] if the file is absent."""
    global _LAND
    if _LAND is None:
        import json
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        p = config.DEMO_DIR / "ne_10m_land.geojson"
        if p.exists():
            polys = []
            for f in json.load(open(p))["features"]:
                g = shape(f["geometry"])
                polys += list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
            _LAND = (polys, STRtree(polys))
        else:
            _LAND = ([], None)
    return _LAND


def shore_distance_km(shape_hw, transform, crs) -> np.ndarray | None:
    """Approximate distance-to-shore (km) per pixel from the Natural Earth land mask (Euclidean distance
    transform in pixel units × mean pixel size). None if no coastline data."""
    land = land_mask(shape_hw, transform, crs, buffer_km=0.0)
    if not land.any():
        return None
    from scipy.ndimage import distance_transform_edt
    px_km = 111.0 * abs(transform.a) * np.cos(np.deg2rad(transform.f)) if crs is None or crs.is_geographic else abs(transform.a) / 1000.0
    return distance_transform_edt(~land) * px_km


def land_mask(shape_hw, transform, crs, buffer_km: float = 1.0) -> np.ndarray:
    """True where a pixel is land (or within `buffer_km` of the shoreline — the wind-shadow / shallow-water
    look-alike zone). Uses the Natural Earth 10 m polygons rasterised onto the scene grid; empty if unavailable
    or CRS is not geographic. Coastline accuracy of NE 10 m is ~ few hundred m (Verified: dataset spec 1:10 M)."""
    polys, tree = _land_geoms()
    h, w = shape_hw
    if not polys or (crs is not None and not crs.is_geographic):
        return np.zeros((h, w), bool)
    from rasterio import features
    from shapely.geometry import box
    xs = [transform * (0, 0), transform * (w, h)]
    bb = box(min(xs[0][0], xs[1][0]), min(xs[0][1], xs[1][1]), max(xs[0][0], xs[1][0]), max(xs[0][1], xs[1][1]))
    idx = tree.query(bb)
    hits = [polys[i].intersection(bb.buffer(0.1)) for i in idx]
    hits = [g for g in hits if not g.is_empty]
    if not hits:
        return np.zeros((h, w), bool)
    buf = buffer_km / 111.0
    geoms = [g.buffer(buf) if buf > 0 else g for g in hits]
    return features.rasterize(((g, 1) for g in geoms), out_shape=(h, w), transform=transform, fill=0, dtype="uint8").astype(bool)


def sea_mask(db: np.ndarray, bright_db: float = -5.0, land: np.ndarray | None = None) -> np.ndarray:
    """Valid-sea mask: finite, not persistently bright (> -5 dB VV: land/ships) and, when a coastline is
    available, not land / shoreline buffer (see land_mask)."""
    vv = db[0]
    mask = np.isfinite(vv)
    mask &= vv < bright_db
    if land is not None:
        mask &= ~land
    return mask


def tiles(h: int, w: int, tile: int = config.TILE, overlap: int = config.TILE_OVERLAP
          ) -> Iterator[Tuple[int, int, int, int]]:
    step = tile - overlap
    for r in range(0, max(h - overlap, 1), step):
        for c in range(0, max(w - overlap, 1), step):
            r0, c0 = min(r, max(h - tile, 0)), min(c, max(w - tile, 0))
            yield r0, c0, min(r0 + tile, h), min(c0 + tile, w)
