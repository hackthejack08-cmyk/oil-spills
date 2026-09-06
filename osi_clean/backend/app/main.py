"""FastAPI application – Oil Spill Intelligence (SIH26143).

    python -m app.main      (from backend/)   or   uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, pipeline
from .api.data_sources import router as data_router

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


@app.get("/api/health")
def health():
    return {"status": "ok", "demo_data": (config.DEMO_DIR / "synthetic_s1_scene.tif").exists(),
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
    suffix = Path(file.filename or "").suffix.lower()
    if kind == "scene" and suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, "only GeoTIFF scenes are accepted")
    if kind == "ais" and suffix != ".csv":
        raise HTTPException(400, "AIS must be CSV")
    if kind == "forcing" and suffix != ".nc":
        raise HTTPException(400, "forcing must be NetCDF")
    uid = uuid.uuid4().hex
    dest = config.DATA_DIR / "uploads" / f"{uid}{suffix}"
    size = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB << 20:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large")
            f.write(chunk)
    # magic-byte check for GeoTIFF
    if kind == "scene":
        with dest.open("rb") as f:
            magic = f.read(4)
        if magic not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            dest.unlink(); raise HTTPException(400, "not a TIFF file")
    return {"upload_id": uid, "bytes": size, "sha256": db.sha256_file(dest)}


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
