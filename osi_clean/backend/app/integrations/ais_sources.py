"""AIS connectors.

* MarineCadastre (NOAA/USCG, CC0): daily national zip files
  https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{YYYY}/AIS_{YYYY}_{MM}_{DD}.zip  [verified live, 2009→]
  Files are 300–800 MB each; we stream-filter by bbox/time and cache the subset.
* Danish Maritime Authority (open): http://web.ais.dk/aisdata/aisdk-YYYY-MM-DD.zip  (Danish waters)
* aisstream.io (free key, REAL-TIME ONLY): websocket recorder that writes the
  MarineCadastre CSV schema so recorded live traffic can feed the same pipeline.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests

MC_URLS = [   # tried in order; NOAA is migrating 2024+ to Azure blob storage (verified 2026-09: 2009–2023 on coast.noaa.gov)
    "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{y}/AIS_{y}_{m:02d}_{d:02d}.zip",
    "https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais{y}/AIS_{y}_{m:02d}_{d:02d}.zip",
    "https://noaaocm.blob.core.windows.net/ais/csv2/csv{y}/AIS_{y}_{m:02d}_{d:02d}.zip",
]
DMA_URL = "http://web.ais.dk/aisdata/aisdk-{y}-{m:02d}-{d:02d}.zip"
COLS = ["MMSI", "BaseDateTime", "LAT", "LON", "SOG", "COG", "Heading", "VesselName", "IMO", "CallSign", "VesselType", "Status", "Length", "Width", "Draft", "Cargo"]


def _fetch_zip(url: str, cache: Path, progress: Optional[Callable] = None) -> Path:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return cache
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)); done = 0
        tmp = cache.with_suffix(".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(4 << 20):
                f.write(chunk); done += len(chunk)
                if progress: progress(done, total)
        tmp.rename(cache)
    return cache


def marinecadastre_subset(bbox, t0: datetime, t1: datetime, cache_dir: Path, out_csv: Path, progress=None) -> dict:
    lon0, lat0, lon1, lat1 = bbox
    days = pd.date_range(t0.date(), t1.date(), freq="D")
    frames = []; n_raw = 0
    for day in days:
        z = None; errs = []
        for tmpl in MC_URLS:
            url = tmpl.format(y=day.year, m=day.month, d=day.day)
            try:
                z = _fetch_zip(url, cache_dir / Path(url).name, progress); break
            except requests.HTTPError as e:
                errs.append(f"{url}: {e.response.status_code}")
        if z is None:
            raise FileNotFoundError("MarineCadastre file not available for %s (%s). Use AccessAIS (marinecadastre.gov/accessais) to export a custom CSV and upload it." % (day.date(), "; ".join(errs)))
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(name) as fh:
                for chunk in pd.read_csv(fh, chunksize=500_000, usecols=lambda c: c in COLS):
                    n_raw += len(chunk)
                    m = (chunk.LAT.between(lat0, lat1)) & (chunk.LON.between(lon0, lon1))
                    frames.append(chunk[m])
    df = pd.concat(frames) if frames else pd.DataFrame(columns=COLS)
    df["BaseDateTime"] = pd.to_datetime(df["BaseDateTime"], utc=True, errors="coerce")
    df = df[(df.BaseDateTime >= t0) & (df.BaseDateTime <= t1)]
    df["BaseDateTime"] = df["BaseDateTime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    out_csv.parent.mkdir(parents=True, exist_ok=True); df.to_csv(out_csv, index=False)
    return {"path": str(out_csv), "n_raw_scanned": int(n_raw), "n_in_bbox": int(len(df)), "n_vessels": int(df.MMSI.nunique()),
            "source": "NOAA MarineCadastre AIS (CC0)", "days": [d.date().isoformat() for d in days]}


def dma_subset(bbox, t0: datetime, t1: datetime, cache_dir: Path, out_csv: Path, progress=None) -> dict:
    lon0, lat0, lon1, lat1 = bbox
    frames = []; n_raw = 0
    for day in pd.date_range(t0.date(), t1.date(), freq="D"):
        url = DMA_URL.format(y=day.year, m=day.month, d=day.day)
        z = _fetch_zip(url, cache_dir / Path(url).name, progress)
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(name) as fh:
                for chunk in pd.read_csv(fh, chunksize=500_000):
                    n_raw += len(chunk)
                    chunk.columns = [c.strip() for c in chunk.columns]
                    m = chunk["Latitude"].between(lat0, lat1) & chunk["Longitude"].between(lon0, lon1)
                    c = chunk[m]
                    frames.append(pd.DataFrame({"MMSI": c["MMSI"], "BaseDateTime": pd.to_datetime(c["# Timestamp"], dayfirst=True, utc=True),
                                                "LAT": c["Latitude"], "LON": c["Longitude"], "SOG": c["SOG"], "COG": c["COG"], "Heading": c["Heading"],
                                                "VesselName": c.get("Name"), "IMO": c.get("IMO"), "VesselType": c.get("Ship type").map(_dma_type),
                                                "Length": c.get("Length"), "Width": c.get("Width")}))
    df = pd.concat(frames) if frames else pd.DataFrame(columns=COLS)
    df = df[(df.BaseDateTime >= t0) & (df.BaseDateTime <= t1)]
    df["BaseDateTime"] = df["BaseDateTime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    out_csv.parent.mkdir(parents=True, exist_ok=True); df.to_csv(out_csv, index=False)
    return {"path": str(out_csv), "n_raw_scanned": int(n_raw), "n_in_bbox": int(len(df)), "n_vessels": int(df.MMSI.nunique()), "source": "Danish Maritime Authority AIS"}


def _dma_type(s):
    s = str(s).lower()
    return 80 if "tanker" in s else 70 if "cargo" in s else 30 if "fishing" in s else 60 if "passenger" in s else 0


def record_aisstream(bbox, minutes: float, out_csv: Path, api_key: Optional[str] = None, progress=None) -> dict:
    """Record live AIS from aisstream.io into the MarineCadastre schema.
    Requires `pip install websocket-client` and a free key (OSI_AISSTREAM_KEY)."""
    import websocket  # websocket-client
    key = api_key or os.getenv("OSI_AISSTREAM_KEY")
    if not key:
        raise PermissionError("Set OSI_AISSTREAM_KEY (free at aisstream.io)")
    lon0, lat0, lon1, lat1 = bbox
    ws = websocket.create_connection("wss://stream.aisstream.io/v0/stream", timeout=30)
    ws.send(json.dumps({"APIKey": key, "BoundingBoxes": [[[lat0, lon0], [lat1, lon1]]],
                        "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}))
    rows, static = [], {}
    end = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    while datetime.now(timezone.utc) < end:
        try:
            msg = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        meta = msg.get("MetaData", {}); mmsi = meta.get("MMSI")
        if msg.get("MessageType") == "ShipStaticData":
            sd = msg["Message"]["ShipStaticData"]
            static[mmsi] = {"IMO": sd.get("ImoNumber"), "VesselType": sd.get("Type"), "Length": (sd.get("Dimension") or {}).get("A", 0) + (sd.get("Dimension") or {}).get("B", 0)}
        elif msg.get("MessageType") == "PositionReport":
            pr = msg["Message"]["PositionReport"]
            rows.append({"MMSI": mmsi, "BaseDateTime": meta.get("time_utc", "")[:19].replace(" ", "T"), "LAT": pr.get("Latitude"), "LON": pr.get("Longitude"),
                         "SOG": pr.get("Sog"), "COG": pr.get("Cog"), "Heading": pr.get("TrueHeading"), "VesselName": (meta.get("ShipName") or "").strip()})
            if progress and len(rows) % 100 == 0: progress(len(rows), 0)
    ws.close()
    df = pd.DataFrame(rows)
    if len(df):
        for k in ("IMO", "VesselType", "Length"):
            df[k] = df.MMSI.map(lambda m: static.get(m, {}).get(k))
    out_csv.parent.mkdir(parents=True, exist_ok=True); df.to_csv(out_csv, index=False)
    return {"path": str(out_csv), "n_messages": int(len(df)), "n_vessels": int(df.MMSI.nunique()) if len(df) else 0, "source": "aisstream.io live recording"}
