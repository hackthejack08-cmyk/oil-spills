"""Real-data benchmark of the OSI detector on GlobalOSD-SAR (Zenodo 15286918, CC BY 4.0).

The dataset gives ~100k oil-slick centre points (ships / platforms / seeps) and ~100k look-alike points, each
with the Sentinel-1 acquisition date. For every sampled point we stream the matching S1 GRD chip from
Microsoft Planetary Computer (no account), calibrate to σ⁰, run the OSI segmentation + look-alike logic and
record whether a detection of the requested class falls within `hit_km` of the labelled centre.

Metrics (chip level, honest and reproducible):
  * detection rate      = positives with ≥1 object (oil_likelihood ≥ thr) within hit_km / positives processed
  * false-alarm rate    = negatives with ≥1 object (oil_likelihood ≥ thr) within hit_km / negatives processed
  * area under ROC over thr, using the max oil_likelihood inside hit_km as the chip score.

Usage:  python training/benchmark_globalosd.py --n 40 --seed 1 --subcat Ships --factor 4 --half 0.15
Output: runtime/eval/benchmark_<tag>.json (+ per-chip CSV). ~25 s per chip on 2 vCPU.
Caveat: label points are slick *centres*; scene selection takes the first IW GRD on that date intersecting the
point (ascending/descending ambiguity, ~1 % risk of picking a different pass). Not verified against the authors'
own chip extraction.
"""
from __future__ import annotations

import argparse, csv, json, random, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, db, pipeline                                   # noqa: E402
from app.integrations import planetary, s1_calibrate                   # noqa: E402

EVAL = config.DATA_DIR / "eval"


def load_points(subcat: str | None, n: int, seed: int):
    import shapefile
    rng = random.Random(seed)
    pos = shapefile.Reader(str(EVAL / "positives")); neg = shapefile.Reader(str(EVAL / "negatives"))
    P = [(str(r[0]), float(r[3]), float(r[4]), r[2]) for r in pos.iterRecords() if (subcat is None or r[2] == subcat) and str(r[0])[:4] >= "2016"]
    N = [(str(r[0]), float(r[2]), float(r[3]), "lookalike") for r in neg.iterRecords() if str(r[0])[:4] >= "2016"]
    rng.shuffle(P); rng.shuffle(N)
    return P[:n], N[:n]


def haversine_km(lon1, lat1, lon2, lat2):
    R = 6371.0; p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def run_chip(date: str, lon: float, lat: float, half: float, factor: int, inv_id: str, hit_km: float):
    day = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    prods = planetary.search(lon, lat, f"{day}T00:00:00Z", f"{day}T23:59:59Z", top=4)
    if not prods:
        return {"status": "no_scene"}
    out = EVAL / "chips" / f"{date}_{lon:.3f}_{lat:.3f}.tif"
    p = prods[0]
    if not out.exists():
        last = None
        for p in prods:                                    # try each pass on that date until one covers the AOI
            try:
                files = planetary.stage_annotations(p["assets"], config.DATA_DIR / "cache" / "pc" / p["id"], planetary.sas_token())
                s1_calibrate.calibrate_product(files, out, factor=factor, bbox=[lon - half, lat - half, lon + half, lat + half],
                                               source_note=" (Planetary Computer COG)")
                break
            except ValueError as e:
                last = e
        else:
            raise last
    r = pipeline.analyze_scene(inv_id, str(out))
    near = [d for d in r["detections"] if haversine_km(lon, lat, d["geometry"]["centroid_lon"], d["geometry"]["centroid_lat"]) <= hit_km
            or _poly_near(d, lon, lat, hit_km)]
    best = max((d["oil_likelihood"] for d in near), default=0.0)
    best_seg = max((d["seg_confidence"] for d in near), default=0.0)
    return {"status": "ok", "scene": p["id"], "n_det": len(r["detections"]), "n_near": len(near), "score": best, "seg_score": best_seg,
            "wind": r.get("wind_ms"), "rules": sorted({pen["rule"] for d in near for pen in d["lookalike_penalties"]})}


def _poly_near(d, lon, lat, hit_km):
    from shapely.geometry import shape, Point
    try:
        poly = shape(d["geometry"]["polygon_geojson"]) if "polygon_geojson" in d["geometry"] else None
    except Exception:
        poly = None
    if poly is None:
        return False
    return poly.distance(Point(lon, lat)) * 111.0 <= hit_km


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--subcat", default="Ships", help="Ships | Platforms | Natural seeps | all")
    ap.add_argument("--factor", type=int, default=4); ap.add_argument("--half", type=float, default=0.15)
    ap.add_argument("--hit-km", type=float, default=8.0); ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    db.init(); (EVAL / "chips").mkdir(parents=True, exist_ok=True)
    tag = a.tag or f"{a.subcat.replace(' ', '')}_n{a.n}_s{a.seed}_f{a.factor}"
    P, N = load_points(None if a.subcat == "all" else a.subcat, a.n, a.seed)
    inv = pipeline.create_investigation(f"BENCHMARK GlobalOSD {tag}", "eval")
    rows = []
    for label, pts in (("oil", P), ("lookalike", N)):
        for i, (date, lon, lat, sub) in enumerate(pts):
            t = time.time()
            try:
                res = run_chip(date, lon, lat, a.half, a.factor, inv["id"], a.hit_km)
            except Exception as e:  # noqa
                res = {"status": f"error: {type(e).__name__}: {str(e)[:120]}"}
            res |= {"label": label, "subcat": sub, "date": date, "lon": lon, "lat": lat, "secs": round(time.time() - t, 1)}
            rows.append(res); print(f"[{label} {i+1}/{len(pts)}] {date} ({lon:.2f},{lat:.2f}) {res['status']} score={res.get('score')} n_det={res.get('n_det')} {res['secs']}s", flush=True)
            with open(EVAL / f"benchmark_{tag}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r})); w.writeheader(); w.writerows(rows)
    ok = [r for r in rows if r["status"] == "ok"]
    pos = [r for r in ok if r["label"] == "oil"]; neg = [r for r in ok if r["label"] == "lookalike"]
    summary = {"tag": tag, "n_pos": len(pos), "n_neg": len(neg), "skipped": len(rows) - len(ok), "hit_km": a.hit_km, "factor": a.factor, "half_deg": a.half,
               "detector": "unet" if config.SEG_WEIGHTS.exists() else "baseline-adaptive-threshold", "generated": datetime.now(timezone.utc).isoformat()}
    for thr in (0.35, 0.5, 0.6):
        summary[f"detection_rate@{thr}"] = round(np.mean([r["score"] >= thr for r in pos]), 3) if pos else None
        summary[f"false_alarm_rate@{thr}"] = round(np.mean([r["score"] >= thr for r in neg]), 3) if neg else None
    summary["any_dark_object_rate_pos"] = round(np.mean([r["n_near"] > 0 for r in pos]), 3) if pos else None
    summary["any_dark_object_rate_neg"] = round(np.mean([r["n_near"] > 0 for r in neg]), 3) if neg else None
    if pos and neg:
        s = np.array([r["score"] for r in pos] + [r["score"] for r in neg]); y = np.array([1] * len(pos) + [0] * len(neg))
        order = np.argsort(-s); tp = np.cumsum(y[order]); fp = np.cumsum(1 - y[order])
        tpr = tp / y.sum(); fpr = fp / (1 - y).sum(); summary["auc_oil_likelihood"] = round(float(np.trapezoid(tpr, fpr)), 3)
        s2 = np.array([r["seg_score"] for r in pos] + [r["seg_score"] for r in neg]); order = np.argsort(-s2)
        tp = np.cumsum(y[order]); fp = np.cumsum(1 - y[order]); summary["auc_seg_only"] = round(float(np.trapezoid(tp / y.sum(), fp / (1 - y).sum())), 3)
    (EVAL / f"benchmark_{tag}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
