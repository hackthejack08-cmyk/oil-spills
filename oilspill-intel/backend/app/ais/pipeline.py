"""AIS ingestion, validation, cleaning and trajectory reconstruction.

Column contract (MarineCadastre-compatible, case-insensitive):
  MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, VesselType,
  Length, Width, Draft, Status  (+ optional `synthetic` flag column)

Every dropped record is counted with a reason so data quality is reportable.
We never invent positions: interpolation is *linear between consecutive real
fixes only when the gap ≤ AIS_GAP_MINUTES*; longer gaps remain gaps and are
flagged as behavioural evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import config

R_EARTH_KM = 6371.0088

# ITU / MarineCadastre VesselType codes (AIS ship-type field, 2-digit)
TANKER = set(range(80, 90))
CARGO = set(range(70, 80))
FISHING = {30}
PASSENGER = set(range(60, 70))


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH_KM * np.arcsin(np.sqrt(a))


def bearing_deg(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    x = np.sin(lon2 - lon1) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(lon2 - lon1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


@dataclass
class CleanReport:
    n_raw: int = 0
    n_kept: int = 0
    dropped: Dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, n: int):
        if n:
            self.dropped[reason] = self.dropped.get(reason, 0) + int(n)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().upper() for c in df.columns]
    ren = {"BASEDATETIME": "TIME", "TIMESTAMP": "TIME", "VESSELNAME": "NAME", "VESSELTYPE": "TYPE",
           "LATITUDE": "LAT", "LONGITUDE": "LON"}
    return df.rename(columns=ren)


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanReport]:
    rep = CleanReport(n_raw=len(df))
    d = df.copy()
    # timestamps
    d["TIME"] = pd.to_datetime(d["TIME"], utc=True, errors="coerce")
    bad = d["TIME"].isna(); rep.drop("bad_timestamp", bad.sum()); d = d[~bad]
    # MMSI validity (9 digits, MID 201-775)
    d["MMSI"] = pd.to_numeric(d["MMSI"], errors="coerce")
    bad = d["MMSI"].isna() | (d["MMSI"] < 201000000) | (d["MMSI"] > 775999999)
    rep.drop("invalid_mmsi", bad.sum()); d = d[~bad]
    d["MMSI"] = d["MMSI"].astype("int64")
    # positions
    for c in ("LAT", "LON", "SOG", "COG"):
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    bad = d["LAT"].isna() | d["LON"].isna() | (d["LAT"].abs() > 90) | (d["LON"].abs() > 180) | \
          ((d["LAT"] == 0) & (d["LON"] == 0)) | (d["LAT"].abs() == 91) | (d["LON"].abs() == 181)
    rep.drop("invalid_position", bad.sum()); d = d[~bad]
    # sentinel values
    d.loc[d["SOG"] >= 102.3, "SOG"] = np.nan          # AIS "not available"
    d.loc[d["COG"] >= 360, "COG"] = np.nan
    if "HEADING" in d:
        d["HEADING"] = pd.to_numeric(d["HEADING"], errors="coerce")
        d.loc[d["HEADING"] == 511, "HEADING"] = np.nan
    # duplicates
    before = len(d); d = d.drop_duplicates(subset=["MMSI", "TIME"]); rep.drop("duplicate", before - len(d))
    d = d.sort_values(["MMSI", "TIME"]).reset_index(drop=True)
    # impossible implied speed between consecutive fixes
    g = d.groupby("MMSI")
    dist = haversine_km(g["LON"].shift(), g["LAT"].shift(), d["LON"], d["LAT"])
    dt_h = (d["TIME"] - g["TIME"].shift()).dt.total_seconds() / 3600
    implied_kn = dist / dt_h.replace(0, np.nan) / 1.852
    bad = implied_kn > config.AIS_MAX_SOG_KN * 1.5
    rep.drop("impossible_speed", bad.sum()); d = d[~bad.fillna(False)]
    rep.n_kept = len(d)
    return d.reset_index(drop=True), rep


@dataclass
class Track:
    mmsi: int
    name: str
    imo: str
    vtype: int
    df: pd.DataFrame                 # cleaned fixes
    gaps: List[dict]                 # {"start","end","minutes","lon","lat"}

    @property
    def category(self) -> str:
        t = self.vtype
        return ("tanker" if t in TANKER else "cargo" if t in CARGO else "fishing" if t in FISHING
                else "passenger" if t in PASSENGER else "other")

    def position_at(self, when: datetime) -> Optional[tuple]:
        """Linear interpolation between real fixes only if the bracketing gap is
        short enough; returns None inside a long gap or outside the track."""
        t = self.df["TIME"].values.astype("datetime64[s]").astype("int64")
        w = int(when.replace(tzinfo=timezone.utc).timestamp()) if when.tzinfo is None else int(when.timestamp())
        if w < t[0] or w > t[-1]:
            return None
        i = int(np.searchsorted(t, w))
        if i < len(t) and t[i] == w:
            r = self.df.iloc[i]; return float(r.LON), float(r.LAT), 0.0
        a, b = self.df.iloc[i - 1], self.df.iloc[i]
        gap_min = (t[i] - t[i - 1]) / 60
        if gap_min > config.AIS_GAP_MINUTES:
            return None
        f = (w - t[i - 1]) / (t[i] - t[i - 1])
        return float(a.LON + f * (b.LON - a.LON)), float(a.LAT + f * (b.LAT - a.LAT)), float(gap_min)

    def dead_reckon_in_gap(self, when: datetime) -> Optional[tuple]:
        """If `when` falls inside a long AIS gap, return the straight-line
        dead-reckoned position between the two REAL bracketing fixes plus an
        uncertainty radius (km) that grows with gap length. Clearly an
        assumption (constant course/speed) – reported as such in evidence."""
        t = self.df["TIME"].values.astype("datetime64[s]").astype("int64")
        w = int(when.timestamp())
        if w <= t[0] or w >= t[-1]:
            return None
        i = int(np.searchsorted(t, w))
        gap_min = (t[i] - t[i - 1]) / 60
        if gap_min <= config.AIS_GAP_MINUTES:
            return None
        a, b = self.df.iloc[i - 1], self.df.iloc[i]
        f = (w - t[i - 1]) / (t[i] - t[i - 1])
        unc_km = 0.10 * gap_min          # assumption: ~0.1 km positional uncertainty per silent minute
        return float(a.LON + f * (b.LON - a.LON)), float(a.LAT + f * (b.LAT - a.LAT)), float(gap_min), unc_km


def build_tracks(d: pd.DataFrame) -> List[Track]:
    tracks = []
    for mmsi, g in d.groupby("MMSI"):
        g = g.sort_values("TIME").reset_index(drop=True)
        dt = g["TIME"].diff().dt.total_seconds().div(60).fillna(0)
        gaps = [{"start": g.TIME[i - 1].isoformat(), "end": g.TIME[i].isoformat(), "minutes": round(float(dt[i]), 1),
                 "lon": float((g.LON[i - 1] + g.LON[i]) / 2), "lat": float((g.LAT[i - 1] + g.LAT[i]) / 2)}
                for i in range(1, len(g)) if dt[i] > config.AIS_GAP_MINUTES]
        def first(col, default):
            if col not in g:
                return default
            v = g[col].dropna()
            v = v[v.astype(str).str.strip() != ""] if len(v) else v
            return v.iloc[0] if len(v) else default
        vt = pd.to_numeric(g["TYPE"], errors="coerce").dropna() if "TYPE" in g else pd.Series([], dtype=float)
        tracks.append(Track(int(mmsi), str(first("NAME", f"MMSI {mmsi}")), str(first("IMO", "")),
                            int(vt.iloc[0]) if len(vt) else 0, g, gaps))
    return tracks


def spatial_temporal_filter(tracks: List[Track], lon: float, lat: float, radius_km: float,
                            t_start: datetime, t_end: datetime) -> List[Track]:
    """Keep vessels with ≥1 fix inside the window AND within radius of the origin
    envelope, OR with an AIS gap bracketing the window (silence is evidence)."""
    out = []
    for tr in tracks:
        g = tr.df[(tr.df.TIME >= t_start) & (tr.df.TIME <= t_end)]
        near = haversine_km(g.LON.values, g.LAT.values, lon, lat) <= radius_km if len(g) else np.array([])
        gap_bracket = any(pd.Timestamp(x["start"]) <= t_end and pd.Timestamp(x["end"]) >= t_start and
                          haversine_km(x["lon"], x["lat"], lon, lat) <= radius_km * 1.5 for x in tr.gaps)
        if (len(near) and near.any()) or gap_bracket:
            out.append(tr)
    return out
