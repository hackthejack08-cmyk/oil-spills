"""Build the static-browser result for the bundled measured Sentinel-1 chip."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "demo" / "samples" / "globalosd_diverse20" / "20190616_59.950_23.114.tif"
NAME = "S1A_IW_20190616_140738_analysis"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="osi-real-sar-") as runtime:
        os.environ["OSI_DATA_DIR"] = runtime
        os.environ["OSI_DB_PATH"] = str(Path(runtime) / "osi.sqlite")
        sys.path.insert(0, str(ROOT / "backend"))
        from app import db, pipeline

        db.init()
        investigation = pipeline.create_investigation("Sentinel-1 measured sample", "real")
        result = pipeline.analyze_scene(
            investigation["id"], SAMPLE, sensing_time="2019-06-16T14:07:38Z"
        )
        probability = Path(runtime) / "renders" / Path(result["prob_overlay"]).name
        result["quicklook"] = "samples/S1A_IW_20190616_140738_quicklook.png"
        result["prob_overlay"] = f"samples/{NAME}_prob.png"
        payload = json.dumps(result, separators=(",", ":"))

        for destination in (ROOT / "frontend" / "static" / "samples", ROOT / "public-demo" / "samples"):
            (destination / f"{NAME}.json").write_text(payload, encoding="utf-8")
            shutil.copyfile(probability, destination / f"{NAME}_prob.png")

    print(f"Exported {len(result['detections'])} detections from the measured SAR sample.")


if __name__ == "__main__":
    main()
