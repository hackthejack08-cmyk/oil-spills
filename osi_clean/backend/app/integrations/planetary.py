"""Sentinel-1 GRD via Microsoft Planetary Computer — NO account, NO quota (verified 2026-09-05).

* STAC search:  POST https://planetarycomputer.microsoft.com/api/stac/v1/search  (collection sentinel-1-grd)
* Assets are cloud-optimised GeoTIFFs + annotation XMLs in Azure blob storage; reads need a short-lived
  anonymous SAS token from /api/sas/v1/token/sentinel-1-grd (no sign-up).
* Only the annotation XMLs (~1–3 MB) and the decimated / AOI-windowed pixels are transferred, so a 40 m
  AOI product takes seconds–minutes instead of a 1 GB download.
Licence: Copernicus Sentinel data, free and open (attribution required).
"""
from __future__ import annotations

import json
from pathlib import Path

import time

import requests


def _get(url, tries=4, **kw):
    """GET with retry/back-off (the PC API occasionally times out under load)."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=kw.pop("timeout", 60), **kw); r.raise_for_status(); return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last = e; time.sleep(2 * (i + 1))
    raise last

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-grd"


def search(lon: float, lat: float, start: str, end: str, top: int = 10, bbox=None) -> list[dict]:
    geom = {"type": "Point", "coordinates": [lon, lat]}
    body = {"collections": ["sentinel-1-grd"], "datetime": f"{start}/{end}", "limit": top, "sortby": [{"field": "datetime", "direction": "desc"}]}
    if bbox:
        body["bbox"] = list(bbox)
    else:
        body["intersects"] = geom
    r = requests.post(STAC, json=body, timeout=60); r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        if p.get("sar:instrument_mode") not in (None, "IW"):
            continue
        out.append({"id": f["id"], "name": f["id"], "sensing_start": p.get("datetime"), "size_mb": None,
                    "orbit_direction": p.get("sat:orbit_state"), "polarisation": p.get("sar:polarizations"),
                    "platform": p.get("platform"), "footprint": f.get("geometry"), "bbox": f.get("bbox"),
                    "assets": {k: v["href"] for k, v in f["assets"].items() if k in ("vv", "vh", "schema-calibration-vv", "schema-calibration-vh",
                                                                                       "schema-noise-vv", "schema-noise-vh", "schema-product-vv", "schema-product-vh", "rendered_preview")},
                    "source": "planetary_computer"})
    return out


def item(item_id: str) -> dict:
    f = _get(f"https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/{item_id}").json()
    return {"id": f["id"], "assets": {k: v["href"] for k, v in f["assets"].items()}, "properties": f["properties"], "bbox": f.get("bbox")}


def sas_token() -> str:
    return _get(SAS, timeout=30).json()["token"]


def stage_annotations(assets: dict, dest: Path, token: str, progress=None) -> dict:
    """Download the small XML files; return the `files` dict expected by s1_calibrate.calibrate_product."""
    dest.mkdir(parents=True, exist_ok=True)
    files = {}
    for pol in ("vv", "vh"):
        if pol not in assets:
            continue
        entry = {"tif": "/vsicurl/" + assets[pol] + "?" + token}
        # NOTE: the STAC asset called "schema-product-*" points at annotation/rfi/rfi-iw-*.xml on some items
        # (verified 2026-09); the real product annotation is annotation/iw-<pol>.xml next to the measurement dir.
        ann_url = assets[pol].replace("/measurement/iw-" + pol + ".tiff", "/annotation/iw-" + pol + ".xml")
        for url, name in ((assets["schema-calibration-" + pol], "cal"), (assets["schema-noise-" + pol], "noise"), (ann_url, "ann")):
            p = dest / f"{name}-{pol}.xml"
            if not p.exists():
                if progress: progress(0, 1, f"fetching {name}-{pol}.xml")
                try:
                    r = _get(url + "?" + token, timeout=120)
                except requests.HTTPError as e:
                    if name != "ann" or e.response is None or e.response.status_code != 404:
                        raise
                    r = _get(assets["schema-product-" + pol] + "?" + token, timeout=120)
                p.write_bytes(r.content)
            entry[name] = p
        files[pol] = entry
    return files
