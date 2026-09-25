"""FastAPI application – Oil Spill Intelligence (SIH26143).

    python -m app.main      (from backend/)   or   uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import socket
from datetime import datetime, timezone
import shutil
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, jobs, pipeline
from .api.data_sources import router as data_router
from .integrations import metocean

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("osi")

app = FastAPI(title="Oil Spill Intelligence API", version="0.1.0",
              description="Satellite → segmentation → geometry → hindcast → AIS correlation → explainable ranking")
db.init()
app.include_router(data_router)

MAX_UPLOAD_MB = 600
ALLOWED_SUFFIX = {".tif", ".tiff"}


class InvestigationIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    mode: str = Field("demo", pattern="^(demo|real)$")
    notes: str = ""


class SceneAnalyzeIn(BaseModel):
    investigation_id: str
    scene: str = Field("demo", description="'demo' or an upload id returned by /api/satellite/upload")
    sensing_time: Optional[str] = None
    wind_ms: Optional[float] = Field(None, ge=0, le=60)


class HindcastIn(BaseModel):
    investigation_id: str
    detection_id: str
    forcing: str = Field("demo", description="'demo' or path to a CF NetCDF with uo,vo,u10,v10")
    hours: int = Field(config.DRIFT_MAX_HOURS, ge=1, le=72)
    forward_hours: int = Field(6, ge=0, le=72)


class AISIn(BaseModel):
    investigation_id: str
    ais: str = Field("demo", description="'demo' or an upload id of a CSV")
    radius_km: float = Field(config.AIS_SEARCH_RADIUS_KM, ge=1, le=500)


class RemoteSceneIn(BaseModel):
    url: str = Field(..., min_length=12, max_length=2048)


def _resolve(kind: str, ref: str) -> Path:
    """Map 'demo' or an upload id to a file path. Never accept raw client paths (path traversal)."""
    if ref == "demo":
        p = {"scene": config.DEMO_DIR / "synthetic_s1_scene.tif", "forcing": config.DEMO_DIR / "synthetic_forcing.nc",
             "ais": config.DEMO_DIR / "synthetic_ais.csv"}[kind]
    else:
        if not ref.replace("-", "").isalnum():
            raise HTTPException(400, "invalid upload id")
        matches = list((config.DATA_DIR / "uploads").glob(f"{ref}.*"))
        if not matches:
            raise HTTPException(404, f"upload {ref} not found")
        p = matches[0]
    if not p.exists():
        raise HTTPException(404, f"{kind} file missing: {p.name}")
    return p


async def _save_upload(kind: str, file: UploadFile) -> tuple[str, Path, int, str]:
    """Store one trusted upload and return its id, path, size and digest."""
    suffix = Path(file.filename or "").suffix.lower()
    expected = {"scene": ALLOWED_SUFFIX, "ais": {".csv"}, "forcing": {".nc"}}
    if suffix not in expected.get(kind, set()):
        labels = {"scene": "a GeoTIFF (.tif/.tiff)", "ais": "a CSV", "forcing": "a NetCDF (.nc)"}
        raise HTTPException(400, f"{kind} input must be {labels.get(kind, 'a supported file')}")
    uid = uuid.uuid4().hex
    dest = config.DATA_DIR / "uploads" / f"{uid}{suffix}"
    size = 0
    try:
        with dest.open("wb") as output:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_MB << 20:
                    raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_MB} MB limit")
                output.write(chunk)
        if kind == "scene":
            with dest.open("rb") as source:
                if source.read(4) not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
                    raise HTTPException(400, "the selected file is not a TIFF")
        return uid, dest, size, db.sha256_file(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _scene_time(path: Path, original_name: str) -> str:
    """Read capture time from GeoTIFF metadata, then from a YYYYMMDD filename."""
    import rasterio

    with rasterio.open(path) as scene:
        if scene.crs is None:
            raise ValueError("the GeoTIFF has no CRS; export it with geographic coordinates")
        if scene.transform.is_identity:
            raise ValueError("the GeoTIFF has no georeferencing transform")
        sensing = scene.tags().get("sensing_time") or scene.tags().get("datetime")
    if sensing:
        captured = datetime.fromisoformat(sensing.replace("Z", "+00:00"))
        return (captured if captured.tzinfo else captured.replace(tzinfo=timezone.utc)).isoformat()
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", original_name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
    raise ValueError("capture time is missing; add a 'sensing_time' GeoTIFF tag or YYYYMMDD to the filename")


def _run_image_case(path: Path, original_name: str, sensing_time: str, progress) -> dict:
    """Run every available stage from one SAR image; never invent unavailable AIS."""
    warnings: list[str] = []
    progress(1, 5, "detecting and measuring suspected slicks")
    inv = pipeline.create_investigation(f"Image analysis – {Path(original_name).stem[:70]}", "real", "single-image workflow")
    scene = pipeline.analyze_scene(inv["id"], str(path), sensing_time=sensing_time)
    result = {"investigation": inv, "scene": scene, "detection_id": None, "drift": None, "ais": None,
              "completed_stage": "detection", "warnings": warnings}
    if not scene["detections"]:
        warnings.append("No suspected slick passed the detector threshold; drift and vessel matching were not run.")
        return result
    detection = max(scene["detections"], key=lambda item: item["oil_likelihood"])
    result["detection_id"] = detection["id"]

    is_bundled_scene = db.sha256_file(path) == db.sha256_file(config.DEMO_DIR / "synthetic_s1_scene.tif")
    forcing_path = config.DEMO_DIR / "synthetic_forcing.nc" if is_bundled_scene else config.DATA_DIR / "uploads" / f"{uuid.uuid4().hex}.nc"
    if not is_bundled_scene:
        progress(2, 5, "fetching ocean currents and wind")
        try:
            pad = [scene["bbox"][0] - 1, scene["bbox"][1] - 1, scene["bbox"][2] + 1, scene["bbox"][3] + 1]
            metocean.build_forcing(pad, pipeline._parse_time(sensing_time), config.DRIFT_MAX_HOURS + 3, 9, forcing_path)
        except Exception as exc:
            warnings.append(f"Environmental forcing unavailable: {type(exc).__name__}: {exc}")
            return result

    progress(3, 5, "reconstructing probable origin and forecast")
    drift = pipeline.hindcast(inv["id"], detection["id"], str(forcing_path), config.DRIFT_MAX_HOURS, 6)
    result.update({"drift": drift, "completed_stage": "drift"})

    ais_path = config.DEMO_DIR / "synthetic_ais.csv" if is_bundled_scene else Path(os.getenv("OSI_DEFAULT_AIS_CSV", ""))
    if not is_bundled_scene and (not str(ais_path) or not ais_path.is_file()):
        warnings.append("Historical AIS is not configured for this deployment. Add OSI_DEFAULT_AIS_CSV or an authorized AIS provider to enable vessel ranking.")
        return result
    progress(4, 5, "reconstructing vessel tracks and ranking evidence")
    ais = pipeline.analyze_ais(inv["id"], str(ais_path))
    result.update({"ais": ais, "completed_stage": "complete"})
    progress(5, 5, "investigation complete")
    return result


def _queue_image_case(path: Path, original_name: str, size: int, digest: str, uid: str) -> dict:
    try:
        sensing_time = _scene_time(path, original_name)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"Cannot use this image automatically: {exc}")

    job = jobs.submit("image_investigation", lambda progress: _run_image_case(path, original_name, sensing_time, progress))
    return {"job_id": job["id"], "upload_id": uid, "bytes": size, "sha256": digest,
            "sensing_time": sensing_time}


def _public_https_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Use a public HTTPS GeoTIFF URL")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise HTTPException(400, "GeoTIFF host could not be resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(400, "Private, local and reserved network addresses are not allowed")
    return url


def _download_remote_scene(url: str) -> tuple[str, Path, int, str, str]:
    current = url
    response = None
    try:
        for _ in range(4):
            current = _public_https_url(current)
            response = requests.get(current, stream=True, timeout=(10, 90), allow_redirects=False,
                                    headers={"User-Agent": "OSI-SIH26143/1.0"})
            if response.is_redirect:
                current = urljoin(current, response.headers.get("location", "")); response.close(); continue
            response.raise_for_status(); break
        else:
            raise HTTPException(400, "Too many redirects while downloading GeoTIFF")
    except requests.RequestException as exc:
        raise HTTPException(502, f"GeoTIFF download failed: {exc}") from exc

    suffix = Path(urlparse(current).path).suffix.lower()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if suffix not in ALLOWED_SUFFIX and content_type not in {"image/tiff", "image/geotiff", "application/geotiff", "application/octet-stream"}:
        response.close(); raise HTTPException(415, "URL must point directly to a GeoTIFF (.tif or .tiff)")
    uid = uuid.uuid4().hex; path = config.DATA_DIR / "uploads" / f"{uid}.tif"; size = 0
    try:
        with path.open("wb") as output:
            for chunk in response.iter_content(1 << 20):
                if not chunk: continue
                size += len(chunk)
                if size > MAX_UPLOAD_MB << 20:
                    raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_MB} MB limit")
                output.write(chunk)
        with path.open("rb") as source:
            signature = source.read(4)
        if signature not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            raise HTTPException(415, "Downloaded file is not a TIFF")
        return uid, path, size, db.sha256_file(path), Path(urlparse(current).path).name or f"remote-{uid}.tif"
    except Exception:
        path.unlink(missing_ok=True); raise
    finally:
        response.close()


@app.get("/api/health")
def health():
    return {"status": "ok", "demo_data": (config.DEMO_DIR / "synthetic_s1_scene.tif").exists(),
            "land_mask_available": (config.DEMO_DIR / "ne_10m_land.geojson").exists(),
            "cnn_weights": config.SEG_WEIGHTS.exists(), "drift_engine": config.DRIFT_ENGINE,
            "db": str(config.DB_PATH), "offline": True}


@app.post("/api/investigations")
def create_investigation(body: InvestigationIn):
    return pipeline.create_investigation(body.name, body.mode, body.notes)


@app.get("/api/investigations")
def list_investigations():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM investigations ORDER BY created_at DESC LIMIT 50")]


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str):
    try:
        return pipeline.get_investigation(inv_id)
    except KeyError:
        raise HTTPException(404, "investigation not found")


@app.delete("/api/investigations/{inv_id}")
def delete_investigation(inv_id: str):
    with db.conn() as c:
        if c.execute("SELECT 1 FROM investigations WHERE id=?", (inv_id,)).fetchone() is None:
            raise HTTPException(404, "investigation not found")
        for t in ("vessel_correlations", "vessel_trajectories", "ais_messages", "evidence", "system_logs"):
            c.execute(f"DELETE FROM {t} WHERE investigation_id=?", (inv_id,))
        scenes = [r[0] for r in c.execute("SELECT id FROM satellite_scenes WHERE investigation_id=?", (inv_id,))]
        for sid in scenes:
            dets = [r[0] for r in c.execute("SELECT id FROM spill_detections WHERE scene_id=?", (sid,))]
            for d in dets:
                runs = [r[0] for r in c.execute("SELECT id FROM drift_runs WHERE detection_id=?", (d,))]
                for r in runs: c.execute("DELETE FROM origin_estimates WHERE drift_run_id=?", (r,))
                c.execute("DELETE FROM drift_runs WHERE detection_id=?", (d,)); c.execute("DELETE FROM spill_geometries WHERE detection_id=?", (d,))
            c.execute("DELETE FROM spill_detections WHERE scene_id=?", (sid,)); c.execute("DELETE FROM model_runs WHERE scene_id=?", (sid,))
        c.execute("DELETE FROM satellite_scenes WHERE investigation_id=?", (inv_id,)); c.execute("DELETE FROM investigations WHERE id=?", (inv_id,))
    pipeline._CACHE.pop(inv_id, None)
    return {"deleted": inv_id}


@app.get("/api/uploads")
def list_uploads():
    out = []
    for p in sorted((config.DATA_DIR / "uploads").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        if not p.stem.replace("-", "").isalnum():
            continue
        out.append({"upload_id": p.stem, "kind": {".tif": "scene", ".tiff": "scene", ".nc": "forcing", ".csv": "ais"}.get(p.suffix.lower(), "other"),
                    "bytes": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()})
    return out


@app.post("/api/satellite/upload")
async def upload(kind: str = Form(...), file: UploadFile = File(...)):
    uid, _, size, digest = await _save_upload(kind, file)
    return {"upload_id": uid, "bytes": size, "sha256": digest}


@app.post("/api/auto/run")
async def auto_run(file: UploadFile = File(...)):
    """Queue the simple one-image workflow and return immediately."""
    uid, path, size, digest = await _save_upload("scene", file)
    return _queue_image_case(path, file.filename or path.name, size, digest, uid)


@app.post("/api/auto/url")
def auto_url(body: RemoteSceneIn):
    """Download and queue one public georeferenced SAR GeoTIFF."""
    uid, path, size, digest, name = _download_remote_scene(body.url)
    return _queue_image_case(path, name, size, digest, uid)


@app.post("/api/satellite/analyze")
def analyze(body: SceneAnalyzeIn):
    path = _resolve("scene", body.scene)
    try:
        return pipeline.analyze_scene(body.investigation_id, str(path), body.sensing_time, body.wind_ms)
    except Exception as exc:
        log.exception("scene analysis failed"); raise HTTPException(500, f"scene analysis failed: {exc}")


@app.get("/api/spills/{det_id}")
def get_spill(det_id: str):
    with db.conn() as c:
        r = c.execute("SELECT d.*, g.* FROM spill_detections d JOIN spill_geometries g ON g.detection_id=d.id WHERE d.id=?", (det_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "detection not found")
    d = dict(r); d["geojson"] = json.loads(d["geojson"]); d["lookalike_penalties"] = json.loads(d["lookalike_penalties"])
    return d


@app.post("/api/drift/hindcast")
def drift_hindcast(body: HindcastIn):
    forcing = _resolve("forcing", body.forcing)
    try:
        return pipeline.hindcast(body.investigation_id, body.detection_id, str(forcing), body.hours, body.forward_hours)
    except KeyError:
        raise HTTPException(404, "detection not found")


@app.post("/api/drift/forecast")
def drift_forecast(body: HindcastIn):
    forcing = _resolve("forcing", body.forcing)
    res = pipeline.hindcast(body.investigation_id, body.detection_id, str(forcing), 1, max(body.forward_hours, 1))
    return {"drift_run_id": res["drift_run_id"], "forward": res["forward"], "params": res["params"]}


@app.post("/api/ais/analyze")
def ais_analyze(body: AISIn):
    path = _resolve("ais", body.ais)
    try:
        return pipeline.analyze_ais(body.investigation_id, str(path), body.radius_km)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/vessels/candidates")
def candidates(investigation_id: str):
    with db.conn() as c:
        rows = c.execute("SELECT c.*, v.name, v.imo, v.category FROM vessel_correlations c JOIN vessels v ON v.mmsi=c.mmsi "
                         "WHERE c.investigation_id=? ORDER BY rank", (investigation_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["evidence"] = json.loads(d["evidence"]); d["limitations"] = json.loads(d["limitations"]); out.append(d)
    return {"candidates": out, "disclaimer": "Correlation ≠ proof of responsibility."}


@app.get("/api/vessels/{mmsi}")
def vessel(mmsi: int, investigation_id: str):
    with db.conn() as c:
        v = c.execute("SELECT * FROM vessels WHERE mmsi=?", (mmsi,)).fetchone()
        t = c.execute("SELECT * FROM vessel_trajectories WHERE investigation_id=? AND mmsi=?", (investigation_id, mmsi)).fetchone()
        cor = c.execute("SELECT * FROM vessel_correlations WHERE investigation_id=? AND mmsi=?", (investigation_id, mmsi)).fetchone()
    if v is None:
        raise HTTPException(404, "vessel not found")
    d = {"vessel": dict(v), "trajectory": None if t is None else dict(t) | {"geojson": json.loads(t["geojson"]), "gaps": json.loads(t["gaps"])},
         "correlation": None if cor is None else dict(cor) | {"evidence": json.loads(cor["evidence"]), "limitations": json.loads(cor["limitations"])}}
    return d


@app.get("/api/evidence/{inv_id}")
def evidence(inv_id: str):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id, kind, ref_id, sha256, created_at, payload FROM evidence WHERE investigation_id=? ORDER BY created_at", (inv_id,))]
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return {"investigation_id": inv_id, "items": rows}


@app.get("/api/demo/scenario")
def scenario():
    p = config.DEMO_DIR / "scenario.json"
    return json.loads(p.read_text()) if p.exists() else {"synthetic": True}


@app.get("/api/demo/sample-scene")
def download_sample_scene():
    """Download the same synthetic GeoTIFF used by the verified demo."""
    path = config.DEMO_DIR / "synthetic_s1_scene.tif"
    if not path.exists():
        raise HTTPException(404, "sample scene is not installed")
    return FileResponse(
        path,
        media_type="image/tiff",
        filename="OSI_sample_20250314.tif",
        headers={"X-OSI-Data-Type": "synthetic"},
    )


@app.get("/api/evidence/{inv_id}/export")
def export_evidence(inv_id: str):
    with db.conn() as c:
        inv = c.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
    if inv is None:
        raise HTTPException(404, "investigation not found")
    return JSONResponse({"investigation": dict(inv), "exported_at": db.now(),
                         "disclaimer": "Research prototype. Scores are uncalibrated evidence matches, not proof of responsibility. Evidence may include previous runs.",
                         **evidence(inv_id)},
                        headers={"Content-Disposition": f'attachment; filename="{inv_id}-evidence.json"'})


@app.get("/api/demo/coastline")
def coastline():
    return FileResponse(config.DEMO_DIR / "ne_110m_coastline.geojson", media_type="application/geo+json")


@app.post("/api/demo/run")
def run_demo():
    """One-click full evidence chain on the bundled synthetic scenario."""
    inv = pipeline.create_investigation("Demo – Arabian Sea synthetic discharge", "demo", "bundled synthetic scenario")
    from .drift.engine import Forcing
    forcing = Forcing.from_netcdf(str(config.DEMO_DIR / "synthetic_forcing.nc"))
    wind = forcing.wind_at(pipeline._parse_time("2025-03-14T01:12:00+00:00"), 70.58, 19.43)
    scene = pipeline.analyze_scene(inv["id"], str(config.DEMO_DIR / "synthetic_s1_scene.tif"), wind_ms=round(wind, 1))
    if not scene["detections"]:
        raise HTTPException(500, "no detection in demo scene")
    det = max(scene["detections"], key=lambda d: d["oil_likelihood"])
    drift = pipeline.hindcast(inv["id"], det["id"], str(config.DEMO_DIR / "synthetic_forcing.nc"), config.DRIFT_MAX_HOURS, 6)
    # re-run look-alike assessment with modelled wind now that forcing is known
    ais = pipeline.analyze_ais(inv["id"], str(config.DEMO_DIR / "synthetic_ais.csv"))
    return {"investigation": inv, "scene": scene, "detection_id": det["id"], "drift": drift, "ais": ais}


app.mount("/renders", StaticFiles(directory=config.DATA_DIR / "renders"), name="renders")
app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
