"""Central configuration. Every path is relative to the repository root so the
same code runs from `python -m app.main`, pytest and Docker."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = Path(os.getenv("OSI_DEMO_DIR", REPO_ROOT / "demo" / "data"))
DATA_DIR = Path(os.getenv("OSI_DATA_DIR", REPO_ROOT / "backend" / "runtime"))
MODEL_DIR = Path(os.getenv("OSI_MODEL_DIR", REPO_ROOT / "backend" / "models"))
DB_PATH = Path(os.getenv("OSI_DB_PATH", DATA_DIR / "osi.sqlite"))
STATIC_DIR = REPO_ROOT / "frontend" / "static"

for _p in (DATA_DIR, MODEL_DIR, DATA_DIR / "uploads", DATA_DIR / "renders"):
    _p.mkdir(parents=True, exist_ok=True)

# ---- SAR / model -----------------------------------------------------------
SEG_WEIGHTS = MODEL_DIR / "unet_s1_oil.pt"       # trained checkpoint (optional)
SEG_ENCODER = os.getenv("OSI_SEG_ENCODER", "resnet18")
SEG_IN_CHANNELS = 2                               # VV, VH (sigma0 dB)
TILE = 256
TILE_OVERLAP = 32
DB_CLIP = (-35.0, 5.0)                            # dB range used for normalisation
SEG_THRESHOLD = float(os.getenv("OSI_SEG_THRESHOLD", "0.5"))  # STARTING VALUE – tune on val set
MIN_SLICK_AREA_PX = 60

# ---- Drift ------------------------------------------------------------------
DRIFT_ENGINE = os.getenv("OSI_DRIFT_ENGINE", "builtin")   # "builtin" | "opendrift"
DRIFT_N_PARTICLES = 400
DRIFT_DT_SECONDS = 600                # 10 min
DRIFT_MAX_HOURS = 12                  # backward horizon (age hypotheses 1..12 h)
WIND_DRIFT_FACTOR = (0.02, 0.04)      # literature range ~2.5–4.4 %, mean ≈3 %
HORIZONTAL_DIFFUSIVITY = 10.0         # m^2/s  (assumption; typical 1–100)
CURRENT_UNCERTAINTY = 0.05            # m/s   (mirrors OpenDrift default)
WIND_UNCERTAINTY = 0.5                # m/s

# ---- AIS --------------------------------------------------------------------
AIS_MAX_SOG_KN = 40.0                 # hard physical plausibility cap for merchant vessels
AIS_GAP_MINUTES = 20.0                # gap considered "AIS silence"
AIS_SEARCH_RADIUS_KM = 60.0

# ---- Correlation weights (rule-based baseline; documented in report) -------
CORR_WEIGHTS = {
    "spatial": 0.40,
    "temporal": 0.15,
    "trajectory": 0.15,
    "behaviour": 0.15,
    "vessel_type": 0.10,
    "ais_quality": 0.05,
}
