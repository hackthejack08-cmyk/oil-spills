"""Run the external POSEatSea five-class checkpoint on JPG/PNG images.

This is a compatibility/evaluation tool, not the active OSI GeoTIFF detector.
The checkpoint expects RGB images resized to exactly 512x512 and predicts:
sea, oil, look-alike, ship and land.

Example:
  python training/infer_poseatsea.py ../demo/samples/pretrained_reference
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image
import segmentation_models_pytorch as smp

CLASSES = ("sea", "oil", "lookalike", "ship", "land")
COLORS = np.array(((0, 0, 0), (0, 255, 255), (255, 0, 0), (153, 76, 0), (0, 153, 0)), dtype=np.uint8)


def load_model(weights: Path):
    model = smp.Unet(encoder_name="mit_b2", encoder_weights=None, in_channels=3, classes=5)
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True), strict=True)
    return model.eval()


def input_files(path: Path):
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"} and "_mask" not in p.stem)


def run(model, path: Path, output_dir: Path):
    source = Image.open(path).convert("RGB")
    resized = source.resize((512, 512), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(array.transpose(2, 0, 1)).unsqueeze(0)
    with torch.inference_mode():
        mask_512 = model(tensor).argmax(1)[0].numpy().astype(np.uint8)
    mask = Image.fromarray(mask_512).resize(source.size, Image.Resampling.NEAREST)
    labels = np.asarray(mask)
    output_dir.mkdir(parents=True, exist_ok=True)
    mask.save(output_dir / f"{path.stem}_classes.png")
    Image.fromarray(COLORS[labels]).save(output_dir / f"{path.stem}_prediction.png")
    total = labels.size
    return {name: round(float((labels == i).sum() / total), 6) for i, name in enumerate(CLASSES)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JPG/PNG file or folder")
    parser.add_argument("--weights", type=Path, default=Path("models/poseatsea_mitb2_5class.pth"))
    parser.add_argument("--out", type=Path, default=Path("runtime/poseatsea_predictions"))
    args = parser.parse_args()
    files = input_files(args.input)
    if not files:
        raise SystemExit("No JPG/PNG inputs found.")
    model = load_model(args.weights)
    result = {str(path): run(model, path, args.out) for path in files}
    (args.out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"outputs": str(args.out), "images": len(files)}, indent=2))


if __name__ == "__main__":
    main()
