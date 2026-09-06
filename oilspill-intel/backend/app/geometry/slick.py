"""Spill geometry extraction with correct CRS handling.

Pixels -> polygon in scene CRS -> WGS84 for storage -> geodesic area/perimeter
(pyproj.Geod on WGS84 ellipsoid) -> local azimuthal-equidistant projection
centred on the slick for shape metrics (major/minor axis, orientation).

We never derive km² from pixel counts × nominal pixel size, because Sentinel-1
GRD tiles reprojected to EPSG:4326 have latitude-dependent pixel footprints.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

import numpy as np
from pyproj import Geod, Transformer, CRS as PCRS
from rasterio import features
from rasterio.transform import Affine
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import transform as shp_transform, unary_union

GEOD = Geod(ellps="WGS84")


@dataclass
class SlickGeometry:
    polygon_geojson: dict
    centroid_lon: float
    centroid_lat: float
    area_km2: float
    perimeter_km: float
    length_km: float
    width_km: float
    orientation_deg: float           # major axis bearing, 0..180 clockwise from north
    elongation: float
    compactness: float               # 4πA / P²  (1 = circle)
    bbox: List[float]                # [minlon, minlat, maxlon, maxlat]
    pixel_count: int
    mean_prob: float
    mean_contrast_db: float
    touches_land: bool
    shore_km: Optional[float] = None

    def dict(self):
        return asdict(self)


def _local_aeqd(lon: float, lat: float) -> Transformer:
    aeqd = PCRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m")
    return Transformer.from_crs("EPSG:4326", aeqd, always_xy=True)


def _axes(poly_m: Polygon):
    """Major/minor axis via PCA of boundary coordinates (metres)."""
    xy = np.asarray(poly_m.exterior.coords)
    xy = xy - xy.mean(axis=0)
    cov = np.cov(xy.T)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    v = vecs[:, order[0]]
    proj_major = xy @ v
    proj_minor = xy @ vecs[:, order[1]]
    length = float(proj_major.max() - proj_major.min())
    width = float(proj_minor.max() - proj_minor.min())
    bearing = (np.degrees(np.arctan2(v[0], v[1])) + 360) % 180   # x=east, y=north
    return length, width, float(bearing)


def extract(mask: np.ndarray, prob: np.ndarray, db_vv: np.ndarray, transform: Affine,
            crs, land_mask: Optional[np.ndarray] = None, min_area_km2: float = 0.02, shore_km: Optional[np.ndarray] = None
            ) -> List[SlickGeometry]:
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True) if crs and not PCRS(crs).equals("EPSG:4326") else None
    results: List[SlickGeometry] = []
    from scipy import ndimage as ndi
    labels, n = ndi.label(mask)
    # Background reference = surrounding SEA pixels only: exclude every dark object and all land (land is 10–20 dB
    # brighter than sea and would otherwise inflate the contrast of any object near a coast).
    valid = np.isfinite(db_vv) & ~mask
    if land_mask is not None:
        valid &= ~land_mask
    for i in range(1, n + 1):
        comp = labels == i
        ring_px = int(np.clip(np.sqrt(comp.sum()) * 1.5, 15, 150))       # ring width scales with object size
        ring = ndi.binary_dilation(comp, iterations=ring_px) & valid & ~comp if ring_px <= 60 else \
            (ndi.binary_dilation(comp, structure=np.ones((3, 3)), iterations=ring_px // 2) & valid & ~comp)
        if ring.sum() >= 50:
            bg_val = float(np.median(db_vv[ring]))
        else:                                                              # fallback: mean of valid sea in a window
            r0, c0 = np.argwhere(comp).min(0); r1, c1 = np.argwhere(comp).max(0)
            pad = 200; win = (slice(max(r0 - pad, 0), r1 + pad), slice(max(c0 - pad, 0), c1 + pad))
            v = db_vv[win][valid[win]]
            bg_val = float(np.median(v)) if v.size else float(np.nanmedian(db_vv))
        geoms = [shape(g) for g, v in features.shapes(comp.astype(np.uint8), mask=comp, transform=transform) if v == 1]
        if not geoms:
            continue
        poly = unary_union(geoms)
        if to_wgs is not None:
            poly = shp_transform(to_wgs.transform, poly)
        poly = poly.simplify(1e-4, preserve_topology=True)
        if poly.is_empty:
            continue
        # geodesic area/perimeter
        polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
        area = sum(abs(GEOD.geometry_area_perimeter(p)[0]) for p in polys)
        perim = sum(abs(GEOD.geometry_area_perimeter(p)[1]) for p in polys)
        area_km2 = area / 1e6
        if area_km2 < min_area_km2:
            continue
        c = poly.centroid
        tr = _local_aeqd(c.x, c.y)
        poly_m = shp_transform(tr.transform, poly)
        biggest = max(polys, key=lambda p: p.area)
        length, width, bearing = _axes(shp_transform(tr.transform, biggest))
        width = max(width, 1.0)
        contrast = float(bg_val - np.nanmean(db_vv[comp]))
        touches = bool(land_mask[comp].any()) if land_mask is not None else False
        if shore_km is not None and float(np.nanmin(shore_km[comp])) <= 1.25:   # abuts the 1 km shoreline buffer
            touches = True
        results.append(SlickGeometry(
            polygon_geojson=mapping(poly), centroid_lon=float(c.x), centroid_lat=float(c.y),
            area_km2=round(area_km2, 4), perimeter_km=round(perim / 1e3, 3),
            length_km=round(length / 1e3, 3), width_km=round(width / 1e3, 3),
            orientation_deg=round(bearing, 1), elongation=round(length / width, 2),
            compactness=round(4 * np.pi * area / max(perim, 1) ** 2, 4),
            bbox=list(poly.bounds), pixel_count=int(comp.sum()),
            mean_prob=round(float(prob[comp].mean()), 3), mean_contrast_db=round(contrast, 2),
            touches_land=touches, shore_km=None if shore_km is None else round(float(np.nanmin(shore_km[comp])), 2)))
    results.sort(key=lambda s: -s.area_km2)
    return results
