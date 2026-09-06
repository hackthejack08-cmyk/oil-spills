"""Copernicus Data Space Ecosystem (CDSE) – Sentinel-1 GRD search & download.

Search: public OData catalogue, no account needed  [verified live 2026-09-05].
Download: needs a free CDSE account (OSI_CDSE_USER / OSI_CDSE_PASSWORD env) –
OAuth2 password grant against the CDSE identity service, then a zipper download
of the product. Downloads count against the free monthly quota.

Products: we prefer the *COG* GRD variant (IW_GRDH_1S-COG) when present – it
is a Cloud-Optimised GeoTIFF SAFE that GDAL/rasterio can open directly.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Optional

import requests

CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
DOWNLOAD = "https://download.dataspace.copernicus.eu/odata/v1/Products({pid})/$value"


def _norm(s: str, tail: str) -> str:
    """Accept 'YYYY-MM-DD' or a full ISO-8601 instant; return OData-friendly UTC instant."""
    s = s.strip()
    if len(s) == 10:
        return s + tail
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    d = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def search(lon: float, lat: float, start: str, end: str, top: int = 10, mode: str = "IW_GRDH",
           polygon_wkt: Optional[str] = None) -> list[dict]:
    geom = polygon_wkt or f"POINT({lon} {lat})"
    start, end = _norm(start, "T00:00:00.000Z"), _norm(end, "T23:59:59.000Z")
    flt = (f"Collection/Name eq 'SENTINEL-1' and contains(Name,'{mode}') and "
           f"OData.CSC.Intersects(area=geography'SRID=4326;{geom}') and "
           f"ContentDate/Start gt {start} and ContentDate/Start lt {end}")
    r = requests.get(CATALOGUE, params={"$filter": flt, "$top": top, "$orderby": "ContentDate/Start desc",
                                        "$expand": "Attributes"}, timeout=60)
    r.raise_for_status()
    out = []
    for p in r.json().get("value", []):
        attrs = {a["Name"]: a.get("Value") for a in p.get("Attributes", [])}
        out.append({"id": p["Id"], "name": p["Name"], "sensing_start": p["ContentDate"]["Start"],
                    "size_mb": round((p.get("ContentLength") or 0) / 1e6, 1), "s3_path": p.get("S3Path"),
                    "cog": "COG" in p["Name"], "orbit_direction": attrs.get("orbitDirection"),
                    "polarisation": attrs.get("polarisationChannels"), "footprint": p.get("GeoFootprint"),
                    "online": p.get("Online", True)})
    return out


def _token() -> str:
    user, pw = os.getenv("OSI_CDSE_USER"), os.getenv("OSI_CDSE_PASSWORD")
    if not user or not pw:
        raise PermissionError("Set OSI_CDSE_USER and OSI_CDSE_PASSWORD (free account at dataspace.copernicus.eu)")
    r = requests.post(TOKEN_URL, data={"client_id": "cdse-public", "grant_type": "password", "username": user, "password": pw}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def download(product_id: str, dest_dir: Path, progress=None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    zpath = dest_dir / f"{product_id}.zip"
    s = requests.Session(); s.headers["Authorization"] = f"Bearer {_token()}"
    with s.get(DOWNLOAD.format(pid=product_id), stream=True, timeout=120, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)); done = 0
        with zpath.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk); done += len(chunk)
                if progress: progress(done, total)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest_dir)
    safe = next(dest_dir.glob("*.SAFE"))
    zpath.unlink(missing_ok=True)
    return safe


def measurement_tiffs(safe: Path) -> dict[str, Path]:
    """Return {'vv': path, 'vh': path} for the measurement GeoTIFFs inside a SAFE."""
    out = {}
    for p in (safe / "measurement").glob("*.tif*"):
        for pol in ("vv", "vh", "hh", "hv"):
            if f"-{pol}-" in p.name.lower():
                out[pol] = p
    return out
