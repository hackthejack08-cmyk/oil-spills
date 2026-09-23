"""Build the static, read-only SIH judge demo from a verified local investigation."""

from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
CASE_ID = "inv_7d65e77749"
OUT = ROOT / "public-demo"


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=30) as response:
        return json.load(response)


def main() -> None:
    case = get(f"/api/investigations/{CASE_ID}")
    evidence = get(f"/api/evidence/{CASE_ID}")
    status = get("/api/data/status")
    health = get("/api/health")
    vessels = {
        str(candidate["mmsi"]): get(
            f"/api/vessels/{candidate['mmsi']}?investigation_id={CASE_ID}"
        )
        for candidate in case["ais"]["candidates"]
    }

    project_link = None
    project_file = OUT / ".vercel" / "project.json"
    if project_file.exists():
        project_link = project_file.read_text(encoding="utf-8")
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(ROOT / "frontend" / "static", OUT)
    if project_link:
        project_file.parent.mkdir()
        project_file.write_text(project_link, encoding="utf-8")
    (OUT / "data").mkdir()
    (OUT / "renders").mkdir()
    (OUT / "samples").mkdir()
    shutil.copy2(ROOT / "demo" / "data" / "ne_110m_coastline.geojson", OUT / "data" / "coastline.geojson")
    shutil.copy2(ROOT / "demo" / "data" / "synthetic_s1_scene.tif", OUT / "samples" / "OSI_sample_20250314.tif")

    for key in ("quicklook", "prob_overlay"):
        source_url = case["scene"].get(key)
        if not source_url:
            continue
        filename = Path(source_url).name
        shutil.copy2(ROOT / "backend" / "runtime" / "renders" / filename, OUT / "renders" / filename)
        case["scene"][key] = f"renders/{filename}"

    health.update(
        {
            "status": "public-demo",
            "db": "read-only bundled evidence",
            "offline": False,
            "land_mask_available": True,
        }
    )
    for connector in status["connectors"]:
        if connector["id"] == "hycom":
            connector["name"] = "Ocean currents – HYCOM GOFS/ESPC (NCSS)"
            connector["note"] = "NetCDF subset service; live connector verified 2026-09-19"

    payload = {
        "case": case,
        "vessels": vessels,
        "evidence": evidence,
        "status": status,
        "health": health,
    }
    (OUT / "demo-data.js").write_text(
        "window.OSI_PUBLIC_DEMO=true;window.OSI_DEMO_DATA="
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )

    index = (OUT / "index.html").read_text(encoding="utf-8")
    index = index.replace(
        '<script src="app.js"></script>',
        '<script src="demo-data.js"></script>\n<script src="app.js"></script>',
    )
    (OUT / "index.html").write_text(index, encoding="utf-8")
    (OUT / "vercel.json").write_text(
        json.dumps(
            {
                "public": True,
                "cleanUrls": True,
                "headers": [
                    {
                        "source": "/(.*)",
                        "headers": [
                            {"key": "X-Content-Type-Options", "value": "nosniff"},
                            {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
                        ],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Built {OUT} ({(OUT / 'demo-data.js').stat().st_size:,} bytes of case data)")


if __name__ == "__main__":
    main()
