"""/api/data/* – live data connectors (Sentinel-1, met-ocean, AIS) run as background jobs.
Every product fetched is registered under runtime/uploads/<id>.<ext> so the
existing analyze endpoints can consume it via its id (same path-safety model)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, jobs, pipeline
from ..integrations import cdse, planetary, s1_calibrate, metocean, ais_sources

router = APIRouter(prefix="/api/data", tags=["data-sources"])
UPLOADS = config.DATA_DIR / "uploads"
CACHE = config.DATA_DIR / "cache"


def _iso(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00")); return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _internet() -> bool:
    import socket
    try:
        socket.create_connection(("catalogue.dataspace.copernicus.eu", 443), timeout=3).close(); return True
    except OSError:
        return False


@router.get("/status")
def status():
    """Which connectors are configured (never returns secrets)."""
    cdse_dl = bool(os.getenv("OSI_CDSE_USER")); cmems = bool(os.getenv("OSI_CMEMS_USER")); ais_key = bool(os.getenv("OSI_AISSTREAM_KEY"))
    return {"internet": _internet(), "connectors": [
        {"id": "planetary", "name": "Sentinel-1 GRD – Microsoft Planetary Computer (STAC + COG)", "ready": True, "account": "none, no quota", "note": "default: search + AOI streaming + σ⁰ calibration, ~15–60 s per AOI"},
        {"id": "cdse_search", "name": "Sentinel-1 catalogue (CDSE OData)", "ready": True, "account": "none", "note": "public search (fallback provider)"},
        {"id": "cdse_download", "name": "Sentinel-1 GRD download + σ⁰ calibration", "ready": cdse_dl, "account": "free CDSE account (dataspace.copernicus.eu); monthly quota", "note": "set OSI_CDSE_USER/OSI_CDSE_PASSWORD; ~1 GB per scene"},
        {"id": "hycom", "name": "Ocean currents – HYCOM GOFS/ESPC (OPeNDAP)", "ready": True, "account": "none", "note": "2018-12 → today + 8-day forecast, 1/12°, 3-hourly"},
        {"id": "cmems", "name": "Ocean currents – Copernicus Marine (optional)", "ready": cmems, "account": "free CMEMS account", "note": "set OSI_CMEMS_USER/OSI_CMEMS_PASSWORD; needs `copernicusmarine`"},
        {"id": "open_meteo", "name": "10 m wind – Open-Meteo ERA5 archive", "ready": True, "account": "none (CC BY 4.0, non-commercial free tier)", "note": "0.25°, hourly, 1940 →"},
        {"id": "marinecadastre", "name": "Historical AIS – NOAA MarineCadastre", "ready": True, "account": "none (CC0)", "note": "US waters; 2009–2023 direct daily zips verified; newer years via AccessAIS export"},
        {"id": "dma", "name": "Historical AIS – Danish Maritime Authority", "ready": True, "account": "none", "note": "Danish waters; ~2 GB per day file"},
        {"id": "aisstream", "name": "Live AIS – aisstream.io", "ready": ais_key, "account": "free API key", "note": "real-time only, no history; set OSI_AISSTREAM_KEY"},
    ]}


# ---------------- Sentinel-1 -----------------------------------------------
class S1Search(BaseModel):
    lon: float = Field(..., ge=-180, le=180); lat: float = Field(..., ge=-90, le=90)
    start: str; end: str; top: int = Field(10, ge=1, le=50)
    provider: str = Field("planetary", pattern="^(planetary|cdse)$")


@router.post("/sentinel1/search")
def s1_search(b: S1Search):
    """Sentinel-1 IW GRD search. Default provider = Microsoft Planetary Computer (no account, no quota);
    'cdse' = Copernicus Data Space catalogue (search public; download needs a free account)."""
    try:
        if b.provider == "planetary":
            return {"provider": "planetary", "products": planetary.search(b.lon, b.lat, b.start, b.end, b.top)}
        return {"provider": "cdse", "products": cdse.search(b.lon, b.lat, b.start, b.end, b.top)}
    except Exception as exc:
        raise HTTPException(502, f"{b.provider} catalogue error: {exc}")


class S1Fetch(BaseModel):
    product_id: str = Field(..., min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_\-]+$")
    provider: str = Field("planetary", pattern="^(planetary|cdse)$")
    downsample: int = Field(4, ge=1, le=16)
    bbox: Optional[list[float]] = Field(None, description="AOI [minlon,minlat,maxlon,maxlat]; strongly recommended for remote COG reads")


def _fetch_and_calibrate_cdse(product_id: str, factor: int, bbox, progress):
    progress(0, 1, "downloading from CDSE…")
    safe = cdse.download(product_id, CACHE / "s1" / product_id, progress=lambda d, t: progress(d, t))
    progress(0, 1, "calibrating (thermal noise, σ⁰, geocoding)…")
    uid = uuid.uuid4().hex
    out = UPLOADS / f"{uid}.tif"
    s1_calibrate.calibrate_safe(safe, out, factor=factor, bbox=bbox, progress=lambda pol, d, t: progress(d, t, f"calibrating {pol} {d}/{t}"))
    return {"upload_id": uid, "path": str(out), "safe": safe.name, "source": "CDSE"}


def _fetch_and_calibrate_pc(item_id: str, factor: int, bbox, progress):
    progress(0, 1, "resolving STAC item + SAS token (Planetary Computer)…")
    it = planetary.item(item_id)
    tok = planetary.sas_token()
    files = planetary.stage_annotations(it["assets"], CACHE / "pc" / item_id, tok, progress=progress)
    progress(0, 1, "streaming AOI pixels from COG + calibrating…")
    uid = uuid.uuid4().hex
    out = UPLOADS / f"{uid}.tif"
    s1_calibrate.calibrate_product(files, out, factor=factor, bbox=bbox, source_note=" (Planetary Computer COG)",
                                   progress=lambda pol, d, t: progress(d, t, f"calibrating {pol}"))
    return {"upload_id": uid, "path": str(out), "safe": item_id, "source": "Planetary Computer"}


@router.post("/sentinel1/fetch")
def s1_fetch(b: S1Fetch):
    if b.bbox is not None and (len(b.bbox) != 4 or b.bbox[0] >= b.bbox[2] or b.bbox[1] >= b.bbox[3]):
        raise HTTPException(400, "bbox must be [minlon,minlat,maxlon,maxlat]")
    if b.bbox is not None and (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) > 4.0:
        raise HTTPException(400, "AOI larger than 4 deg² – shrink it (or omit for the whole scene, slow)")
    # memory guard: output pixels ≈ (deg/1e-4)² / factor² ; ~30 bytes/pixel peak in the calibrator
    area = (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) if b.bbox else 2.5 * 1.8
    est_mb = area * 1e8 / (b.downsample ** 2) * 30 / 1e6
    try:
        import psutil; avail_mb = psutil.virtual_memory().available / 1e6
    except Exception:
        avail_mb = None
    if avail_mb is not None and est_mb > 0.7 * avail_mb:
        raise HTTPException(400, f"Estimated {est_mb:.0f} MB needed but only {avail_mb:.0f} MB free – increase downsample or shrink the AOI")
    if b.provider == "planetary":
        return jobs.submit("sentinel1_fetch", _fetch_and_calibrate_pc, b.product_id, b.downsample, b.bbox)
    if not os.getenv("OSI_CDSE_USER"):
        raise HTTPException(401, "CDSE download needs OSI_CDSE_USER / OSI_CDSE_PASSWORD (free account) – or use provider=planetary (no account)")
    return jobs.submit("sentinel1_fetch", _fetch_and_calibrate_cdse, b.product_id, b.downsample, b.bbox)


class S1Local(BaseModel):
    safe_path: str; downsample: int = Field(4, ge=1, le=16)


@router.post("/sentinel1/calibrate-local")
def s1_local(b: S1Local):
    """Calibrate a SAFE folder already on disk (e.g. downloaded manually). Restricted to the cache/uploads tree
    or OSI_LOCAL_SAFE_DIR to avoid arbitrary filesystem access."""
    allowed = [CACHE, UPLOADS] + ([Path(os.environ["OSI_LOCAL_SAFE_DIR"])] if os.getenv("OSI_LOCAL_SAFE_DIR") else [])
    p = Path(b.safe_path).resolve()
    if not any(str(p).startswith(str(a.resolve())) for a in allowed) or not p.is_dir():
        raise HTTPException(400, "path not allowed or not a directory")
    def run(progress):
        uid = uuid.uuid4().hex; out = UPLOADS / f"{uid}.tif"
        s1_calibrate.calibrate_safe(p, out, factor=b.downsample, progress=lambda pol, d, t: progress(d, t, f"calibrating {pol}"))
        return {"upload_id": uid, "path": str(out)}
    return jobs.submit("sentinel1_calibrate", run)


# ---------------- Met-ocean ---------------------------------------------------
class ForcingReq(BaseModel):
    investigation_id: Optional[str] = None
    bbox: Optional[list[float]] = None           # [minlon, minlat, maxlon, maxlat]
    t_obs: Optional[str] = None
    hours_back: int = Field(12, ge=1, le=72); hours_fwd: int = Field(6, ge=0, le=72)
    currents: str = Field("hycom", pattern="^(hycom|cmems)$")


@router.post("/forcing/build")
def forcing_build(b: ForcingReq):
    if b.investigation_id:
        try:
            ctx = pipeline.scene_context(b.investigation_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        bbox, t_obs = ctx["bbox"], _iso(ctx["sensing_time"])
    else:
        if not b.bbox or not b.t_obs:
            raise HTTPException(422, "bbox and t_obs required without investigation_id")
        bbox, t_obs = b.bbox, _iso(b.t_obs)
    if b.currents == "cmems" and not os.getenv("OSI_CMEMS_USER"):
        raise HTTPException(401, "CMEMS needs OSI_CMEMS_USER / OSI_CMEMS_PASSWORD (free account); use currents='hycom' otherwise")
    # pad bbox by 1° so the drift cloud never leaves the grid
    pad = [bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1]
    uid = uuid.uuid4().hex
    def run(progress):
        progress(0, 1, f"fetching {b.currents} currents + Open-Meteo wind…")
        res = metocean.build_forcing(pad, t_obs, b.hours_back, b.hours_fwd, UPLOADS / f"{uid}.nc", currents=b.currents)
        return res | {"upload_id": uid}
    return jobs.submit("forcing_build", run)


# ---------------- AIS ----------------------------------------------------------
class AISReq(BaseModel):
    investigation_id: Optional[str] = None
    bbox: Optional[list[float]] = None; t_obs: Optional[str] = None
    hours_back: int = Field(14, ge=1, le=96)
    source: str = Field("marinecadastre", pattern="^(marinecadastre|dma|aisstream)$")
    radius_deg: float = Field(1.0, ge=0.1, le=5)
    live_minutes: float = Field(5, ge=1, le=120)


@router.post("/ais/fetch")
def ais_fetch(b: AISReq):
    if b.investigation_id:
        try:
            ctx = pipeline.scene_context(b.investigation_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        bbox, t_obs = ctx["bbox"], _iso(ctx["sensing_time"])
    else:
        if not b.bbox or not b.t_obs:
            raise HTTPException(422, "bbox and t_obs required without investigation_id")
        bbox, t_obs = b.bbox, _iso(b.t_obs)
    r = b.radius_deg
    box = [bbox[0] - r, bbox[1] - r, bbox[2] + r, bbox[3] + r]
    t0, t1 = t_obs - timedelta(hours=b.hours_back), t_obs + timedelta(hours=1)
    uid = uuid.uuid4().hex; out = UPLOADS / f"{uid}.csv"
    if b.source == "aisstream":
        if not os.getenv("OSI_AISSTREAM_KEY"):
            raise HTTPException(401, "aisstream needs OSI_AISSTREAM_KEY (free)")
        fn = lambda progress: ais_sources.record_aisstream(box, b.live_minutes, out, progress=progress) | {"upload_id": uid}
    elif b.source == "dma":
        fn = lambda progress: ais_sources.dma_subset(box, t0, t1, CACHE / "ais", out, progress=progress) | {"upload_id": uid}
    else:
        fn = lambda progress: ais_sources.marinecadastre_subset(box, t0, t1, CACHE / "ais", out, progress=progress) | {"upload_id": uid}
    return jobs.submit(f"ais_{b.source}", fn)


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if j is None:
        raise HTTPException(404, "job not found")
    return j


@router.get("/jobs")
def job_list():
    return jobs.all_jobs()
