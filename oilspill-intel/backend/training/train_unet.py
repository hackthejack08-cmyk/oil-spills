"""Train the U-Net (ResNet-18 encoder, 2-ch VV/VH) on the Trujillo-Acatitla
Sentinel-1 oil-spill dataset (Zenodo Parts I–III, CC BY 4.0).

Expected layout after extracting the 7z archives (see docs/DATASETS.md):
  data/zenodo/train_val/{oil,no_oil,lookalike}/images/*.tif   (2048x2048x2 sigma0 dB)
  data/zenodo/train_val/{oil,no_oil,lookalike}/masks/*.tif
  data/zenodo/test/{oil,no_oil,lookalike}/images|masks

All hyper-parameters below are STARTING CONFIGURATIONS – they must be tuned on
the validation split; nothing here is claimed to be optimal.

Leakage control: the split is done by *scene id* (file number) and, when the
GeoTIFF is georeferenced, by 1°×1° spatial cell so neighbouring crops of the
same scene never straddle train/val. Part III is held out as the test set
exactly as the dataset authors intended. The Yang & Singha 2025 Eastern-Med
dataset (PANGAEA) is used ONLY as an external generalisation test.

Usage:
  python training/train_unet.py --root data/zenodo --epochs 40 --out models/unet_s1_oil.pt
"""
from __future__ import annotations

import argparse, hashlib, json, random, time
from pathlib import Path

import numpy as np
import rasterio
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import segmentation_models_pytorch as smp

DB_LO, DB_HI = -35.0, 5.0
CROP = 256


def cell_key(path: Path) -> str:
    try:
        with rasterio.open(path) as ds:
            b = ds.bounds; return f"{int(np.floor((b.left+b.right)/2))}_{int(np.floor((b.top+b.bottom)/2))}"
    except Exception:
        return "nogeo"


def split_by_group(items, val_frac=0.15, seed=0):
    groups = sorted({g for _, _, g in items}); random.Random(seed).shuffle(groups)
    n_val = max(1, int(len(groups) * val_frac)); val_g = set(groups[:n_val])
    return [i for i in items if i[2] not in val_g], [i for i in items if i[2] in val_g]


class S1Crops(Dataset):
    def __init__(self, items, train: bool, crops_per_image=8, oil_oversample=0.6):
        self.items, self.train, self.n, self.p_oil = items, train, crops_per_image, oil_oversample

    def __len__(self): return len(self.items) * self.n

    def _read(self, img_p, msk_p):
        with rasterio.open(img_p) as d: x = d.read()[:2].astype(np.float32)
        with rasterio.open(msk_p) as d: y = d.read(1).astype(np.float32)
        x = np.nan_to_num(x, nan=DB_LO); x = (np.clip(x, DB_LO, DB_HI) - DB_LO) / (DB_HI - DB_LO)
        return x, (y > 0).astype(np.float32)

    def __getitem__(self, i):
        img_p, msk_p, _ = self.items[i // self.n]
        x, y = self._read(img_p, msk_p)
        H, W = y.shape
        if self.train and y.any() and random.random() < self.p_oil:        # crop around oil pixels (class imbalance)
            r, c = random.choice(np.argwhere(y > 0)); r0 = int(np.clip(r - CROP // 2, 0, H - CROP)); c0 = int(np.clip(c - CROP // 2, 0, W - CROP))
        else:
            r0, c0 = random.randint(0, H - CROP), random.randint(0, W - CROP)
        x, y = x[:, r0:r0+CROP, c0:c0+CROP], y[r0:r0+CROP, c0:c0+CROP]
        if self.train:                                                    # SAR-safe augmentations only
            if random.random() < .5: x, y = x[:, :, ::-1], y[:, ::-1]
            if random.random() < .5: x, y = x[:, ::-1, :], y[::-1, :]
            k = random.randint(0, 3); x, y = np.rot90(x, k, (1, 2)), np.rot90(y, k)
            x = x + np.random.normal(0, 0.01, x.shape).astype(np.float32)   # mild radiometric jitter
            x = x * np.random.uniform(0.95, 1.05)
        return torch.from_numpy(np.ascontiguousarray(x)), torch.from_numpy(np.ascontiguousarray(y))[None]


def dice_bce(logits, y, w_bce=0.5):
    bce = F.binary_cross_entropy_with_logits(logits, y)
    p = torch.sigmoid(logits); inter = (p * y).sum((1, 2, 3)); dice = 1 - (2 * inter + 1) / (p.sum((1, 2, 3)) + y.sum((1, 2, 3)) + 1)
    return w_bce * bce + (1 - w_bce) * dice.mean()


@torch.no_grad()
def evaluate(model, dl, dev, thr=0.5):
    model.eval(); tp = fp = fn = 0
    for x, y in dl:
        p = (torch.sigmoid(model(x.to(dev))) > thr).float().cpu()
        tp += (p * y).sum().item(); fp += (p * (1 - y)).sum().item(); fn += ((1 - p) * y).sum().item()
    iou = tp / max(tp + fp + fn, 1); dice = 2 * tp / max(2 * tp + fp + fn, 1)
    return {"iou": iou, "dice": dice, "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); ap.add_argument("--out", default="models/unet_s1_oil.pt")
    ap.add_argument("--epochs", type=int, default=40); ap.add_argument("--bs", type=int, default=16); ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--encoder", default="resnet18"); ap.add_argument("--patience", type=int, default=8); a = ap.parse_args()
    root = Path(a.root); items = []
    for cls in ("oil", "no_oil", "lookalike"):
        for img in sorted((root / "train_val" / cls / "images").glob("*.tif")):
            msk = root / "train_val" / cls / "masks" / img.name
            if msk.exists(): items.append((img, msk, f"{cls}_{img.stem}_{cell_key(img)}"))
    tr, va = split_by_group(items); print(f"train scenes {len(tr)}  val scenes {len(va)}")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = smp.Unet(a.encoder, encoder_weights="imagenet", in_channels=2, classes=1).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * (len(tr) * 8 // a.bs + 1))
    scaler = torch.amp.GradScaler(enabled=dev == "cuda")
    dl_tr = DataLoader(S1Crops(tr, True), a.bs, shuffle=True, num_workers=4, drop_last=True)
    dl_va = DataLoader(S1Crops(va, False, crops_per_image=4), a.bs, num_workers=4)
    best, bad, hist = -1, 0, []
    for ep in range(a.epochs):
        model.train(); t0 = time.time(); tot = 0
        for x, y in dl_tr:
            x, y = x.to(dev), y.to(dev); opt.zero_grad()
            with torch.autocast(device_type=dev, enabled=dev == "cuda"):
                loss = dice_bce(model(x), y)
            scaler.scale(loss).backward(); scaler.step(opt); scaler.update(); sched.step(); tot += loss.item()
        m = evaluate(model, dl_va, dev); m["loss"] = tot / len(dl_tr); m["epoch"] = ep; hist.append(m)
        print(f"ep {ep:02d} loss {m['loss']:.3f} val IoU {m['iou']:.3f} dice {m['dice']:.3f} P {m['precision']:.3f} R {m['recall']:.3f} {time.time()-t0:.0f}s")
        if m["iou"] > best:
            best, bad = m["iou"], 0
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            torch.save({"model": model.state_dict(), "encoder": a.encoder, "in_channels": 2, "db_clip": [DB_LO, DB_HI],
                        "val_metrics": m, "history": hist}, a.out)
        else:
            bad += 1
            if bad >= a.patience: print("early stop"); break
    # threshold sweep on validation set
    model.load_state_dict(torch.load(a.out, map_location=dev)["model"])
    sweep = {t: evaluate(model, dl_va, dev, t)["iou"] for t in (0.3, 0.4, 0.5, 0.6, 0.7)}
    print("threshold sweep IoU:", sweep, " -> set OSI_SEG_THRESHOLD to the argmax")
    json.dump({"sha256": hashlib.sha256(open(a.out, "rb").read()).hexdigest(), "threshold_sweep": sweep, "best_val_iou": best},
              open(Path(a.out).with_suffix(".json"), "w"), indent=2)


if __name__ == "__main__":
    main()
