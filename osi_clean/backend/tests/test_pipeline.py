import json, numpy as np, pytest
from fastapi.testclient import TestClient
from app import config, db
from app.main import app
from app.geometry import slick as geom
from app.ais import pipeline as aisp
from app.sar import lookalike
from rasterio.transform import from_origin

client = TestClient(app)

def test_health():
    r = client.get("/api/health"); assert r.status_code == 200 and r.json()["demo_data"]

def test_geodesic_area_of_known_square():
    # 100x100 px of 0.001° at equator ≈ 0.1°x0.1° ≈ 123.6 km²  (geodesic)
    mask = np.zeros((200, 200), bool); mask[50:150, 50:150] = True
    prob = mask.astype(np.float32); db_vv = np.where(mask, -25.0, -18.0).astype(np.float32)
    tr = from_origin(0.0, 0.1, 0.001, 0.001)
    s = geom.extract(mask, prob, db_vv, tr, "EPSG:4326")
    assert len(s) == 1 and abs(s[0].area_km2 - 123.6) / 123.6 < 0.02
    assert 0.9 < s[0].elongation < 1.15

def test_lookalike_rules():
    a = lookalike.assess(0.8, mean_contrast_db=1.0, elongation=1.1, area_km2=50, touches_land=False, wind_ms=1.5)
    assert a.label == "probable_lookalike" and {p["rule"] for p in a.penalties} >= {"R1_low_wind", "R2_blob_shape", "R3_weak_contrast"}
    b = lookalike.assess(0.8, mean_contrast_db=4.0, elongation=6.0, area_km2=5, touches_land=False, wind_ms=6)
    assert b.label == "probable_oil" and not b.penalties

def test_ais_cleaning_drops_junk():
    raw = aisp.load_csv(str(config.DEMO_DIR / "synthetic_ais.csv"))
    clean, rep = aisp.clean(raw)
    assert rep.dropped.get("invalid_mmsi") == 1 and rep.dropped.get("duplicate") == 1
    tracks = aisp.build_tracks(clean)
    alpha = next(t for t in tracks if t.mmsi == 419000101)
    assert alpha.category == "tanker" and any(g["minutes"] > 30 for g in alpha.gaps)

def test_path_traversal_rejected():
    r = client.post("/api/ais/analyze", json={"investigation_id": "x", "ais": "../../etc/passwd"}); assert r.status_code == 400

def test_full_demo_chain_ranks_culprit_top1():
    r = client.post("/api/demo/run"); assert r.status_code == 200, r.text
    j = r.json(); truth = json.load(open(config.DEMO_DIR / "scenario.json"))
    assert j["ais"]["candidates"][0]["mmsi"] == truth["culprit_mmsi"]
    best = next(d for d in j["scene"]["detections"] if d["id"] == j["detection_id"])
    assert best["label"] == "probable_oil"
    # the 4 h hypothesis centre should be within ~5 km of the true origin (synthetic forcing is smooth)
    h = j["drift"]["backward"]["hypotheses"][3]
    d_km = aisp.haversine_km(h["centre"][0], h["centre"][1], truth["origin_lon"], truth["origin_lat"])
    assert d_km < 6, d_km
    ev = client.get(f"/api/evidence/{j['investigation']['id']}").json(); assert ev["items"] and all(len(i["sha256"]) == 64 for i in ev["items"])
