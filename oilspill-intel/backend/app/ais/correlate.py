"""Vessel–spill correlation and explainable ranking.

Score components ∈ [0,1], each with a human-readable explanation, combined
with the transparent weights in config.CORR_WEIGHTS. The result is a
*correlation score*, explicitly NOT a probability of guilt. An ML re-ranker
(logistic regression / gradient boosting) can be trained on the same feature
vector once labelled cases exist – the feature builder is shared.

Spatial term: instead of distance to a single origin point, we use the
minimum, over all age hypotheses h, of the Mahalanobis-like distance between
the vessel's interpolated position at time (t_obs − h) and the hypothesis h
particle cloud. This correctly rewards vessels that were where the oil *would
have been* at any plausible release time.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from .. import config
from .pipeline import Track, haversine_km, bearing_deg


@dataclass
class Evidence:
    name: str
    score: float
    weight: float
    explanation: str
    raw: dict = field(default_factory=dict)


@dataclass
class Candidate:
    mmsi: int
    name: str
    imo: str
    category: str
    correlation: float
    best_age_hours: Optional[float]
    min_distance_km: Optional[float]
    time_offset_min: Optional[float]
    evidence: List[Evidence]
    limitations: List[str]
    track_geojson: dict

    def dict(self):
        d = asdict(self); return d


def _cloud_distance(lon, lat, cloud: np.ndarray) -> tuple[float, float]:
    """Return (km to cloud centre, normalised distance in units of cloud spread)."""
    c_lon, c_lat = cloud[:, 0].mean(), cloud[:, 1].mean()
    d_centre = float(haversine_km(lon, lat, c_lon, c_lat))
    spread = max(float(np.mean(haversine_km(cloud[:, 0], cloud[:, 1], c_lon, c_lat))), 1.0)
    d_min = float(np.min(haversine_km(lon, lat, cloud[:, 0], cloud[:, 1])))
    return d_min, d_centre / spread


def score_vessel(tr: Track, t_obs: datetime, hypotheses: List[dict], slick_centre: tuple,
                 slick_orientation_deg: float) -> Candidate:
    ev: List[Evidence] = []
    lims: List[str] = []

    # ---- spatial + temporal (over age hypotheses) --------------------------
    best = None
    for h in hypotheses:
        cloud = np.asarray(h["particles"])
        t_h = t_obs - timedelta(hours=h["age_hours"])
        # sample vessel position at t_h and ±20 min to absorb timing uncertainty
        for off in (-20, -10, 0, 10, 20):
            tq = t_h + timedelta(minutes=off)
            pos = tr.position_at(tq)
            dr = None
            if pos is None:
                dr = tr.dead_reckon_in_gap(tq)
                if dr is None:
                    continue
                pos = dr[:3]
            d_min, d_norm = _cloud_distance(pos[0], pos[1], cloud)
            if dr is not None:                       # inflate tolerance by gap uncertainty (capped: a long
                d_norm = d_norm / min(1.0 + dr[3] / 2.0, 2.5)   # silence must not make *everything* plausible)
            d_norm = np.hypot(d_norm, d_min / 12.0)  # absolute-scale term: 12 km ≈ 1σ regardless of cloud size
            cand = (d_norm, d_min, h["age_hours"], off, pos, dr)
            if best is None or cand[0] < best[0]:
                best = cand
    if best is None:
        ev.append(Evidence("spatial", 0.0, config.CORR_WEIGHTS["spatial"],
                           "No valid AIS position inside any origin time window (gap or absent)"))
        ev.append(Evidence("temporal", 0.0, config.CORR_WEIGHTS["temporal"], "No temporal overlap with origin window"))
        lims.append("Vessel had no AIS fixes within the hindcast window; spatial/temporal evidence unavailable")
        d_min = None; age = None; off = None
    else:
        d_norm, d_min, age, off, pos, dr = best
        s_sp = float(np.exp(-0.5 * d_norm ** 2))          # Gaussian in normalised cloud distance
        if dr is not None:
            s_sp *= 0.9                                     # small penalty: position is inferred, not observed
            ev.append(Evidence("spatial", round(s_sp, 3), config.CORR_WEIGHTS["spatial"],
                               f"Vessel was in AIS silence ({dr[2]:.0f} min) at age hypothesis {age} h; dead-reckoned "
                               f"position (±{dr[3]:.1f} km, constant-course assumption) lies {d_min:.1f} km from hindcast cloud",
                               {"d_min_km": d_min, "age_h": age, "dead_reckoned": True, "gap_min": dr[2]}))
            lims.append(f"Position at the best-matching release time is dead-reckoned across a {dr[2]:.0f}-min AIS gap")
        else:
            ev.append(Evidence("spatial", round(s_sp, 3), config.CORR_WEIGHTS["spatial"],
                               f"Closest approach {d_min:.1f} km to hindcast cloud for age hypothesis {age} h "
                               f"({d_norm:.2f}× cloud spread)", {"d_min_km": d_min, "age_h": age}))
        s_t = float(np.exp(-abs(off) / 20.0))
        ev.append(Evidence("temporal", round(s_t, 3), config.CORR_WEIGHTS["temporal"],
                           f"Best match at t_obs − {age} h {'' if off == 0 else f'{off:+d} min'} within ±20 min tolerance",
                           {"offset_min": off}))

    # ---- trajectory alignment -------------------------------------------------
    g = tr.df
    if len(g) >= 2:
        # heading of the vessel while nearest to the slick vs slick major axis
        d_all = haversine_km(g.LON.values, g.LAT.values, slick_centre[0], slick_centre[1])
        i = int(np.argmin(d_all)); j = max(i - 1, 0) if i == len(g) - 1 else i + 1
        brg = float(bearing_deg(g.LON.values[min(i, j)], g.LAT.values[min(i, j)], g.LON.values[max(i, j)], g.LAT.values[max(i, j)]))
        diff = abs(((brg - slick_orientation_deg) + 90) % 180 - 90)   # axis vs bearing, 0..90
        s_tr = float(max(0.0, 1 - diff / 90.0))
        ev.append(Evidence("trajectory", round(s_tr, 3), config.CORR_WEIGHTS["trajectory"],
                           f"Track bearing {brg:.0f}° vs slick major axis {slick_orientation_deg:.0f}° "
                           f"(angular difference {diff:.0f}°; elongated discharges align with the ship track)",
                           {"bearing": brg, "axis": slick_orientation_deg, "diff": diff}))
    else:
        ev.append(Evidence("trajectory", 0.0, config.CORR_WEIGHTS["trajectory"], "Insufficient fixes for a bearing"))
        lims.append("Only one AIS fix – trajectory analysis not possible")

    # ---- behaviour: gaps, speed drops, course changes near the window ---------
    win_lo, win_hi = t_obs - timedelta(hours=max(h["age_hours"] for h in hypotheses) + 1), t_obs
    gw = g[(g.TIME >= win_lo) & (g.TIME <= win_hi)]
    beh = 0.0; notes = []
    gaps_in = [x for x in tr.gaps if pd.Timestamp(x["start"]) <= win_hi and pd.Timestamp(x["end"]) >= win_lo]
    if gaps_in:
        beh += 0.45; notes.append(f"AIS gap of {max(x['minutes'] for x in gaps_in):.0f} min inside window")
    if len(gw) >= 3 and gw.SOG.notna().sum() >= 3:
        sog = gw.SOG.values
        if np.nanmax(sog) - np.nanmin(sog) > 4:
            beh += 0.3; notes.append(f"Speed change {np.nanmin(sog):.1f}→{np.nanmax(sog):.1f} kn in window")
        cog = gw.COG.dropna().values
        if len(cog) >= 3:
            dc = np.abs(((np.diff(cog) + 180) % 360) - 180)
            if dc.max() > 30:
                beh += 0.25; notes.append(f"Course change of {dc.max():.0f}° in window")
    beh = min(beh, 1.0)
    ev.append(Evidence("behaviour", round(beh, 3), config.CORR_WEIGHTS["behaviour"],
                       "; ".join(notes) if notes else "No anomalous behaviour detected in window"))

    # ---- vessel type prior ----------------------------------------------------
    prior = {"tanker": 1.0, "cargo": 0.7, "passenger": 0.4, "other": 0.4, "fishing": 0.3}[tr.category]
    ev.append(Evidence("vessel_type", prior, config.CORR_WEIGHTS["vessel_type"],
                       f"Type '{tr.category}' (AIS type code {tr.vtype}); oil-carrying capability prior"))

    # ---- AIS data quality -----------------------------------------------------
    span_min = max((g.TIME.iloc[-1] - g.TIME.iloc[0]).total_seconds() / 60, 1)
    expected = span_min / 3.0                                  # class-A underway ≈ every ≤3 min at coarse sampling
    completeness = float(min(1.0, len(g) / max(expected, 1)))
    ev.append(Evidence("ais_quality", round(completeness, 3), config.CORR_WEIGHTS["ais_quality"],
                       f"{len(g)} fixes over {span_min:.0f} min ({completeness*100:.0f}% of expected reporting)"))
    if completeness < 0.5:
        lims.append("Sparse AIS – positions between fixes are uncertain")

    total = sum(e.score * e.weight for e in ev) / sum(e.weight for e in ev)
    # Spatial consistency is a NECESSARY condition: a vessel that was never near any plausible
    # release position cannot be a strong candidate however suspicious its type/behaviour.
    s_spatial = next(e.score for e in ev if e.name == "spatial")
    total *= (0.25 + 0.75 * s_spatial)
    if s_spatial < 0.1:
        lims.append("No spatial consistency with any origin hypothesis – score capped (weak candidate)")
    coords = [[float(a), float(b)] for a, b in zip(g.LON.values, g.LAT.values)]
    return Candidate(tr.mmsi, tr.name, tr.imo, tr.category, round(float(total), 3),
                     age, None if d_min is None else round(d_min, 2), off, ev, lims,
                     {"type": "LineString", "coordinates": coords})


def rank(tracks: List[Track], t_obs: datetime, hypotheses: List[dict], slick_centre: tuple,
         slick_orientation_deg: float) -> List[Candidate]:
    cands = [score_vessel(t, t_obs, hypotheses, slick_centre, slick_orientation_deg) for t in tracks]
    cands.sort(key=lambda c: -c.correlation)
    return cands
