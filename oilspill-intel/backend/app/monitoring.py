"""Continuous Sentinel-1 product tracking and cross-scene incident grouping."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from . import db


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _distance_km(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lon"] - a["lon"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def cluster_events(events: list[dict], radius_km: float = 8, max_gap_hours: float = 72) -> list[dict]:
    """Group repeat-pass detections while keeping slicks in one image distinct."""
    incidents: list[dict] = []
    for event in sorted(events, key=lambda item: item["sensing_time"]):
        event_time = _time(event["sensing_time"])
        matches = []
        for incident in incidents:
            if event["product_id"] in incident["product_ids"]:
                continue
            gap = (event_time - _time(incident["last_seen"])).total_seconds() / 3600
            distance = _distance_km(event, incident)
            if 0 <= gap <= max_gap_hours and distance <= radius_km:
                matches.append((distance, incident))
        if matches:
            incident = min(matches, key=lambda match: match[0])[1]
            count = len(incident["events"])
            incident["lat"] = (incident["lat"] * count + event["lat"]) / (count + 1)
            incident["lon"] = (incident["lon"] * count + event["lon"]) / (count + 1)
            incident["last_seen"] = event["sensing_time"]
            incident["product_ids"].add(event["product_id"])
            incident["events"].append(event)
            incident["max_score"] = max(incident["max_score"], event["score"])
            incident["max_area_km2"] = max(incident["max_area_km2"], event["area_km2"])
        else:
            incidents.append({
                "id": "inc_" + hashlib.sha1(event["detection_id"].encode()).hexdigest()[:10],
                "first_seen": event["sensing_time"], "last_seen": event["sensing_time"],
                "lat": event["lat"], "lon": event["lon"], "max_score": event["score"],
                "max_area_km2": event["area_km2"], "product_ids": {event["product_id"]},
                "events": [event],
            })
    for incident in incidents:
        incident["product_ids"] = sorted(incident["product_ids"])
        incident["scene_count"] = len(incident["product_ids"])
    return sorted(incidents, key=lambda item: (item["last_seen"], item["max_score"]), reverse=True)


def claim_product(monitor_id: str, product: dict) -> bool:
    """Atomically reserve a catalogue product; completed products are never reprocessed."""
    with db.conn() as connection:
        row = connection.execute(
            "SELECT status FROM monitor_products WHERE monitor_id=? AND product_id=?",
            (monitor_id, product["id"]),
        ).fetchone()
        if row and row["status"] in ("processing", "done"):
            return False
        connection.execute(
            "INSERT OR REPLACE INTO monitor_products VALUES (?,?,?,?,?,?,?,?,?)",
            (monitor_id, product["id"], None, product.get("sensing_start"), "processing",
             json.dumps(product.get("bbox")), None, None, db.now()),
        )
    return True


def finish_product(monitor_id: str, product_id: str, investigation_id: str | None,
                   result: dict | None = None, error: str | None = None) -> None:
    with db.conn() as connection:
        connection.execute(
            "UPDATE monitor_products SET investigation_id=?, status=?, result=?, error=? "
            "WHERE monitor_id=? AND product_id=?",
            (investigation_id, "failed" if error else "done", json.dumps(result) if result else None,
             error, monitor_id, product_id),
        )


def summary(monitor_id: str, min_score: float = 0.65) -> dict:
    with db.conn() as connection:
        products = [dict(row) for row in connection.execute(
            "SELECT * FROM monitor_products WHERE monitor_id=? ORDER BY sensing_time DESC", (monitor_id,)
        )]
    events = []
    for product in products:
        if product["status"] != "done" or not product["result"]:
            continue
        result = json.loads(product["result"])
        for detection in result.get("detections", []):
            geometry = detection["geometry"]
            if detection["oil_likelihood"] < min_score:
                continue
            events.append({
                "product_id": product["product_id"], "investigation_id": product["investigation_id"],
                "detection_id": detection["id"], "sensing_time": product["sensing_time"],
                "lat": geometry["centroid_lat"], "lon": geometry["centroid_lon"],
                "area_km2": geometry["area_km2"], "score": detection["oil_likelihood"],
                "label": detection["label"],
            })
    return {
        "monitor_id": monitor_id,
        "products": [{key: product[key] for key in ("product_id", "investigation_id", "sensing_time", "status", "error")} for product in products],
        "counts": {"catalogued": len(products), "processed": sum(p["status"] == "done" for p in products),
                   "failed": sum(p["status"] == "failed" for p in products), "slick_events": len(events)},
        "incidents": cluster_events(events),
    }
