"""Look-alike rejection – Approach D+C hybrid (segmentation + physical/geospatial
rules + optional secondary classifier).

For each connected dark object we compute physically motivated features and
apply transparent rules. Each rule returns (penalty, reason). The final
`oil_likelihood` = segmentation confidence × Π(1 − penalty). Every reason is
stored so the evidence panel can show *why* an object was down-weighted.

Rules (each is an engineering recommendation grounded in the SAR literature –
Brekke & Solberg 2005; Alpers et al. 2017; EMSA CleanSeaNet practice):
  R1 wind speed outside ~2–3 … 10 m/s window  -> low-wind look-alike risk
  R2 very low elongation & huge area           -> low-wind area / rain cell
  R3 contrast too weak (< 1.5 dB)              -> biogenic film / weak feature
  R4 touching land mask                        -> wind shadow / shallow water
  R5 extreme thinness + straightness + ship at tip -> could be wake (flag, don't reject)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LookalikeAssessment:
    oil_likelihood: float
    penalties: List[dict] = field(default_factory=list)
    label: str = "possible_oil"


def assess(seg_conf: float, *, mean_contrast_db: float, elongation: float,
           area_km2: float, touches_land: bool, wind_ms: Optional[float],
           near_ship: bool = False, classifier_prob: Optional[float] = None,
           shore_km: Optional[float] = None) -> LookalikeAssessment:
    pens = []
    # Contrast is the single most diagnostic scalar we have: mineral oil typically damps Bragg scattering by
    # ~3–10 dB while biogenic films / wind-streak texture give ~1–3 dB (Alpers et al. 2017; Brekke & Solberg 2005).
    # Geospatial rules therefore scale with contrast: a high-contrast object touching a coast is still a serious
    # candidate (e.g. Wakashio 2020 – observed 6–7 dB against the Mauritius reef), a faint one is probably shadow.
    strong = mean_contrast_db >= 4.0
    very_strong = mean_contrast_db >= 6.0
    scale = 0.35 if very_strong else 0.6 if strong else 1.0

    if shore_km is not None and not touches_land and shore_km < 5.0:
        pens.append({"rule": "R4b_sheltered_water", "penalty": round(0.20 * scale, 3),
                     "detail": f"Object {shore_km:.1f} km from shore: sheltered / wind-shadow water and river plumes are frequent look-alikes"
                               + (f" (penalty reduced: contrast {mean_contrast_db:.1f} dB is oil-like)" if strong else "")})

    if wind_ms is not None:
        if wind_ms < 2.5:
            pens.append({"rule": "R1_low_wind", "penalty": 0.45,
                         "detail": f"Wind {wind_ms:.1f} m/s < 2.5 m/s: calm-sea dark patches mimic oil"})
        elif wind_ms > 10.0:
            pens.append({"rule": "R1_high_wind", "penalty": 0.25,
                         "detail": f"Wind {wind_ms:.1f} m/s > 10 m/s: thin oil dispersed, contrast unreliable"})

    if elongation < 1.6 and area_km2 > 5 and not strong:
        pens.append({"rule": "R2_blob_shape", "penalty": 0.35,
                     "detail": f"Round, large object (elong {elongation:.1f}, {area_km2:.0f} km²) with modest contrast resembles low-wind cell / rain cell"})

    if mean_contrast_db < 1.5:
        pens.append({"rule": "R3_weak_contrast", "penalty": 0.35,
                     "detail": f"Contrast {mean_contrast_db:.1f} dB below background is weak (biogenic film / wind-streak texture?)"})
    elif mean_contrast_db < 2.5:
        pens.append({"rule": "R3_modest_contrast", "penalty": 0.20,
                     "detail": f"Contrast {mean_contrast_db:.1f} dB is in the ambiguous 1.5–2.5 dB band shared by thin oil and natural films"})

    if touches_land:
        pens.append({"rule": "R4_coastal", "penalty": round(0.30 * scale, 3),
                     "detail": "Object touches land mask: wind-shadow / shallow-water look-alike risk"
                               + (f" (penalty reduced: contrast {mean_contrast_db:.1f} dB is oil-like; coastal groundings do occur)" if strong else "")})

    if near_ship and elongation > 8:
        pens.append({"rule": "R5_possible_wake", "penalty": 0.10,
                     "detail": "Very thin linear feature with bright target at tip: could be ship wake OR fresh discharge – flagged"})

    like = seg_conf
    for p in pens:
        like *= (1.0 - p["penalty"])
    if classifier_prob is not None:   # secondary classifier (Approach C) – geometric mean fusion
        like = (like * classifier_prob) ** 0.5

    label = "probable_oil" if like >= 0.6 else "possible_oil" if like >= 0.35 else "probable_lookalike"
    return LookalikeAssessment(oil_likelihood=float(round(like, 3)), penalties=pens, label=label)
