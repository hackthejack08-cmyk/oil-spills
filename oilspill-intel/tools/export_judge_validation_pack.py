"""Create the small, labelled real-SAR pack linked from the public demo."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "demo" / "samples" / "globalosd_diverse20"
POSITIVE = [
    "20160611_-90.935_0.409.tif",
    "20200712_104.284_5.807.tif",
    "20191207_91.453_21.264.tif",
    "20181211_120.428_-5.693.tif",
    "20190616_59.950_23.114.tif",
]
NEGATIVE = [
    "20180722_12.365_-8.231.tif",
    "20171109_-96.165_26.498.tif",
    "20180828_49.893_28.706.tif",
    "20200327_36.993_42.466.tif",
    "20170716_141.945_53.949.tif",
]
README = """# OSI judge validation pack

This pack contains 10 historic measured Sentinel-1 GeoTIFF chips: five GlobalOSD-SAR oil-positive point labels and five look-alike negatives. `manifest.csv` records each source label and the current baseline result at threshold 0.50.

Use this pack to demonstrate both successful cases and failures. A positive label is research-dataset evidence, not legal proof of an incident or responsible vessel. These are point labels, not complete pixel masks, so this pack measures screening recall and false alarms—not segmentation IoU.

Source labels: GlobalOSD-SAR, DOI 10.5281/zenodo.15286918, CC BY 4.0. Pixel chips were streamed from Microsoft Planetary Computer and calibrated to sigma-zero by this repository.
"""


def nearest_row(rows: list[dict], filename: str, label: str) -> dict:
    date, lon, lat = filename.removesuffix(".tif").split("_")
    candidates = [row for row in rows if row["date"] == date and row["label"] == label and row["status"] == "ok"]
    return min(candidates, key=lambda row: (float(row["lon"]) - float(lon)) ** 2 + (float(row["lat"]) - float(lat)) ** 2)


def main() -> None:
    rows = list(csv.DictReader((SOURCE / "expected_results.csv").open(encoding="utf-8")))
    manifest = io.StringIO(newline="")
    writer = csv.writer(manifest)
    writer.writerow(["file", "source_label", "label_lat", "label_lon", "baseline_score", "prediction_at_0.50", "result"])
    selected = [(name, "oil") for name in POSITIVE] + [(name, "lookalike") for name in NEGATIVE]
    for filename, label in selected:
        row = nearest_row(rows, filename, label)
        predicted = "oil_candidate" if float(row["score"] or 0) >= 0.5 else "no_oil_candidate"
        outcome = {("oil", "oil_candidate"): "true_positive", ("oil", "no_oil_candidate"): "false_negative",
                   ("lookalike", "oil_candidate"): "false_positive", ("lookalike", "no_oil_candidate"): "true_negative"}[label, predicted]
        writer.writerow([filename, label, row["lat"], row["lon"], row["score"], predicted, outcome])

    output = ROOT / "frontend" / "static" / "samples" / "OSI_judge_validation_pack.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("README.md", README)
        archive.writestr("manifest.csv", manifest.getvalue())
        archive.writestr("benchmark_summary.json", json.dumps(json.loads((SOURCE / "benchmark_summary.json").read_text()), indent=2))
        for filename, label in selected:
            folder = "positive_oil" if label == "oil" else "negative_lookalikes"
            archive.write(SOURCE / filename, f"{folder}/{filename}")

    public = ROOT / "public-demo" / "samples" / output.name
    public.write_bytes(output.read_bytes())
    print(f"Created {output.name}: {output.stat().st_size / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
