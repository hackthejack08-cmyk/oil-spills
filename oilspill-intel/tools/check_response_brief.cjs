/* Run: node tools/check_response_brief.cjs — no dependencies or remote data. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { buildResponseBrief } = require("../frontend/static/response-brief.js");
const raw = fs.readFileSync(path.join(__dirname, "../public-demo/demo-data.js"), "utf8");
const data = JSON.parse(raw.split("window.OSI_DEMO_DATA=")[1].trim().replace(/;$/, "")).case;
const detections = data.scene.detections.filter(d => d.oil_likelihood >= 0.5);
const state = { inv: data.investigation, scene: data.scene, detection: detections.find(d => d.id === data.drift.detection_id), drift: data.drift, ais: data.ais };
const options = { detections, now: Date.parse("2026-09-25T18:00:00.123Z"), publicDemo: true };
const brief = buildResponseBrief(state, options);
assert.match(brief.text, /Prepared: 2026-09-25 18:00:00 UTC/);
assert.match(brief.basis, /synthetic/);
assert.match(brief.text, /1 candidate for review.*14\.20 km²/);
assert.match(brief.text, /6\.0 h from image time/);
assert.match(brief.text, /419000101.*75\/100 \(uncalibrated\)/);
assert.match(brief.text, /39/);
assert.match(brief.text, /do not use this case to dispatch/);
assert.equal(buildResponseBrief({ scene: null }), null);

// A preview or a different selection must never inherit the old case's estimates.
const preview = buildResponseBrief({ ...state, inv: { id: "live" }, scene: { sensing_time: "2026-09-25T17:00:00Z", bbox: [70, 19, 71, 20], detector: "not processed" } }, options);
assert.match(preview.text, /Not analysed/);
assert.match(preview.text, /1 hours old/);
assert.doesNotMatch(preview.text, /419000101|6\.0 h|14\.20/);
const changed = buildResponseBrief({ ...state, detection: { id: "other" } }, options);
assert.doesNotMatch(changed.text, /419000101|6\.0 h|14\.20/);
const mismatchedRun = buildResponseBrief({ ...state, ais: { ...state.ais, drift_run_id: "different" } }, options);
assert.doesNotMatch(mismatchedRun.text, /419000101/);
const noResult = buildResponseBrief({ scene: { detector: "baseline", detections: [] } }, options);
assert.match(noResult.text, /Acquisition time is missing/);
assert.match(noResult.text, /Location unavailable/);
const negative = buildResponseBrief({ scene: { detector: "baseline", detections: [] } });
assert.match(negative.text, /does not establish oil-free water/);

const historical = buildResponseBrief({ ...state, inv: { id: "historical" }, scene: { ...state.scene, synthetic: false },
  drift: { ...state.drift, forcing_source: {} }, ais: { ...state.ais, synthetic: false } }, options);
assert.match(historical.text, /forecast period has ended/);
assert.match(historical.basis, /Historical image/);
const future = buildResponseBrief({ scene: { detector: "not processed", sensing_time: "2030-01-01T00:00:00Z" } }, options);
assert.match(future.text, /Acquisition time is in the future/);
console.log("Response brief checks passed: real case, filtered count, dates, provenance, missing results, and stale-result isolation.");
