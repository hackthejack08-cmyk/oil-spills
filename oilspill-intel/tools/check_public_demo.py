"""Fast, dependency-free checks for the static SIH judge build."""

from pathlib import Path
from html.parser import HTMLParser
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-demo"


def main() -> None:
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert "<title>CodeCracker Dragons | Oil-spill monitoring</title>" in html, "public branding is stale"
    app = (PUBLIC / "app.js").read_text(encoding="utf-8")
    live_api = (PUBLIC / "api" / "live-scenes.js").read_text(encoding="utf-8")
    required_ids = {
        "casePicker", "stageList", "btnRunReplay", "btnReplayPause",
        "btnReplayRestart", "replaySpeed", "btnReceiveSample", "btnReceiveMap",
        "acquisitionProgress", "chkEnhanced", "chkSlick",
        "chkDriftLayers", "chkAisLayers", "btnExport",
        "liveFeed", "liveScenePreview", "liveOpticalPreview", "liveNasaPreview", "liveArchive", "liveOptical", "liveNasa",
        "responseBrief", "briefFacts", "briefChecks", "briefText", "btnCopyBrief", "btnDownloadBrief",
    }
    missing = sorted(element_id for element_id in required_ids if f'id="{element_id}"' not in html)
    assert not missing, f"missing public controls: {', '.join(missing)}"
    assert "window.OSI_PUBLIC_DEMO=true" in html, "public mode is not enabled before app.js"
    assert '<script src="demo-data.js"></script>' not in html, "large case data must load only when replay starts"
    assert "startHistoricalReplay" in app, "deterministic replay is not wired"
    assert (PUBLIC / "demo-data.js").is_file(), "bundled replay data is missing"
    assert (PUBLIC / "samples" / "OSI_sample_20250314.tif").is_file(), "judge sample is missing"
    assert (PUBLIC / "samples" / "S1A_IW_20190616_140738_real_sample.tif").is_file(), "real SAR sample is missing"
    assert (PUBLIC / "samples" / "S1A_IW_20190616_140738_quicklook.png").is_file(), "SAR quicklook is missing"
    assert (PUBLIC / "samples" / "S1A_IW_20190616_140738_analysis.json").is_file(), "published SAR analysis is missing"
    assert (PUBLIC / "samples" / "S1A_IW_20190616_140738_analysis_prob.png").is_file(), "SAR score overlay is missing"
    validation_pack = PUBLIC / "samples" / "OSI_judge_validation_pack.zip"
    assert validation_pack.is_file() and validation_pack.stat().st_size > 20_000_000, "judge validation pack is missing or incomplete"
    with zipfile.ZipFile(validation_pack) as archive:
        names = archive.namelist()
        assert "README.md" in names and "manifest.csv" in names, "validation pack documentation is missing"
        assert len([name for name in names if name.startswith("positive_oil/") and name.endswith(".tif")]) == 5, "positive SAR examples are incomplete"
        assert len([name for name in names if name.startswith("negative_lookalikes/") and name.endswith(".tif")]) == 5, "negative SAR examples are incomplete"
    assert "btnAnalyze\", \"btnDrift" not in app, "public SAR analysis button is disabled"
    assert "S1A_IW_20190616_140738_analysis.json" in app, "public SAR analysis is not wired"
    assert 'data-page="evaluation"' in html and "How many labelled oil scenes were found?" in html, "evaluation page is missing"
    assert "Check slick" in app and "Possible spills" in app, "simple public workflow is missing"
    assert "sentinel-1-grd" in live_api and "sentinel-2-l2a" in live_api, "live radar and optical catalogue checks are missing"
    assert "gibs.earthdata.nasa.gov" in live_api and "VIIRS_NOAA21" in live_api, "NASA GIBS context is missing"
    assert "Near-real-time Sentinel-1" in html, "live catalogue status is missing"
    assert "loaded automatically" in html and "matched automatically" in html, "automatic input guidance is missing"
    assert "d.oil_likelihood >= 0.5" in app and "if (!window.OSI_PUBLIC_DEMO)" in app, "simple map filtering is missing"
    assert 'href="samples/OSI_judge_validation_pack.zip"' in html, "validation pack download is not linked"
    assert (ROOT / "frontend" / "static" / "app.js").read_bytes() == (PUBLIC / "app.js").read_bytes(), "public app.js is stale"
    assert (ROOT / "frontend" / "static" / "app.css").read_bytes() == (PUBLIC / "app.css").read_bytes(), "public app.css is stale"
    assert '<script src="response-brief.js"></script>' in html, "response brief helper is not loaded"
    assert (ROOT / "frontend" / "static" / "response-brief.js").read_bytes() == (PUBLIC / "response-brief.js").read_bytes(), "public response brief is stale"
    assert 'S.inv.id !== REAL_SAR_SAMPLE.id' in app, "historical analysis must not be attached to a different live scene"
    document_path = PUBLIC / "technical-documentation.html"
    assert 'href="technical-documentation.html"' in html, "technical documentation is not linked"
    assert document_path.read_bytes() == (ROOT / "frontend" / "static" / document_path.name).read_bytes(), "public documentation is stale"
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids, self.hrefs = set(), []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.add(attrs["id"])
            if tag == "a":
                self.hrefs.append(attrs.get("href", ""))
    document = Links()
    document.feed(document_path.read_text(encoding="utf-8"))
    for href in document.hrefs:
        if href.startswith("#"):
            assert href[1:] in document.ids, f"broken document section: {href}"
        elif ":" not in href:
            assert (PUBLIC / href).exists(), f"missing documentation download: {href}"
    print("Public demo check passed: controls, evaluation pack, assets, and source sync are valid.")


if __name__ == "__main__":
    main()
