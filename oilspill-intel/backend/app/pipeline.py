"""Investigation orchestrator – ties the modules together and persists results.
Each stage is independently callable from the API; `run_full` executes the
whole evidence chain for the offline demo."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from PIL import Image

from . import config, db
from .sar import preprocess, segment, lookalike
from .geometry import slick as geom
from .drift import engine as drift
from .ais import pipeline as aisp, correlate

_CACHE: dict = {}          # investigation_id -> in-memory objects (forcing, tracks, hypotheses)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
def create_investigation(name: str, mode: str = "demo", notes: str = "") -> dict:
    inv_id = _uid("inv")
    with db.conn() as c:
        c.execute("INSERT INTO investigations VALUES (?,?,?,?,?,?)", (inv_id, name, db.now(), "created", mode, notes))
    db.log("INFO", f"investigation created ({mode})", inv_id)
    return {"id": inv_id, "name": name, "mode": mode}


def analyze_scene(inv_id: str, scene_path: str, sensing_time: Optional[str] = None,
                  wind_ms: Optional[float] = None) -> dict:
    t0 = time.time()
    scene = preprocess.read_scene(scene_path)
    sensing = sensing_time or scene.meta.get("sensing_time")
    if scene.meta.get("synthetic") != "true":               # synthetic demo scenes are placed in open water
        scene.land = preprocess.land_mask(scene.shape, scene.transform, scene.crs)
    shore = preprocess.shore_distance_km(scene.shape, scene.transform, scene.crs) if scene.land is not None else None
    prob, mask, detector = segment.segment(scene)
    land = ~np.isfinite(scene.db[0])
    if scene.land is not None:
        land |= scene.land
    slicks = geom.extract(mask, prob, scene.db[0], scene.transform, scene.crs, land_mask=land, shore_km=shore)
    scene_id, run_id = _uid("scn"), _uid("run")
    left, bottom, right, top = rasterio.transform.array_bounds(*scene.shape, scene.transform)
    minlon, minlat, maxlon, maxlat = transform_bounds(scene.crs, "EPSG:4326", left, bottom, right, top, densify_pts=21)
    # quicklook PNG for the UI (VV dB stretched) + probability overlay
    ql = preprocess.normalise(scene.db[:1])[0]
    step = max(1, scene.shape[0] // 1024)
    Image.fromarray((ql[::step, ::step] * 255).astype(np.uint8)).save(config.DATA_DIR / "renders" / f"{scene_id}_vv.png")
    rgba = np.zeros((*prob[::step, ::step].shape, 4), np.uint8)
    p = prob[::step, ::step]; rgba[..., 0] = 255; rgba[..., 1] = (80 * (1 - p)).astype(np.uint8); rgba[..., 3] = (p * 200).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(config.DATA_DIR / "renders" / f"{scene_id}_prob.png")

    detections = []
    with db.conn() as c:
        c.execute("INSERT INTO satellite_scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (scene_id, inv_id, str(scene_path), db.sha256_file(scene_path), scene.meta.get("platform", "Sentinel-1"),
                   sensing, scene.meta.get("polarisation", "VV,VH"), str(scene.crs), scene.shape[1], scene.shape[0],
                   minlon, minlat, maxlon, maxlat, int(scene.meta.get("synthetic", "false") == "true"), json.dumps(scene.meta)))
        c.execute("INSERT INTO model_runs VALUES (?,?,?,?,?,?,?,?)",
                  (run_id, scene_id, detector, db.sha256_file(config.SEG_WEIGHTS) if config.SEG_WEIGHTS.exists() else None,
                   config.SEG_THRESHOLD, db.now(), round(time.time() - t0, 2), json.dumps({"tile": config.TILE, "encoder": config.SEG_ENCODER})))
        for rank, s in enumerate(slicks[:10], 1):
            # segmentation confidence: mean prob, capped for the classical baseline
            seg_conf = s.mean_prob if detector.startswith("unet") else min(s.mean_prob, 0.75)
            near_ship = False
            la = lookalike.assess(seg_conf, mean_contrast_db=s.mean_contrast_db, elongation=s.elongation,
                                  area_km2=s.area_km2, touches_land=s.touches_land, wind_ms=wind_ms, near_ship=near_ship, shore_km=s.shore_km)
            det_id = _uid("det")
            c.execute("INSERT INTO spill_detections VALUES (?,?,?,?,?,?,?,?)",
                      (det_id, scene_id, run_id, rank, seg_conf, la.oil_likelihood, la.label, json.dumps(la.penalties)))
            c.execute("INSERT INTO spill_geometries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (det_id, json.dumps(s.polygon_geojson), s.centroid_lon, s.centroid_lat, s.area_km2, s.perimeter_km,
                       s.length_km, s.width_km, s.orientation_deg, s.elongation, s.compactness, s.pixel_count, *s.bbox))
            d = {"id": det_id, "rank": rank, "seg_confidence": round(seg_conf, 3), "oil_likelihood": la.oil_likelihood,
                 "label": la.label, "lookalike_penalties": la.penalties, "geometry": s.dict()}
            db.add_evidence(c, inv_id, "detection", det_id, d, _uid("ev"))
            detections.append(d)
        c.execute("UPDATE investigations SET status='scene_analyzed' WHERE id=?", (inv_id,))
    out = {"scene_id": scene_id, "model_run_id": run_id, "detector": detector, "sensing_time": sensing,
           "synthetic": scene.meta.get("synthetic", "false") == "true",
           "bbox": [minlon, minlat, maxlon, maxlat], "quicklook": f"/renders/{scene_id}_vv.png",
           "prob_overlay": f"/renders/{scene_id}_prob.png", "detections": detections,
           "processing_s": round(time.time() - t0, 2),
           "age_estimate": {"status": "unavailable", "reason": "Spill age is not observable from a single SAR scene; "
                            "hindcast is run over a 1–%d h age window instead." % config.DRIFT_MAX_HOURS}}
    # ponytail: response snapshots fit laptop cases; use object storage for large multi-user runs.
    with db.conn() as c:
        db.add_evidence(c, inv_id, "scene_result", scene_id, out, _uid("ev"))
    _CACHE[inv_id] = {"scene": out}
    return out


def hindcast(inv_id: str, detection_id: str, forcing_path: str, hours: int = config.DRIFT_MAX_HOURS,
             forward_hours: int = 0) -> dict:
    with db.conn() as c:
        g = c.execute("SELECT g.*, s.sensing_time FROM spill_geometries g JOIN spill_detections d ON d.id=g.detection_id "
                      "JOIN satellite_scenes s ON s.id=d.scene_id WHERE g.detection_id=?", (detection_id,)).fetchone()
    if g is None:
        raise KeyError("detection not found")
    t_obs = _parse_time(g["sensing_time"])
    forcing = drift.Forcing.from_netcdf(forcing_path)
    # seed particles uniformly inside the slick polygon
    from shapely.geometry import shape, Point
    poly = shape(json.loads(g["geojson"]))
    rng = np.random.default_rng(1)
    minx, miny, maxx, maxy = poly.bounds
    pts = []
    while len(pts) < 300:
        x, y = rng.uniform(minx, maxx, 500), rng.uniform(miny, maxy, 500)
        pts += [(a, b) for a, b in zip(x, y) if poly.contains(Point(a, b))]
    pts = np.asarray(pts[:300])
    back = drift.simulate(forcing, pts[:, 0], pts[:, 1], t_obs, hours, backward=True)
    fwd = drift.simulate(forcing, pts[:, 0], pts[:, 1], t_obs, forward_hours, backward=False) if forward_hours else None
    wind = forcing.wind_at(t_obs, g["centroid_lon"], g["centroid_lat"])
    run_id = _uid("drf")
    result = {"drift_run_id": run_id, "detection_id": detection_id, "t_obs": t_obs.isoformat(), "backward": {"hypotheses": back.hypotheses, "centre_track": back.centre_track},
              "forward": None if fwd is None else {"hypotheses": fwd.hypotheses, "centre_track": fwd.centre_track},
              "params": back.params, "forcing_source": forcing.source, "wind_at_slick_ms": round(wind, 2),
              "origin_window": {"earliest": (t_obs - timedelta(hours=hours)).isoformat(), "latest": (t_obs - timedelta(hours=1)).isoformat(),
                                "note": "Release time is unobservable from one scene; every hour in this window is a hypothesis with its own uncertainty ellipse."}}
    with db.conn() as c:
        c.execute("INSERT INTO drift_runs VALUES (?,?,?,?,?,?,?,?)", (run_id, detection_id, "backward+forward" if fwd else "backward",
                  back.params["engine"], json.dumps(forcing.source), json.dumps(back.params), db.now(), json.dumps({"n_hyp": len(back.hypotheses)})))
        for h in back.hypotheses:
            c.execute("INSERT INTO origin_estimates VALUES (?,?,?,?,?,?,?,?,?)", (_uid("org"), run_id, h["age_hours"], h["time"],
                      h["centre"][0], h["centre"][1], json.dumps(h["ellipse50"]), json.dumps(h["ellipse90"]), h["spread_km"]))
        db.add_evidence(c, inv_id, "drift", run_id, {k: v for k, v in result.items() if k != "backward"} | {"n_hypotheses": len(back.hypotheses)}, _uid("ev"))
        db.add_evidence(c, inv_id, "drift_result", run_id, result, _uid("ev"))
        c.execute("UPDATE investigations SET status='hindcast_done' WHERE id=?", (inv_id,))
    _CACHE.setdefault(inv_id, {}).pop("ais", None)
    _CACHE[inv_id].update({"drift_run_id": run_id, "hypotheses": back.hypotheses, "t_obs": t_obs, "wind": wind,
                                          "slick": (g["centroid_lon"], g["centroid_lat"], g["orientation_deg"]), "detection_id": detection_id})
    return result


def _restore_ctx(inv_id: str) -> dict:
    """Rebuild in-memory context from the DB (server restarts, multiple workers)."""
    ctx = _CACHE.setdefault(inv_id, {})
    if "hypotheses" in ctx:
        return ctx
    with db.conn() as c:
        row = c.execute("SELECT dr.id, dr.detection_id, g.centroid_lon, g.centroid_lat, g.orientation_deg, s.sensing_time "
                        "FROM drift_runs dr JOIN spill_detections d ON d.id=dr.detection_id JOIN spill_geometries g ON g.detection_id=d.id "
                        "JOIN satellite_scenes s ON s.id=d.scene_id WHERE s.investigation_id=? "
                        "AND s.id=(SELECT id FROM satellite_scenes WHERE investigation_id=? ORDER BY rowid DESC LIMIT 1) "
                        "ORDER BY dr.rowid DESC LIMIT 1", (inv_id, inv_id)).fetchone()
        if row is None:
            return ctx
        hyps = [dict(age_hours=r["age_hours"], time=r["time_utc"], centre=[r["lon"], r["lat"]], ellipse50=json.loads(r["ellipse50"]),
                     ellipse90=json.loads(r["ellipse90"]), spread_km=r["spread_km"], particles=json.loads(r["ellipse50"])["coordinates"][0])
                for r in c.execute("SELECT * FROM origin_estimates WHERE drift_run_id=? ORDER BY age_hours", (row["id"],))]
    ctx.update({"drift_run_id": row["id"], "hypotheses": hyps, "t_obs": _parse_time(row["sensing_time"]), "detection_id": row["detection_id"],
                "slick": (row["centroid_lon"], row["centroid_lat"], row["orientation_deg"])})
    return ctx


def analyze_ais(inv_id: str, ais_path: str, radius_km: float = config.AIS_SEARCH_RADIUS_KM) -> dict:
    ctx = _restore_ctx(inv_id)
    if "hypotheses" not in ctx:
        raise RuntimeError("Run hindcast before AIS analysis")
    raw = aisp.load_csv(ais_path)
    clean, rep = aisp.clean(raw)
    tracks = aisp.build_tracks(clean)
    t_obs = ctx["t_obs"]; hyps = ctx["hypotheses"]
    hours = max(h["age_hours"] for h in hyps)
    c_lon = float(np.mean([h["centre"][0] for h in hyps])); c_lat = float(np.mean([h["centre"][1] for h in hyps]))
    window = (t_obs - timedelta(hours=hours + 1), t_obs)
    n_all = len(tracks)
    kept = aisp.spatial_temporal_filter(tracks, c_lon, c_lat, radius_km, *window)
    cands = correlate.rank(kept, t_obs, hyps, ctx["slick"][:2], ctx["slick"][2])
    synthetic = int("SYNTHETIC" in raw.columns and len(raw) and bool(raw["SYNTHETIC"].iloc[0]))
    with db.conn() as c:
        c.execute("DELETE FROM vessel_correlations WHERE investigation_id=?", (inv_id,))
        c.execute("DELETE FROM ais_messages WHERE investigation_id=?", (inv_id,))
        c.executemany("INSERT INTO ais_messages(investigation_id,mmsi,time_utc,lon,lat,sog,cog,heading,source,synthetic) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      [(inv_id, int(r.MMSI), r.TIME.isoformat(), float(r.LON), float(r.LAT), None if pd.isna(r.SOG) else float(r.SOG),
                        None if pd.isna(r.COG) else float(r.COG), None if ("HEADING" not in clean.columns or pd.isna(r.HEADING)) else float(r.HEADING),
                        Path(ais_path).name, synthetic) for r in clean.itertuples()])
        for tr in tracks:
            c.execute("INSERT OR REPLACE INTO vessels VALUES (?,?,?,?,?,?,?)", (tr.mmsi, tr.name, tr.imo, tr.vtype, tr.category, None, None))
            c.execute("INSERT OR REPLACE INTO vessel_trajectories VALUES (?,?,?,?,?,?,?,?)",
                      (f"trj_{inv_id}_{tr.mmsi}", inv_id, tr.mmsi, json.dumps({"type": "LineString", "coordinates": tr.df[["LON", "LAT"]].values.round(5).tolist()}),
                       len(tr.df), json.dumps(tr.gaps), tr.df.TIME.iloc[0].isoformat(), tr.df.TIME.iloc[-1].isoformat()))
        for i, cd in enumerate(cands, 1):
            cid = _uid("cor")
            c.execute("INSERT INTO vessel_correlations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, inv_id, ctx["detection_id"], cd.mmsi, i, cd.correlation, cd.best_age_hours, cd.min_distance_km, cd.time_offset_min,
                       json.dumps([e.__dict__ for e in cd.evidence]), json.dumps(cd.limitations), "rule-based-v1"))
            db.add_evidence(c, inv_id, "correlation", cid, cd.dict() | {"rank": i}, _uid("ev"))
        c.execute("UPDATE investigations SET status='ranked' WHERE id=?", (inv_id,))
    kept_ids = {id(t) for t in kept}
    def _pack(tr):
        step = 1 if id(tr) in kept_ids or len(tr.df) <= 400 else int(np.ceil(len(tr.df) / 400))
        g = tr.df.iloc[::step]
        return {"mmsi": tr.mmsi, "name": tr.name, "category": tr.category, "n": len(tr.df), "gaps": tr.gaps[:50],
                "track": {"type": "LineString", "coordinates": g[["LON", "LAT"]].values.round(5).tolist()},
                "times": [t.isoformat() for t in g.TIME], "sog": g.SOG.fillna(-1).round(1).tolist(),
                "cog": g.COG.fillna(-1).round(0).tolist(), "candidate": id(tr) in kept_ids}
    all_tracks = [_pack(tr) for tr in tracks]
    out = {"cleaning": rep.__dict__, "n_vessels_total": n_all, "n_candidates": len(kept), "search_radius_km": radius_km,
           "window": [window[0].isoformat(), window[1].isoformat()], "synthetic": bool(synthetic),
           "candidates": [c.dict() | {"rank": i} for i, c in enumerate(cands, 1)], "tracks": all_tracks,
           "disclaimer": "Correlation scores express consistency with the available evidence. They are NOT proof of responsibility."}
    out.update({"drift_run_id": ctx.get("drift_run_id"), "detection_id": ctx["detection_id"]})
    with db.conn() as c:
        db.add_evidence(c, inv_id, "ais_result", ctx["detection_id"], out, _uid("ev"))
    ctx["ais"] = out
    return out


def get_investigation(inv_id: str) -> dict:
    with db.conn() as c:
        inv = c.execute("SELECT * FROM investigations WHERE id=?", (inv_id,)).fetchone()
        if inv is None:
            raise KeyError(inv_id)
        dets = [dict(r) for r in c.execute("SELECT d.*, g.centroid_lon, g.centroid_lat, g.area_km2 FROM spill_detections d "
                                            "JOIN spill_geometries g ON g.detection_id=d.id JOIN satellite_scenes s ON s.id=d.scene_id "
                                            "WHERE s.investigation_id=? ORDER BY d.rank", (inv_id,))]
        cors = [dict(r) for r in c.execute("SELECT c.*, v.name, v.category FROM vessel_correlations c JOIN vessels v ON v.mmsi=c.mmsi "
                                            "WHERE c.investigation_id=? ORDER BY rank LIMIT 5", (inv_id,))]
        ev = c.execute("SELECT COUNT(*) FROM evidence WHERE investigation_id=?", (inv_id,)).fetchone()[0]
    ctx = _CACHE.get(inv_id, {})
    with db.conn() as c:
        scenes = [{"id": r["id"], "sensing_time": r["sensing_time"], "bbox": [r["minlon"], r["minlat"], r["maxlon"], r["maxlat"]]}
                  for r in c.execute("SELECT * FROM satellite_scenes WHERE investigation_id=? ORDER BY rowid DESC", (inv_id,))]
    with db.conn() as c:
        def latest(kind):
            row = c.execute("SELECT payload FROM evidence WHERE investigation_id=? AND kind=? ORDER BY rowid DESC LIMIT 1",
                            (inv_id, kind)).fetchone()
            return json.loads(row[0]) if row else None
        scene = latest("scene_result") or ctx.get("scene")
        # Legacy cases have detection evidence but no saved scene response.
        if scene is None and scenes:
            sc = scenes[0]
            ids = {d["id"] for d in dets if d["scene_id"] == sc["id"]}
            detections = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM evidence WHERE investigation_id=? AND kind='detection' ORDER BY rowid", (inv_id,))
                if json.loads(r[0])["id"] in ids]
            model = c.execute("SELECT detector, duration_s FROM model_runs WHERE scene_id=? ORDER BY rowid DESC LIMIT 1", (sc["id"],)).fetchone()
            scene = {"scene_id": sc["id"], "sensing_time": sc["sensing_time"], "bbox": sc["bbox"],
                     "quicklook": f"/renders/{sc['id']}_vv.png", "prob_overlay": f"/renders/{sc['id']}_prob.png",
                     "detections": detections, "detector": model[0] if model else "unknown", "processing_s": model[1] if model else None,
                     "age_estimate": {"status": "unavailable", "reason": "Age is not observable from one SAR image."}}
        dr = latest("drift_result")
        if dr and (not scene or dr["detection_id"] not in {d["id"] for d in scene["detections"]}):
            dr = None
        ais = latest("ais_result")
        if ais and (not dr or ais.get("drift_run_id") != dr["drift_run_id"]):
            ais = None
    return {"investigation": dict(inv), "scenes": scenes, "detections": dets, "top_candidates": cors, "n_evidence": ev,
            "scene": scene, "drift": dr, "ais": ais, "has_drift": dr is not None, "has_ais": ais is not None}


def scene_context(inv_id: str) -> dict:
    """bbox, sensing time and best detection for an investigation (used by data connectors)."""
    with db.conn() as c:
        sc = c.execute("SELECT * FROM satellite_scenes WHERE investigation_id=? ORDER BY rowid DESC LIMIT 1", (inv_id,)).fetchone()
        if sc is None:
            raise KeyError("no scene analysed for this investigation")
        det = c.execute("SELECT d.id, d.oil_likelihood, g.centroid_lon, g.centroid_lat FROM spill_detections d JOIN spill_geometries g ON g.detection_id=d.id "
                        "WHERE d.scene_id=? ORDER BY d.oil_likelihood DESC LIMIT 1", (sc["id"],)).fetchone()
    return {"scene_id": sc["id"], "bbox": [sc["minlon"], sc["minlat"], sc["maxlon"], sc["maxlat"]], "sensing_time": sc["sensing_time"],
            "detection": None if det is None else dict(det)}
