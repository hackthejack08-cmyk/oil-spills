"""SQLite persistence (MVP). Schema mirrors report §19. Geometries stored as
GeoJSON text + bbox columns with indexes (poor-man's spatial index that is
adequate for 10^5 rows; PostGIS is the documented upgrade path).
Evidence rows carry a SHA-256 over their payload for tamper-evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY, name TEXT, created_at TEXT, status TEXT, mode TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS satellite_scenes (
  id TEXT PRIMARY KEY, investigation_id TEXT REFERENCES investigations(id), path TEXT, sha256 TEXT,
  platform TEXT, sensing_time TEXT, polarisation TEXT, crs TEXT, width INTEGER, height INTEGER,
  minlon REAL, minlat REAL, maxlon REAL, maxlat REAL, synthetic INTEGER, meta TEXT);
CREATE TABLE IF NOT EXISTS model_runs (
  id TEXT PRIMARY KEY, scene_id TEXT REFERENCES satellite_scenes(id), detector TEXT, weights_sha256 TEXT,
  threshold REAL, started_at TEXT, duration_s REAL, params TEXT);
CREATE TABLE IF NOT EXISTS spill_detections (
  id TEXT PRIMARY KEY, scene_id TEXT REFERENCES satellite_scenes(id), model_run_id TEXT REFERENCES model_runs(id),
  rank INTEGER, seg_confidence REAL, oil_likelihood REAL, label TEXT, lookalike_penalties TEXT);
CREATE TABLE IF NOT EXISTS spill_geometries (
  detection_id TEXT PRIMARY KEY REFERENCES spill_detections(id), geojson TEXT, centroid_lon REAL, centroid_lat REAL,
  area_km2 REAL, perimeter_km REAL, length_km REAL, width_km REAL, orientation_deg REAL, elongation REAL,
  compactness REAL, pixel_count INTEGER, minlon REAL, minlat REAL, maxlon REAL, maxlat REAL);
CREATE INDEX IF NOT EXISTS ix_geom_bbox ON spill_geometries(minlon, maxlon, minlat, maxlat);
CREATE TABLE IF NOT EXISTS drift_runs (
  id TEXT PRIMARY KEY, detection_id TEXT REFERENCES spill_detections(id), direction TEXT, engine TEXT,
  forcing_source TEXT, params TEXT, created_at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS origin_estimates (
  id TEXT PRIMARY KEY, drift_run_id TEXT REFERENCES drift_runs(id), age_hours REAL, time_utc TEXT,
  lon REAL, lat REAL, ellipse50 TEXT, ellipse90 TEXT, spread_km REAL);
CREATE INDEX IF NOT EXISTS ix_origin_time ON origin_estimates(time_utc);
CREATE TABLE IF NOT EXISTS vessels (
  mmsi INTEGER PRIMARY KEY, name TEXT, imo TEXT, vessel_type INTEGER, category TEXT, length REAL, width REAL);
CREATE TABLE IF NOT EXISTS ais_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, investigation_id TEXT, mmsi INTEGER, time_utc TEXT, lon REAL, lat REAL,
  sog REAL, cog REAL, heading REAL, source TEXT, synthetic INTEGER);
CREATE INDEX IF NOT EXISTS ix_ais_mmsi_time ON ais_messages(mmsi, time_utc);
CREATE INDEX IF NOT EXISTS ix_ais_bbox ON ais_messages(lon, lat);
CREATE TABLE IF NOT EXISTS vessel_trajectories (
  id TEXT PRIMARY KEY, investigation_id TEXT, mmsi INTEGER, geojson TEXT, n_fixes INTEGER, gaps TEXT,
  t_start TEXT, t_end TEXT);
CREATE TABLE IF NOT EXISTS vessel_correlations (
  id TEXT PRIMARY KEY, investigation_id TEXT, detection_id TEXT, mmsi INTEGER, rank INTEGER, correlation REAL,
  best_age_hours REAL, min_distance_km REAL, time_offset_min REAL, evidence TEXT, limitations TEXT, method TEXT);
CREATE INDEX IF NOT EXISTS ix_corr_inv ON vessel_correlations(investigation_id, rank);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, investigation_id TEXT, kind TEXT, ref_id TEXT, payload TEXT, sha256 TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS monitor_products (
  monitor_id TEXT, product_id TEXT, investigation_id TEXT REFERENCES investigations(id), sensing_time TEXT,
  status TEXT, bbox TEXT, result TEXT, error TEXT, created_at TEXT,
  PRIMARY KEY (monitor_id, product_id));
CREATE INDEX IF NOT EXISTS ix_monitor_time ON monitor_products(monitor_id, sensing_time);
CREATE TABLE IF NOT EXISTS system_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, investigation_id TEXT, message TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def log(level: str, msg: str, investigation_id: str | None = None):
    with conn() as c:
        c.execute("INSERT INTO system_logs(ts, level, investigation_id, message) VALUES (?,?,?,?)",
                  (now(), level, investigation_id, msg))


def add_evidence(c, inv_id: str, kind: str, ref_id: str, payload: dict, ev_id: str):
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    c.execute("INSERT INTO evidence(id, investigation_id, kind, ref_id, payload, sha256, created_at) VALUES (?,?,?,?,?,?,?)",
              (ev_id, inv_id, kind, ref_id, raw.decode(), sha256_bytes(raw), now()))
