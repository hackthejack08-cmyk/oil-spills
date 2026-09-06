"""Oil-slick segmentation.

Two back-ends, selected automatically:

* **CNN** – U-Net (segmentation_models_pytorch, MIT) with an ImageNet-pretrained
  ResNet-18 encoder adapted to 2 input channels (VV, VH dB). Used whenever a
  trained checkpoint exists at config.SEG_WEIGHTS.
* **Baseline** – classical adaptive dark-spot detector (local-contrast threshold
  + morphology). Used when no checkpoint is present so the *whole pipeline* still
  runs offline. Its outputs are clearly tagged `detector="baseline-adaptive-threshold"`
  and receive a lower confidence ceiling. This is NOT a substitute for the
  trained model – it is the guarantee that the demo never dead-ends.

Both return a probability map in [0,1] of the same size as the scene.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy import ndimage as ndi

from .. import config
from .preprocess import Scene, normalise, sea_mask, tiles

log = logging.getLogger(__name__)

_MODEL = None
_DEVICE = "cpu"


def _load_cnn():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if not config.SEG_WEIGHTS.exists():
        return None
    try:
        import torch
        import segmentation_models_pytorch as smp
        model = smp.Unet(encoder_name=config.SEG_ENCODER, encoder_weights=None,
                         in_channels=config.SEG_IN_CHANNELS, classes=1)
        state = torch.load(config.SEG_WEIGHTS, map_location="cpu")
        model.load_state_dict(state["model"] if "model" in state else state)
        model.eval()
        _MODEL = model
        log.info("Loaded CNN weights %s", config.SEG_WEIGHTS)
    except Exception as exc:  # pragma: no cover
        log.exception("Could not load CNN weights: %s", exc)
        _MODEL = None
    return _MODEL


def predict_cnn(scene: Scene) -> Optional[np.ndarray]:
    model = _load_cnn()
    if model is None:
        return None
    import torch
    x = normalise(scene.db)
    h, w = scene.shape
    prob = np.zeros((h, w), np.float32)
    weight = np.zeros((h, w), np.float32)
    with torch.no_grad():
        for r0, c0, r1, c1 in tiles(h, w):
            patch = x[:, r0:r1, c0:c1]
            ph, pw = patch.shape[1:]
            pad = np.zeros((x.shape[0], config.TILE, config.TILE), np.float32)
            pad[:, :ph, :pw] = patch
            t = torch.from_numpy(pad)[None]
            p = torch.sigmoid(model(t))[0, 0].numpy()[:ph, :pw]
            prob[r0:r1, c0:c1] += p
            weight[r0:r1, c0:c1] += 1
    prob /= np.maximum(weight, 1)
    return prob


def predict_baseline(scene: Scene) -> np.ndarray:
    """Adaptive dark-spot detector. Oil damps Bragg waves -> locally low sigma0.
    Score = how far below the local background (in dB) a pixel is, squashed to
    [0,1]. Local background = large-window median (robust to the slick itself)."""
    vv = scene.db[0].astype(np.float32)
    valid = sea_mask(scene.db, land=scene.land)
    filled = np.where(valid, vv, np.nanmedian(vv[valid]) if valid.any() else -20)
    smooth = ndi.median_filter(filled, size=5)
    background = ndi.uniform_filter(ndi.uniform_filter(smooth, size=401), size=401)
    contrast = background - smooth           # positive where darker than surroundings
    # 2.5 dB below background ~ 0.5 probability, 6 dB ~ 0.95 (logistic; assumption)
    prob = 1.0 / (1.0 + np.exp(-(contrast - 2.5) * 1.2))
    prob[~valid] = 0.0
    return prob.astype(np.float32)


def segment(scene: Scene):
    prob = predict_cnn(scene)
    detector = "unet-resnet18-s1"
    if prob is None:
        prob = predict_baseline(scene)
        detector = "baseline-adaptive-threshold"
    if scene.land is not None:
        prob = np.where(scene.land, 0.0, prob).astype(np.float32)
    mask = prob >= config.SEG_THRESHOLD
    mask = ndi.binary_opening(mask, iterations=1)
    mask = ndi.binary_closing(mask, iterations=8)
    mask = ndi.binary_fill_holes(mask)
    labels, n = ndi.label(mask)
    if n:
        sizes = ndi.sum(mask, labels, range(1, n + 1))
        keep = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= config.MIN_SLICK_AREA_PX])
        mask = keep
    return prob, mask.astype(bool), detector
