import json, numpy as np, pytest, rasterio
from fastapi.testclient import TestClient
from app import config, db
from app.main import app, _scene_time
from app.geometry import slick as geom
from app.ais import pipeline as aisp
from app.sar import lookalike
from rasterio.transform import from_origin

client = TestClient(app)

def test_health():
    r = client.get("/api/health"); assert r.status_code == 200 and r.json()["demo_data"]

def test_sample_scene_download():
    r = client.get("/api/demo/sample-scene")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/tiff")
    assert "OSI_sample_20250314.tif" in r.headers["content-disposition"]
    assert r.headers["x-osi-data-type"] == "synthetic"
    assert r.content[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")

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


def test_monitoring_keeps_multiple_slicks_and_links_repeat_passes():
    from app.monitoring import cluster_events
    base = {"area_km2": 2.0, "score": 0.7, "label": "probable_oil"}
    events = [
        base | {"detection_id": "a", "product_id": "scene-1", "sensing_time": "2026-01-01T00:00:00Z", "lat": 19.0, "lon": 72.0},
        base | {"detection_id": "b", "product_id": "scene-1", "sensing_time": "2026-01-01T00:00:00Z", "lat": 19.01, "lon": 72.01},
        base | {"detection_id": "c", "product_id": "scene-2", "sensing_time": "2026-01-02T00:00:00Z", "lat": 19.005, "lon": 72.005},
    ]
    incidents = cluster_events(events)
    assert len(incidents) == 2
    assert sorted(len(incident["events"]) for incident in incidents) == [1, 2]


def test_monitor_scan_queues_an_idempotent_cycle(monkeypatch):
    from app import jobs
    queued = {}
    monkeypatch.setattr(jobs, "submit", lambda kind, fn, body: queued.update(kind=kind, body=body) or {"id": "job_monitor"})
    payload = {"monitor_id": "mumbai-coast", "bbox": [72.2, 18.6, 73.0, 19.5],
               "start": "2025-03-01T00:00:00Z", "end": "2025-03-31T23:59:59Z"}
    response = client.post("/api/data/monitor/scan", json=payload)
    assert response.status_code == 200 and response.json()["id"] == "job_monitor"
    assert queued["kind"] == "sentinel1_monitor" and queued["body"].monitor_id == "mumbai-coast"


def test_live_scene_snapshot_endpoint(monkeypatch):
    from app.integrations import planetary
    expected = {"area": "Mumbai coast", "radar": [{"id": "S1-test"}], "optical": [{"id": "S2-test"}]}
    monkeypatch.setattr(planetary, "live_snapshot", lambda: expected)
    response = client.get("/api/data/live-scenes")
    assert response.status_code == 200 and response.json() == expected

def test_ais_cleaning_drops_junk():
    raw = aisp.load_csv(str(config.DEMO_DIR / "synthetic_ais.csv"))
    clean, rep = aisp.clean(raw)
    assert rep.dropped.get("invalid_mmsi") == 1 and rep.dropped.get("duplicate") == 1
    tracks = aisp.build_tracks(clean)
    alpha = next(t for t in tracks if t.mmsi == 419000101)
    assert alpha.category == "tanker" and any(g["minutes"] > 30 for g in alpha.gaps)

def test_path_traversal_rejected():
    r = client.post("/api/ais/analyze", json={"investigation_id": "x", "ais": "../../etc/passwd"}); assert r.status_code == 400


def test_remote_scene_blocks_private_networks():
    response = client.post("/api/auto/url", json={"url": "https://127.0.0.1/private-scene.tif"})
    assert response.status_code == 400 and "Private" in response.json()["detail"]


def test_one_image_entrypoint_reads_geotiff_metadata(tmp_path, monkeypatch):
    from app import jobs
    scene = tmp_path / "scene.tif"
    with rasterio.open(scene, "w", driver="GTiff", width=8, height=8, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(72, 20, 0.001, 0.001)) as dst:
        dst.write(np.full((8, 8), -18, np.float32), 1)
        dst.update_tags(sensing_time="2026-09-19T06:30:00+00:00")
    assert _scene_time(scene, scene.name) == "2026-09-19T06:30:00+00:00"

    queued = {}
    def fake_submit(kind, fn):
        queued["kind"] = kind
        return {"id": "job_test"}
    monkeypatch.setattr(jobs, "submit", fake_submit)
    with scene.open("rb") as source:
        response = client.post("/api/auto/run", files={"file": (scene.name, source, "image/tiff")})
    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "job_test" and queued["kind"] == "image_investigation"
    (config.DATA_DIR / "uploads" / f"{response.json()['upload_id']}.tif").unlink(missing_ok=True)

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
    assert j["scene"]["metadata"]["location_label"] and j["scene"]["metadata"]["crs"] == "EPSG:4326"
    assert j["drift"]["environment"]["wind_speed_ms"] > 0
    assert j["drift"]["environment"]["forecast_toward"] in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    ev = client.get(f"/api/evidence/{j['investigation']['id']}").json(); assert ev["items"] and all(len(i["sha256"]) == 64 for i in ev["items"])
    # Reopening must survive a server restart without manufacturing sparse scenes.
    from app import pipeline
    pipeline._CACHE.clear()
    saved = client.get(f"/api/investigations/{j['investigation']['id']}").json()
    assert saved["investigation"]["id"] == j["investigation"]["id"]
    assert saved["scene"]["quicklook"] == j["scene"]["quicklook"]
    assert saved["scene"]["detections"][0]["geometry"]["polygon_geojson"]
    assert saved["drift"]["backward"] == j["drift"]["backward"]
    assert saved["ais"]["candidates"][0]["mmsi"] == truth["culprit_mmsi"]
    assert saved["ais"]["drift_run_id"] == saved["drift"]["drift_run_id"]
    download = client.get(f"/api/evidence/{j['investigation']['id']}/export")
    assert download.status_code == 200
    assert "attachment;" in download.headers["content-disposition"]
    assert download.json()["investigation"]["id"] == j["investigation"]["id"]
    assert download.json()["items"]
    assert client.get("/api/evidence/missing-case/export").status_code == 404
    # New scene invalidates the displayed old drift/ranking without deleting evidence.
    newer = client.post("/api/satellite/analyze", json={"investigation_id": j["investigation"]["id"], "scene": "demo"})
    assert newer.status_code == 200
    latest = client.get(f"/api/investigations/{j['investigation']['id']}").json()
    assert latest["scene"]["scene_id"] == newer.json()["scene_id"]
    assert latest["drift"] is None and latest["ais"] is None
    assert client.post("/api/ais/analyze", json={"investigation_id": j["investigation"]["id"], "ais": "demo"}).status_code == 409


def test_legacy_case_reopens_without_scene_snapshot():
    from app import pipeline
    inv = pipeline.create_investigation("Legacy restore regression")
    scene = pipeline.analyze_scene(inv["id"], str(config.DEMO_DIR / "synthetic_s1_scene.tif"))
    with db.conn() as c:
        c.execute("DELETE FROM evidence WHERE investigation_id=? AND kind='scene_result'", (inv["id"],))
    pipeline._CACHE.clear()
    saved = client.get(f"/api/investigations/{inv['id']}")
    assert saved.status_code == 200
    restored = saved.json()["scene"]
    assert restored["scene_id"] == scene["scene_id"]
    assert restored["detections"][0]["geometry"]["area_km2"] == scene["detections"][0]["geometry"]["area_km2"]
