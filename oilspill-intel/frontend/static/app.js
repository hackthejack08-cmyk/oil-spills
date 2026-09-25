/* OSI dashboard – vanilla JS + Leaflet (BSD-2). The base map is bundled for reliable offline use. */
const S = { inv: null, scene: null, detection: null, drift: null, ais: null, selectedVessel: null, timeline: [] };
window.S = S;
const $ = (q) => document.querySelector(q);
const esc = (x) => String(x ?? "").replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
const PIPELINE_STAGES = [
  ["acquisition", "New scenes"], ["preprocessing", "SAR processed"], ["detection", "Slicks detected"],
  ["drift", "Drift forecast"], ["ais", "Vessel correlation"], ["evidence", "Alerts"],
];
const reviewDetections = (scene) => (scene?.detections || []).filter((d) => !window.OSI_PUBLIC_DEMO || d.oil_likelihood >= 0.5);
const REAL_SAR_SAMPLE = {
  id: "S1A_IW_GRDH_1SDV_20190616T140738_20190616T140803_027706_03209B",
  start: "2019-06-16T14:07:38Z",
  stop: "2019-06-16T14:08:03Z",
  durationS: 25,
  bbox: [59.45519693247744, 22.671491235118914, 60.51277339248815, 23.723201581643035],
  quicklook: "samples/S1A_IW_20190616_140738_quicklook.png",
};
const LIVE_FEED = { scene: null, recentScenes: 0, optical: null };
const ageLabel = (value) => {
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000));
  return days < 1 ? "Published today" : days === 1 ? "1 day ago" : `${days} days ago`;
};
async function loadLiveCatalogue() {
  const feed = $("#liveFeed");
  if (!feed) return;
  try {
    const endpoint = window.OSI_PUBLIC_DEMO ? "/api/live-scenes" : "/api/data/live-scenes";
    const response = await fetch(endpoint, { cache: "no-store" });
    if (!response.ok) throw new Error(`catalogue returned HTTP ${response.status}`);
    const snapshot = await response.json();
    const radar = snapshot.radar || [];
    if (!radar.length) throw new Error("no Sentinel-1 IW scenes found in the last 120 days");
    const latest = radar[0];
    const properties = latest.properties || {};
    const preview = latest.assets?.rendered_preview?.href || latest.assets?.thumbnail?.href;
    LIVE_FEED.scene = {
      id: latest.id, start: properties.datetime, durationS: 25, bbox: latest.bbox,
      quicklook: preview, platform: String(properties.platform || "Sentinel-1").replace("sentinel-", "Sentinel-"),
      orbit: properties["sat:orbit_state"] || "unknown", sourceLabel: "Live catalogue",
    };
    LIVE_FEED.recentScenes = radar.length;
    feed.classList.add("connected");
    $("#liveSceneAge").textContent = ageLabel(properties.datetime);
    $("#liveSceneTitle").textContent = `${LIVE_FEED.scene.platform} · ${LIVE_FEED.scene.orbit} orbit`;
    $("#liveSceneMeta").textContent = `${new Date(properties.datetime).toLocaleString([], { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC · Mumbai coast`;
    $("#liveArchive").textContent = `Archive · ${radar.length} recent scenes`;
    if (preview) {
      $("#liveScenePreview").src = preview; $("#liveSceneOpen").href = preview; $("#liveSceneOpen").hidden = false;
    }
    LIVE_FEED.optical = snapshot.optical?.[0] || null;
    let opticalVisible = false;
    if (LIVE_FEED.optical) {
      const opticalProperties = LIVE_FEED.optical.properties || {};
      const opticalPreview = LIVE_FEED.optical.assets?.rendered_preview?.href || LIVE_FEED.optical.assets?.thumbnail?.href;
      const gapDays = Math.abs(new Date(opticalProperties.datetime) - new Date(properties.datetime)) / 86400000;
      $("#liveOptical").textContent = `EO companion · ${gapDays.toFixed(1)} d · ${Number(opticalProperties["eo:cloud_cover"] || 0).toFixed(0)}% cloud`;
      if (opticalPreview) {
        $("#liveOpticalPreview").src = opticalPreview; $("#liveOpticalOpen").href = opticalPreview;
        $("#liveOpticalOpen").hidden = false; opticalVisible = true;
      }
    } else {
      $("#liveOptical").textContent = "EO companion · inconclusive";
    }
    const nasa = snapshot.nasa;
    let nasaVisible = false;
    if (nasa?.image_url) {
      const nasaImage = $("#liveNasaPreview");
      nasaImage.onerror = () => {
        if (nasa.fallback_image_url && nasaImage.src !== nasa.fallback_image_url) {
          nasaImage.src = nasa.fallback_image_url; $("#liveNasa").textContent = `NASA NRT · ${nasa.fallback_date}`;
        }
      };
      nasaImage.src = nasa.image_url; $("#liveNasaOpen").href = nasa.image_url; $("#liveNasaOpen").hidden = false;
      $("#liveNasa").textContent = `NASA NRT · ${nasa.date}`; nasaVisible = true;
    } else {
      $("#liveNasa").textContent = "NASA NRT · unavailable";
    }
    $("#liveScenePair").classList.toggle("with-nasa", opticalVisible && nasaVisible);
    $("#liveScenePair").classList.toggle("paired", opticalVisible !== nasaVisible);
    $("#liveSceneNote").textContent = window.OSI_PUBLIC_DEMO
      ? "Live SAR, matched EO and NASA VIIRS context. The verified replay below demonstrates detection, drift and AIS attribution."
      : "The service streams SAR for detection; EO and NASA VIIRS remain supporting context before drift and AIS review.";
    $("#btnReceiveSample").textContent = "View latest real radar image";
    $("#btnReceiveMap").textContent = "View latest real radar image";
    if (window.OSI_PUBLIC_DEMO) $("#health").textContent = "● Live catalogue connected";
  } catch (error) {
    $("#liveSceneAge").textContent = "Unavailable";
    $("#liveSceneTitle").textContent = "Satellite catalogue could not be reached";
    $("#liveSceneMeta").textContent = "The verified offline replay is still available.";
    $("#liveArchive").textContent = "Archive · unavailable"; $("#liveOptical").textContent = "EO · unavailable"; $("#liveNasa").textContent = "NASA · unavailable";
    $("#liveSceneNote").textContent = error.message;
  }
}
const activeReceptionSample = () => LIVE_FEED.scene?.quicklook && LIVE_FEED.scene?.bbox ? LIVE_FEED.scene : REAL_SAR_SAMPLE;
const stageState = Object.fromEntries(PIPELINE_STAGES.map(([id]) => [id, "waiting"]));
const replay = { result: null, index: -1, timer: null, frame: null, paused: false, active: false, kind: null, elapsedMs: 0, lastFrame: 0 };
function notify(message, error = false) {
  $("#noticeText").textContent = message; $("#notice").hidden = false;
  $("#notice").classList.toggle("error", error);
}
$("#dismissNotice").onclick = () => $("#notice").hidden = true;
function renderStageStatus() {
  const groups = window.OSI_PUBLIC_DEMO ? [
    [["acquisition", "preprocessing"], "Receive SAR"], [["detection"], "Check slick"],
    [["drift"], "Wind + drift"], [["ais"], "Match AIS"], [["evidence"], "Evidence"],
  ] : PIPELINE_STAGES.map(([id, label]) => [[id], label]);
  const groupState = (ids) => {
    const states = ids.map((id) => stageState[id]);
    return states.includes("failed") ? "failed" : states.includes("running") ? "running" : states.includes("partial") ? "partial" : states.every((state) => state === "complete") ? "complete" : "waiting";
  };
  $("#stageList").innerHTML = groups.map(([ids, label]) => { const status = groupState(ids); return `<li class="${status}" aria-label="${esc(label)}: ${status}">${esc(label)}</li>`; }).join("");
  const values = Object.values(stageState);
  $("#systemValue").textContent = values.includes("failed") ? "Review required" : values.includes("running") ? "Processing" : values.includes("partial") ? "Partial" : values.every((v) => v === "complete") ? "Complete" : "Ready";
  if ($("#operationState")) $("#operationState").textContent = $("#systemValue").textContent;
}
function setStage(id, status) { stageState[id] = status; renderStageStatus(); }
function resetStages() { PIPELINE_STAGES.forEach(([id]) => { stageState[id] = "waiting"; }); $("#acquisitionProgress").style.width = "0%"; renderStageStatus(); }
function completeAvailableStages() {
  if (S.scene) { setStage("acquisition", "complete"); setStage("preprocessing", "complete"); setStage("detection", S.scene.detections?.length ? "complete" : "partial"); }
  if (S.drift) setStage("drift", "complete");
  if (S.ais) { setStage("ais", "complete"); setStage("evidence", "complete"); }
}

let demoDataPromise;
function loadPublicDemoData() {
  if (window.OSI_DEMO_DATA) return Promise.resolve(window.OSI_DEMO_DATA);
  if (!demoDataPromise) demoDataPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script"); script.src = "demo-data.js";
    script.onload = () => window.OSI_DEMO_DATA ? resolve(window.OSI_DEMO_DATA) : reject(new Error("Public demo data is incomplete."));
    script.onerror = () => reject(new Error("Public demo data could not be loaded. Check the connection and retry."));
    document.head.appendChild(script);
  });
  return demoDataPromise;
}
const publicDemoApi = async (path) => {
  if (path === "/api/health") return { status: "public-demo", cnn_weights: false, detector: "baseline-adaptive-threshold", land_mask_available: true, mode: "static replay" };
  if (path === "/api/data/jobs" || path === "/api/uploads") return [];
  const data = await loadPublicDemoData();
  if (path === "/api/demo/run") return data.case;
  if (path === "/api/data/status") return data.status;
  if (path === "/api/investigations") return [data.case.investigation];
  if (path.startsWith("/api/investigations/")) return data.case;
  if (path.startsWith("/api/vessels/")) return data.vessels[path.split("/")[3].split("?")[0]];
  if (path.startsWith("/api/evidence/")) return data.evidence;
  throw new Error("This action needs the full analysis service.");
};
const api = async (path, body, method) => {
  if (window.OSI_PUBLIC_DEMO) return publicDemoApi(path);
  let r;
  try { r = await fetch(path, body ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}); }
  catch { throw new Error("Cannot reach the analysis service. Check that the server or Docker container is running, then try again."); }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || r.statusText);
    throw new Error(message);
  }
  return r.json();
};
const fmt = (x, d = 2) => (x == null ? "–" : Number(x).toFixed(d));
const log = (msg) => { S.timeline.push(`${new Date().toISOString().slice(11, 19)}Z  ${msg}`); $("#timeline").innerHTML = S.timeline.map((t) => `<li>${esc(t)}</li>`).join(""); };

/* ---------------- map ---------------- */
const map = L.map("map", { zoomControl: true }).setView([19.4, 70.6], 9);
new ResizeObserver(() => map.invalidateSize()).observe($("#map"));
L.control.scale({ imperial: false }).addTo(map);
const layers = {
  coast: L.geoJSON(null, { style: { color: "#607780", weight: 1.4 } }).addTo(map),
  graticule: L.layerGroup().addTo(map),
  scene: null, prob: null,
  slick: L.geoJSON(null, { style: { color: "#ff5a3c", weight: 2, fillOpacity: 0.25 } }).addTo(map),
  ellipses: L.layerGroup().addTo(map), back: L.layerGroup().addTo(map), fwd: L.layerGroup().addTo(map),
  vectors: L.layerGroup().addTo(map), tracks: L.layerGroup().addTo(map), positions: L.layerGroup().addTo(map), gaps: L.layerGroup().addTo(map),
};
fetch(window.OSI_PUBLIC_DEMO ? "data/coastline.geojson" : "/api/demo/coastline").then((r) => r.json()).then((g) => layers.coast.addData(g)).catch(() => {});
for (let lat = -80; lat <= 80; lat += 1) L.polyline([[lat, -180], [lat, 180]], { color: "#9baeb5", weight: 0.5, opacity: 0.55, interactive: false }).addTo(layers.graticule);
for (let lon = -180; lon <= 180; lon += 1) L.polyline([[-85, lon], [85, lon]], { color: "#9baeb5", weight: 0.5, opacity: 0.55, interactive: false }).addTo(layers.graticule);
const legend = (rows) => ($("#legend").innerHTML = rows.map(([c, t]) => `<div><span class="sw" style="background:${c}"></span>${t}</div>`).join(""));
legend(window.OSI_PUBLIC_DEMO ? [["#ff5a3c", "Oil candidate"], ["#3ec5ff", "Possible origin path"], ["#39d98a", "Forecast path"], ["#c8d3ea", "Vessel track"]] : [["#ff5a3c", "Suspected slick"], ["#ffb648", "Model origin 50 / 90% ellipse"], ["#3ec5ff", "Hindcast"], ["#39d98a", "Forecast"], ["#c8d3ea", "Vessel track"], ["#ff3cf0", "AIS gap (dashed)"]]);

/* ---------------- nav ---------------- */
document.querySelectorAll("#nav button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#nav button, .page").forEach((e) => e.classList.remove("active"));
  document.querySelectorAll("#nav button").forEach((e) => e.removeAttribute("aria-current"));
  b.setAttribute("aria-current", "page"); $("#sidebar").scrollTop = 0;
  b.classList.add("active"); $(`#page-${b.dataset.page}`).classList.add("active"); setTimeout(() => map.invalidateSize(), 50);
});
const goto = (p) => document.querySelector(`#nav button[data-page=${p}]`).click();

/* ---------------- health ---------------- */
api("/api/health").then((h) => { $("#health").textContent = window.OSI_PUBLIC_DEMO ? "● Demo ready" : `● API online · ${h.cnn_weights ? "weights present" : "baseline detector"}`; $("#healthBox").textContent = JSON.stringify(h, null, 1); $("#landWarning").hidden = h.land_mask_available !== false; })
  .catch(() => ($("#health").textContent = "● API offline"));

/* ---------------- uploads ---------------- */
async function upload(kind, file, sel) {
  if (!file) return;
  const fd = new FormData(); fd.append("kind", kind); fd.append("file", file);
  let r;
  try { r = await fetch("/api/satellite/upload", { method: "POST", body: fd }); }
  catch { throw new Error("Upload could not reach the analysis service. Check that the server or Docker container is running."); }
  const j = await r.json().catch(() => ({ detail: `Upload failed with HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.detail);
  const o = document.createElement("option"); o.value = j.upload_id; o.textContent = `${file.name} (${(j.bytes / 1e6).toFixed(1)} MB, sha ${j.sha256.slice(0, 8)}…)`; sel.appendChild(o); sel.value = j.upload_id;
  log(`uploaded ${kind} ${file.name} sha256=${j.sha256.slice(0, 12)}`);
}
$("#sceneFile").onchange = (e) => { if (e.target.files[0]) notify(`${e.target.files[0].name} selected. Press “Analyse selected image” to upload and run it.`); };
$("#aisFile").onchange = (e) => upload("ais", e.target.files[0], $("#aisSel")).catch((x) => notify(x.message, true));
$("#forcingFile").onchange = (e) => upload("forcing", e.target.files[0], $("#forcingSel")).catch((x) => notify(x.message, true));

/* ---------------- stage 1: investigation + scene ---------------- */
async function ensureInv() {
  if (S.inv) return S.inv;
  S.inv = await api("/api/investigations", { name: "Investigation " + new Date().toISOString().slice(0, 16), mode: $("#sceneSel").value === "demo" ? "demo" : "real" });
  log(`investigation ${S.inv.id} created`); return S.inv;
}
async function analyzeScene() {
  $("#btnAnalyze").disabled = true;
  resetStages(); setStage("acquisition", "running");
  try {
    if (window.OSI_PUBLIC_DEMO) {
      let scene;
      if (S.inv?.mode === "sar-reception") {
        const response = await fetch("samples/S1A_IW_20190616_140738_analysis.json");
        if (!response.ok) throw new Error("The published SAR analysis result could not be loaded.");
        scene = await response.json();
      } else {
        const result = await api("/api/demo/run", {});
        S.inv = result.investigation; scene = result.scene;
      }
      clearDownstream(); S.scene = scene; S.detection = null;
      renderScene(); log(`published baseline analysis loaded → ${scene.detections.length} candidate(s)`);
      setStage("acquisition", "complete"); setStage("preprocessing", "complete");
      setStage("detection", scene.detections.length ? "complete" : "partial");
      notify(`${reviewDetections(scene).length} oil candidate(s) require review.`);
      return;
    }
    const pending = $("#sceneFile").files[0];
    if (pending) await upload("scene", pending, $("#sceneSel"));
    await ensureInv(); notify("Analysing radar pixels. This may take a moment…");
    const wind = $("#windIn").value ? Number($("#windIn").value) : null;
    const scene = await api("/api/satellite/analyze", { investigation_id: S.inv.id, scene: $("#sceneSel").value, wind_ms: wind });
    resetResults(); S.scene = scene;
    renderScene(); log(`scene analysed by ${S.scene.detector} in ${S.scene.processing_s}s → ${S.scene.detections.length} object(s)`);
    setStage("acquisition", "complete"); setStage("preprocessing", "complete"); setStage("detection", scene.detections.length ? "complete" : "partial");
    notify(`${scene.detections.length} suspected object(s) detected. Review before running drift.`); refreshInvestigations();
  } catch (e) { setStage("acquisition", "failed"); notify(e.message, true); } finally { $("#btnAnalyze").disabled = false; }
}
function directionFromTrack(track) {
  if (!track || track.length < 2) return null;
  const [lon1, lat1] = track[0], [lon2, lat2] = track.at(-1);
  const east = (lon2 - lon1) * Math.cos((lat1 + lat2) * Math.PI / 360);
  const degrees = (Math.atan2(east, lat2 - lat1) * 180 / Math.PI + 360) % 360;
  const cardinal = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(degrees / 45) % 8];
  return { degrees, cardinal };
}
function renderContext() {
  const panel = $("#contextPanel"), s = S.scene;
  if (!panel || !s) { if (panel) panel.hidden = true; return; }
  const m = s.metadata || {}, b = s.bbox || [];
  const lon = m.centre?.lon ?? (b.length === 4 ? (b[0] + b[2]) / 2 : null);
  const lat = m.centre?.lat ?? (b.length === 4 ? (b[1] + b[3]) / 2 : null);
  const location = m.location_label || (lat == null ? "Unavailable" : `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"}`);
  const e = S.drift?.environment || {};
  const forecast = e.forecast_toward ? { cardinal: e.forecast_toward, degrees: e.forecast_toward_deg } : directionFromTrack(S.drift?.forward?.centre_track);
  const windSpeed = e.wind_speed_ms ?? S.drift?.wind_at_slick_ms;
  const wind = windSpeed != null ? `${fmt(windSpeed, 1)} m/s${e.wind_from ? ` · from ${esc(e.wind_from)} (${fmt(e.wind_from_deg, 0)}°)` : " · direction not stored in this replay"}` : "Fetched after a slick is selected";
  const current = e.current_speed_ms != null ? `${fmt(e.current_speed_ms, 2)} m/s · toward ${esc(e.current_toward || "–")} (${fmt(e.current_toward_deg, 0)}°)` : S.drift ? "Applied by model · vector not stored in this replay" : "Fetched after a slick is selected";
  panel.hidden = false;
  panel.innerHTML = `<h3>Automatic image context</h3><div class="context-grid">
    <div><span>Location</span><b>${esc(location)}</b></div><div><span>Image time</span><b>${esc(s.sensing_time || "Missing metadata")}</b></div>
    <div><span>Satellite / image</span><b>${esc(m.platform || s.platform || "Uploaded SAR GeoTIFF")}${m.orbit_pass && m.orbit_pass !== "unknown" ? " · " + esc(m.orbit_pass) : ""}</b></div>
    <div><span>Footprint</span><b>${b.length === 4 ? `${fmt(b[0], 3)}, ${fmt(b[1], 3)} → ${fmt(b[2], 3)}, ${fmt(b[3], 3)}` : "Unavailable"}</b></div>
    <div><span>Wind at slick</span><b>${wind}</b></div><div><span>Surface current</span><b>${current}</b></div>
    <div><span>Forecast direction</span><b>${forecast ? `${esc(forecast.cardinal)} · ${fmt(forecast.degrees, 0)}°` : "Available after forecast"}</b></div>
    <div><span>Image format</span><b>${esc(m.crs || "Georeferenced")}${m.width_px ? ` · ${m.width_px}×${m.height_px}px` : ""}</b></div>
  </div><p class="context-source">Source: ${esc(m.source || s.source_label || "GeoTIFF metadata")}${e.wind_source ? ` · Wind: ${esc(e.wind_source)} · Current: ${esc(e.current_source)}` : ""}</p>`;
}
function renderScene() {
  const s = S.scene; const b = s.bbox; const bounds = [[b[1], b[0]], [b[3], b[2]]];
  if (layers.scene) map.removeLayer(layers.scene); if (layers.prob) map.removeLayer(layers.prob);
  layers.scene = s.quicklook ? L.imageOverlay(s.quicklook, bounds, { opacity: $("#chkScene").checked ? 0.85 : 0 }).addTo(map) : null;
  layers.prob = s.prob_overlay ? L.imageOverlay(s.prob_overlay, bounds, { opacity: $("#chkProb").checked ? 0.8 : 0 }).addTo(map) : null;
  if (layers.scene) layers.scene.on("error", () => notify("Radar preview file is unavailable. Re-analyse this scene to regenerate it.", true));
  applyImagePreview();
  layers.slick.clearLayers(); reviewDetections(s).forEach((d) => layers.slick.addData({ type: "Feature", properties: d, geometry: d.geometry.polygon_geojson }));
  layers.slick.eachLayer((l) => l.bindTooltip(`${l.feature.properties.label} · ${fmt(l.feature.properties.geometry.area_km2)} km²`));
  map.fitBounds(bounds);
  $("#sceneInfo").innerHTML = `<div class="card"><h4>Scene</h4>sensing ${s.sensing_time || "?"} · detector <b>${s.detector}</b> · ${s.processing_s}s<br>
    <span class="muted">${s.age_estimate.status}: ${s.age_estimate.reason}</span></div>`;
  $("#detList").innerHTML = s.detections.length ? s.detections.map((d) => `<div class="card"><h4>#${d.rank} <span class="badge ${d.label.includes("look") ? "la" : "oil"}">${esc(d.label)}</span> oil-likelihood ${fmt(d.oil_likelihood)}</h4>
    area ${fmt(d.geometry.area_km2, 3)} km² · perimeter ${fmt(d.geometry.perimeter_km)} km · L×W ${fmt(d.geometry.length_km)}×${fmt(d.geometry.width_km)} km<br>
    orientation ${fmt(d.geometry.orientation_deg, 0)}° · elongation ${fmt(d.geometry.elongation, 1)} · compactness ${fmt(d.geometry.compactness, 3)}<br>
    centroid ${fmt(d.geometry.centroid_lat, 4)}, ${fmt(d.geometry.centroid_lon, 4)} · ${d.geometry.pixel_count} px · contrast ${fmt(d.geometry.mean_contrast_db, 1)} dB · seg conf ${fmt(d.seg_confidence)}<br>
    ${d.lookalike_penalties.length ? "<b>look-alike rules:</b> " + d.lookalike_penalties.map((p) => p.rule).join(", ") : "no look-alike rule triggered"}<br>
    <button class="primary" onclick="selectDet('${d.id}')">Use this detection for drift</button></div>`).join("") : '<p class="empty-note">No suspected slick found at the current detector threshold. This does not prove the scene is oil-free.</p>';
  if (!S.detection && s.detections.length) S.detection = s.detections.reduce((a, b) => (a.oil_likelihood > b.oil_likelihood ? a : b));
  renderContext();
  kpis();
}
window.selectDet = (id) => {
  if (S.detection?.id !== id) { clearDownstream(); ["drift", "ais", "evidence"].forEach((stage) => setStage(stage, "waiting")); S.detection = S.scene.detections.find((d) => d.id === id); kpis(); }
  goto("drift");
};
$("#btnAnalyze").onclick = analyzeScene;
$("#chkProb").onchange = () => layers.prob && layers.prob.setOpacity($("#chkProb").checked ? 0.8 : 0);

/* ---------------- stage 2: drift ---------------- */
async function runDrift() {
  if (!S.detection) return notify("Analyse a scene and select a detection first.", true);
  $("#btnDrift").disabled = true; ["ais", "evidence"].forEach((stage) => setStage(stage, "waiting"));
  setStage("drift", "running");
  try {
    notify("Modelling possible origins and future drift…");
    const drift = await api("/api/drift/hindcast", { investigation_id: S.inv.id, detection_id: S.detection.id, forcing: $("#forcingSel").value, hours: +$("#hoursIn").value, forward_hours: +$("#fwdIn").value });
    clearDownstream(); S.drift = drift;
    $("#ageSlider").max = S.drift.backward.hypotheses.length; renderDrift(); log(`hindcast: ${S.drift.backward.hypotheses.length} age hypotheses, wind ${S.drift.wind_at_slick_ms} m/s`);
    setStage("drift", "complete");
    notify("Drift complete. Origin times are hypotheses, not measured spill age.");
  } catch (e) { setStage("drift", "failed"); notify(e.message, true); } finally { $("#btnDrift").disabled = false; }
}
function renderDrift() {
  const d = S.drift; ["ellipses", "back", "fwd", "vectors"].forEach((k) => layers[k].clearLayers());
  L.polyline(d.backward.centre_track.map((p) => [p[1], p[0]]), { color: "#3ec5ff", weight: 2, dashArray: "4 4" }).addTo(layers.back);
  if (d.forward && $("#chkFwd").checked) {
    L.polyline(d.forward.centre_track.map((p) => [p[1], p[0]]), { color: "#39d98a", weight: 2, dashArray: "2 6" }).addTo(layers.fwd);
    const last = d.forward.hypotheses[d.forward.hypotheses.length - 1];
    if (last && !window.OSI_PUBLIC_DEMO) L.geoJSON(last.ellipse90, { style: { color: "#39d98a", weight: 1, fillOpacity: 0.12 } }).bindTooltip(`forecast +${last.age_hours} h (90 %)`).addTo(layers.fwd);
  }
  const age = +$("#ageSlider").value; const h = d.backward.hypotheses[Math.min(age, d.backward.hypotheses.length) - 1];
  $("#ageLbl").textContent = `${h.age_hours} h  →  ${h.time.slice(0, 16)}Z`;
  if (!window.OSI_PUBLIC_DEMO) {
    L.geoJSON(h.ellipse90, { style: { color: "#ffb648", weight: 1, fillOpacity: 0.10, dashArray: "3 3" } }).addTo(layers.ellipses);
    L.geoJSON(h.ellipse50, { style: { color: "#ffb648", weight: 2, fillOpacity: 0.25 } }).bindTooltip(`origin hypothesis ${h.age_hours} h · 50 % ellipse ${fmt(h.ellipse50.semi_axes_km[0], 1)}×${fmt(h.ellipse50.semi_axes_km[1], 1)} km`).addTo(layers.ellipses);
    h.particles.forEach((p) => L.circleMarker([p[1], p[0]], { radius: 1.5, color: "#ffb648", opacity: 0.5, interactive: false }).addTo(layers.ellipses));
    d.backward.hypotheses.forEach((x) => L.circleMarker([x.centre[1], x.centre[0]], { radius: 3, color: "#3ec5ff", fillOpacity: 1 }).bindTooltip(`age ${x.age_hours} h · spread ${fmt(x.spread_km, 1)} km`).addTo(layers.back));
  }
  // Disabled UI cleanup: drawVectors(h) is schematic and could be mistaken for measured forcing.
  // if ($("#chkVec").checked) drawVectors(h);
  $("#driftInfo").innerHTML = `<div class="card"><h4>Origin estimate</h4>Release window: <b>${d.origin_window.earliest.slice(0, 16)}Z → ${d.origin_window.latest.slice(0, 16)}Z</b><br>
    Selected hypothesis ${h.age_hours} h: centre ${fmt(h.centre[1], 4)}, ${fmt(h.centre[0], 4)} · spread σ ${fmt(h.spread_km, 2)} km<br>
    Wind drift deflected ${d.params.wind_deflection_deg ? d.params.wind_deflection_deg.join("–") + "° to the " + (d.params.hemisphere === "S" ? "left" : "right") + " (" + d.params.hemisphere + " hemisphere)" : "–"} · land: ${d.params.land_interaction || "–"}${h.stranded_fraction ? ` · ${Math.round(h.stranded_fraction * 100)} % of particles beached at this age` : ""}<br>
    Wind at slick ${d.wind_at_slick_ms} m/s (modelled) <span class="muted">${d.wind_at_slick_ms < 2.5 || d.wind_at_slick_ms > 10 ? "⚠ outside 2.5–10 m/s detection window" : "✓ inside 2.5–10 m/s detection window"}</span><br>
    <span class="muted small">${d.origin_window.note}</span></div>`;
  $("#driftParams").textContent = JSON.stringify(d.params, null, 1);
  renderContext();
  kpis();
}
function drawVectors(h) {
  // vectors are drawn from the hypothesis centre outward as a schematic of the forcing sampled server-side (particles' mean displacement)
  const c = h.centre; const tr = S.drift.backward.centre_track; const i = Math.max(1, tr.findIndex((p) => p[2] === h.time));
  if (i < 1) return; const a = tr[i - 1], b = tr[i];
  const arrow = (from, dlon, dlat, color, label) => L.polyline([[from[1], from[0]], [from[1] + dlat, from[0] + dlon]], { color, weight: 3 }).bindTooltip(label).addTo(layers.vectors);
  arrow(c, (a[0] - b[0]) * 6, (a[1] - b[1]) * 6, "#3ec5ff", "net surface drift (current + wind factor) — direction the oil moved");
}
$("#btnDrift").onclick = runDrift; $("#ageSlider").oninput = () => S.drift && renderDrift();
// $("#chkVec").onchange = $("#chkFwd").onchange = () => S.drift && renderDrift();
$("#chkFwd").onchange = () => S.drift && renderDrift();

/* ---------------- stage 3: AIS ---------------- */
async function runAis() {
  if (!S.drift) return notify("Run the hindcast first, then correlate AIS vessel tracks.", true);
  $("#btnAis").disabled = true; setStage("evidence", "waiting");
  setStage("ais", "running");
  try {
    notify("Cleaning AIS observations and comparing vessel tracks…");
    S.ais = await api("/api/ais/analyze", { investigation_id: S.inv.id, ais: $("#aisSel").value, radius_km: +$("#radiusIn").value });
    S.selectedVessel = null;
    renderAis(); renderRanking(); await loadLedger(); log(`AIS: ${S.ais.n_vessels_total} vessels, ${S.ais.n_candidates} candidates after filtering; dropped ${JSON.stringify(S.ais.cleaning.dropped)}`);
    setStage("ais", "complete"); setStage("evidence", "complete");
    notify("Vessel matching complete. Scores are evidence matches, not guilt probabilities.");
  } catch (e) { setStage("ais", "failed"); notify(e.message, true); } finally { $("#btnAis").disabled = false; }
}
const catColor = { tanker: "#ff9f43", cargo: "#c8d3ea", fishing: "#7bd88f", passenger: "#a29bfe", other: "#8fa0c0" };
function visibleTypes() { return [...document.querySelectorAll(".fType:checked")].map((e) => e.value); }
function drawTrack(tr, selected = false) {
  const ll = tr.track.coordinates.map((p) => [p[1], p[0]]);
  const tooltip = `${esc(tr.name)} · ${tr.mmsi} · ${esc(tr.category)}`;
  // Draw recorded sections separately: a long missing interval is not an observed route.
  const options = { color: selected ? "#62ceff" : catColor[tr.category] || "#c8d3ea", weight: selected ? 3 : tr.candidate ? 2 : 1, opacity: selected ? 1 : S.selectedVessel ? 0.3 : 0.7 };
  let section = ll.length ? [ll[0]] : [];
  const line = (points, style) => L.polyline(points, style).bindTooltip(tooltip).on("click", () => showVessel(tr.mmsi)).addTo(layers.tracks);
  for (let i = 1; i < ll.length; i++) {
    const gap = tr.gaps.some((g) => new Date(g.start) < new Date(tr.times[i]) && new Date(g.end) > new Date(tr.times[i - 1]));
    if (gap) { line(section, options); line([ll[i - 1], ll[i]], { ...options, color: "#ff3cf0", dashArray: "4 6", weight: 1 }); section = []; }
    section.push(ll[i]);
  }
  if (section.length) line(section, options);
  return ll;
}
function renderAis() {
  const a = S.ais; ["tracks", "positions", "gaps"].forEach((k) => layers[k].clearLayers());
  const types = visibleTypes(); const only = $("#chkOnlyCand").checked;
  const t0 = new Date(a.window[0]).getTime(), t1 = new Date(a.window[1]).getTime();
  const tSel = t0 + (t1 - t0) * (+$("#timeSlider").value / 100); $("#timeLbl").textContent = new Date(tSel).toISOString().slice(0, 16) + "Z";
  a.tracks.forEach((tr) => {
    if (!types.includes(tr.category) || (only && !tr.candidate)) return;
    const ll = drawTrack(tr, S.selectedVessel === tr.mmsi);
    tr.gaps.forEach((g) => L.circleMarker([g.lat, g.lon], { radius: 7, color: "#ff3cf0", weight: 2, fillOpacity: 0.2 }).bindTooltip(`AIS gap ${g.minutes} min · ${tr.name}`).addTo(layers.gaps));
    // position at slider time (nearest real fix within 10 min)
    let best = null; tr.times.forEach((t, i) => { const dt = Math.abs(new Date(t).getTime() - tSel); if (dt < 600000 && (!best || dt < best.dt)) best = { dt, i }; });
    if (best) L.circleMarker(ll[best.i], { radius: 5, color: catColor[tr.category], fillOpacity: 1 }).bindTooltip(`${tr.name} · SOG ${tr.sog[best.i]} kn · COG ${tr.cog[best.i]}°`).addTo(layers.positions);
  });
  $("#aisInfo").innerHTML = `<div class="card"><h4>AIS quality ${a.synthetic ? '<span class="badge la">SYNTHETIC DATA</span>' : ""}</h4>raw ${a.cleaning.n_raw} → kept ${a.cleaning.n_kept} · dropped ${JSON.stringify(a.cleaning.dropped)}<br>
    ${a.n_vessels_total} vessels in file · ${a.n_candidates} inside ${a.search_radius_km} km of origin envelope during ${a.window[0].slice(0, 16)}Z → ${a.window[1].slice(0, 16)}Z</div>`;
  $("#vesselList").innerHTML = `<table><tr><th>MMSI</th><th>Name</th><th>Type</th><th>fixes</th><th>gaps</th></tr>` +
    a.tracks.filter((t) => types.includes(t.category) && (!only || t.candidate)).map((t) => `<tr><td>${t.mmsi}</td><td><button class="candidate-button" onclick="showVessel(${t.mmsi})">${esc(t.name)}</button></td><td>${esc(t.category)}</td><td>${t.n}</td><td>${t.gaps.length}</td></tr>`).join("") + `</table>`;
  kpis();
}
$("#btnAis").onclick = runAis; $("#timeSlider").oninput = () => S.ais && renderAis();
document.querySelectorAll(".fType, #chkOnlyCand").forEach((e) => (e.onchange = () => S.ais && renderAis()));

/* ---------------- ranking + evidence ---------------- */
function renderRanking() {
  const c = S.ais.candidates; const ev = (x, n) => x.evidence.find((e) => e.name === n);
  $("#rankTable").innerHTML = `<table><tr><th>#</th><th>Vessel</th><th>Corr.</th><th>Dist km</th><th>Δt</th><th>Traj</th><th>Behav</th><th>AIS Q</th></tr>` +
    c.map((x) => `<tr><td>${x.rank}</td><td><button class="candidate-button" onclick="showVessel(${x.mmsi})">${esc(x.name)}</button><br><span class="muted">${x.mmsi} · ${esc(x.category)}</span></td>
      <td><b>${(x.correlation * 100).toFixed(0)}/100</b><div class="bar"><i style="width:${x.correlation * 100}%"></i></div></td>
      <td>${fmt(x.min_distance_km, 1)}</td><td>${x.best_age_hours == null ? "–" : `−${x.best_age_hours}h${x.time_offset_min ? (x.time_offset_min > 0 ? "+" : "") + x.time_offset_min + "m" : ""}`}</td>
      <td>${(ev(x, "trajectory").score * 100).toFixed(0)}%</td><td>${(ev(x, "behaviour").score * 100).toFixed(0)}%</td><td>${(ev(x, "ais_quality").score * 100).toFixed(0)}%</td></tr>`).join("") + `</table>
      <p class="muted small">${S.ais.disclaimer}</p>`;
}
window.showVessel = async (mmsi) => {
  if (!S.ais || !S.drift || !S.detection) return notify("Run drift and AIS analysis to inspect vessel evidence.", true);
  const invId = S.inv.id;
  S.selectedVessel = mmsi; renderAis();
  try {
  const v = await api(`/api/vessels/${mmsi}?investigation_id=${invId}`);
  if (S.inv?.id !== invId || S.selectedVessel !== mmsi || !S.drift || !S.ais) return;
  const c = v.correlation; goto("evidence");
  if (!v.trajectory) return notify("No stored trajectory for this vessel in the selected investigation.", true);
  const det = S.detection, g = det.geometry, h = S.drift.backward.hypotheses[(c && c.best_age_hours ? c.best_age_hours : 4) - 1];
  // Keep the incident and matched source in view, not hours of unrelated travel.
  const focus = L.latLngBounds([[S.scene.bbox[1], S.scene.bbox[0]], [S.scene.bbox[3], S.scene.bbox[2]]]);
  if (h) focus.extend(h.ellipse90.coordinates[0].map((p) => [p[1], p[0]]));
  map.invalidateSize(); map.fitBounds(focus.pad(0.15));
  $("#evidencePanel").innerHTML = `
    <img class="radar-preview" src="${esc(S.scene.quicklook)}" alt="Radar scene preview for this investigation"><p class="preview-caption">Radar preview · ${esc(S.scene.sensing_time || "time unavailable")} · ${S.scene.synthetic === true ? "synthetic scene" : "check scene metadata"}</p>
    <div class="card"><h4>${esc(v.vessel.name)} · MMSI ${v.vessel.mmsi} ${v.vessel.imo ? "· " + esc(v.vessel.imo) : ""} · ${esc(v.vessel.category)}</h4>
      ${c ? `<b>Evidence match ${(c.correlation * 100).toFixed(0)}/100</b> (rank ${c.rank}) — <span class="muted">not a calibrated probability or proof</span>` : "<span class='muted'>not a candidate (outside spatio-temporal window)</span>"}</div>
    <div class="card"><h4>1 · Satellite evidence</h4>scene ${S.scene.sensing_time} · detector ${S.scene.detector}<br>segmentation confidence ${fmt(det.seg_confidence)} · oil-likelihood after look-alike rules ${fmt(det.oil_likelihood)} (${det.label})</div>
    <div class="card"><h4>2 · Slick geometry</h4>${fmt(g.area_km2, 2)} km² · ${fmt(g.length_km, 1)}×${fmt(g.width_km, 2)} km · axis ${fmt(g.orientation_deg, 0)}° · elongation ${fmt(g.elongation, 1)}</div>
    <div class="card"><h4>3 · Drift evidence</h4>Origin window ${S.drift.origin_window.earliest.slice(0, 16)}Z → ${S.drift.origin_window.latest.slice(0, 16)}Z · best-matching age ${c ? c.best_age_hours : "–"} h → cloud centre ${h ? fmt(h.centre[1], 3) + ", " + fmt(h.centre[0], 3) : ""} ±${h ? fmt(h.spread_km, 1) : ""} km (1σ) · engine ${S.drift.params.engine}</div>
    <div class="card"><h4>4 · AIS evidence</h4>${v.trajectory.n_fixes} fixes ${v.trajectory.t_start.slice(0, 16)}Z → ${v.trajectory.t_end.slice(0, 16)}Z · gaps: ${v.trajectory.gaps.length ? v.trajectory.gaps.map((x) => `${x.minutes} min @ ${x.start.slice(0, 16)}Z`).join(", ") : "none"}</div>
    ${c ? `<div class="card"><h4>5 · Score breakdown</h4>${c.evidence.map((e) => `<div class="ev"><span>${esc(e.name)} <span class="muted">w=${e.weight}</span></span><div class="bar"><i style="width:${e.score * 100}%"></i></div><span>${(e.score * 100).toFixed(0)}%</span><span class="muted small" style="grid-column:1/4">${esc(e.explanation)}</span></div>`).join("")}</div>
    <div class="card"><h4>6 · Uncertainty & limitations</h4><ul class="small">${[...c.limitations, "Optical validation is not implemented", "Origin position uncertainty grows with age hypothesis (see ellipses)", "Spill age not observable from one scene → window, not instant", "Scores are rule-based v1 weights; not calibrated on labelled incidents"].map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>` : ""}`;
  } catch (e) { notify(e.message, true); }
};
async function loadLedger() {
  const invId = S.inv.id;
  const e = await api(`/api/evidence/${invId}`);
  if (S.inv?.id !== invId) return;
  $("#ledger").innerHTML = `<table><tr><th>kind</th><th>ref</th><th>sha256</th><th>time</th></tr>` + e.items.map((i) => `<tr><td>${i.kind}</td><td>${i.ref_id}</td><td>${i.sha256.slice(0, 14)}…</td><td>${i.created_at.slice(11, 19)}</td></tr>`).join("") + `</table>`;
}

/* ---------------- dashboard ---------------- */
function clearDownstream() {
  S.drift = S.ais = S.selectedVessel = null;
  ["ellipses", "back", "fwd", "vectors", "tracks", "positions", "gaps"].forEach((key) => layers[key].clearLayers());
  ["driftInfo", "driftParams", "aisInfo", "vesselList", "ledger"].forEach((id) => $("#" + id).replaceChildren());
  $("#rankTable").innerHTML = '<p class="empty-note">Run AIS correlation to review candidates.</p>';
  $("#evidencePanel").innerHTML = '<p class="empty-note">Select a candidate after drift and AIS analysis.</p>';
}
function resetResults() {
  clearDownstream(); S.scene = S.detection = null;
  for (const key of ["scene", "prob"]) { if (layers[key]) map.removeLayer(layers[key]); layers[key] = null; }
  layers.slick.clearLayers();
  $("#sceneInfo").replaceChildren(); $("#detList").replaceChildren();
  $("#contextPanel").hidden = true; $("#contextPanel").replaceChildren();
  S.timeline = []; $("#timeline").replaceChildren();
}
function renderCandidates() {
  const candidates = S.ais?.candidates || [];
  $("#candidateOverview").innerHTML = candidates.length ? `<table><thead><tr><th>Vessel</th><th>Evidence match</th><th>Nearest to origin</th><th>Missing tracking</th></tr></thead><tbody>${candidates.map((c) => {
    const track = S.ais.tracks.find((t) => t.mmsi === c.mmsi);
    return `<tr class="${S.selectedVessel === c.mmsi ? "selected-row" : ""}"><td><button class="candidate-button" onclick="showVessel(${c.mmsi})">${esc(c.name)}</button><div class="candidate-tag">${c.mmsi} · ${esc(c.category)}</div></td><td>${fmt(c.correlation * 100, 0)}<div class="bar"><i style="width:${c.correlation * 100}%"></i></div></td><td>${fmt(c.min_distance_km, 1)} km</td><td>${track?.gaps.length ? `<span class="warn">${track.gaps.length} gap(s)</span>` : "None recorded"}</td></tr>`;
  }).join("")}</tbody></table>` : `<p class="empty-note">${S.ais ? "No vessel matched this time and location. Untracked vessels may still exist." : "Possible source vessels appear after the scan completes."}</p>`;
}
function kpis() {
  const d = S.detection, dr = S.drift, a = S.ais; const top = a && a.candidates[0];
  const h = dr && dr.backward.hypotheses[3];
  $("#kpis").innerHTML = S.inv ? [
    ["Scenes scanned", S.scene ? "1" : "0"], ["Latest scene", S.scene ? (S.scene.sensing_time || "").slice(0, 16) : "–"],
    ["Slicks detected", S.scene ? String(S.scene.detections?.length || 0) : "–"],
    ["Oil-likelihood score (uncalibrated)", d ? fmt(d.oil_likelihood) : "–"], ["Area", d ? fmt(d.geometry.area_km2, 2) + " km²" : "–"],
    ["Centroid", d ? `${fmt(d.geometry.centroid_lat, 3)}, ${fmt(d.geometry.centroid_lon, 3)}` : "–"],
    ["Origin (4 h hyp.)", h ? `${fmt(h.centre[1], 3)}, ${fmt(h.centre[0], 3)} ±${fmt(h.spread_km, 1)} km` : "–"],
    ["Candidate vessels", a ? `${a.n_candidates} / ${a.n_vessels_total}` : "–"], ["Top match / 100", top ? `${top.name} (${(top.correlation * 100).toFixed(0)})` : "–"],
  ].map(([k, v]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("") : "";
  $("#timelineHeading").hidden = !S.inv;
  $("#caseTitle").textContent = S.inv?.mode === "demo" ? "Arabian Sea continuous monitor" : S.inv?.name || "Oil spill monitoring";
  $("#caseId").textContent = S.inv?.mode === "demo" ? "Monitoring area · latest cycle" : S.inv ? S.inv.id : "New area";
  const isReception = S.inv?.mode === "sar-reception";
  $("#dataBadge").textContent = S.inv ? isReception ? "Real image" : S.scene?.synthetic || S.ais?.synthetic ? "Demo data" : S.inv.mode === "demo" ? "Demo data" : "Live data" : "Not started";
  $("#dataBadge").classList.toggle("la", S.inv?.mode === "demo");
  $("#dataBadge").classList.toggle("oil", isReception);
  $("#acquisitionValue").textContent = S.scene?.sensing_time ? S.scene.sensing_time.slice(0, 16).replace("T", " ") + "Z" : "—";
  if (S.inv?.mode === "demo") $("#casePicker").value = "demo";
  if (isReception) $("#casePicker").value = "reception";
  $("#mapEmpty").hidden = Boolean(S.inv);
  $("#btnFit").disabled = !S.scene;
  $("#btnExport").disabled = !S.inv || isReception;
  $("#caseSummary").innerHTML = isReception ? [
    `${esc(S.scene?.platform || "Sentinel-1A")} <b>IW GRD</b>`,
    `Acquisition <b>${esc(S.scene?.sensing_time?.slice(0, 19).replace("T", " ") || "unknown")} UTC</b>`,
    `Slice <b>${S.scene?.duration_s || 25} seconds</b>`,
    `<b>${esc(S.scene?.source_label || "Historic measured SAR")}</b>`
  ].map((s) => `<span>${s}</span>`).join("") : S.inv ? [
    `Latest image <b>${esc(S.scene?.sensing_time?.replace("T", " ") || "Waiting")}</b>`,
    `Images checked <b>${S.scene ? "1" : "0"}</b>`,
    `Possible spills <b>${reviewDetections(S.scene).length}</b>`,
    '<b>Review required before action</b>'
  ].map((s) => `<span>${s}</span>`).join("") : "Choose an operation to begin.";
  renderCandidates();
}

async function applyCaseResult(r, message) {
  resetResults(); resetStages();
  S.inv = r.investigation; S.scene = r.scene; S.detection = r.scene?.detections.find((d) => d.id === r.detection_id) || null;
  S.drift = r.drift; S.ais = r.ais;
  if (S.scene) renderScene();
  if (S.drift) { $("#ageSlider").max = S.drift.backward.hypotheses.length; renderDrift(); }
  if (S.ais) { renderAis(); renderRanking(); }
  await loadLedger(); refreshInvestigations(); kpis(); completeAvailableStages();
  const warnings = r.warnings || [];
  notify(warnings.length ? `${message} ${warnings.join(" ")}` : message);
  if (S.ais?.candidates.length) await showVessel(S.ais.candidates[0].mmsi);
  else goto(S.drift ? "drift" : "satellite");
}

async function watchAutoJob(id) {
  try {
    const job = await api(`/api/data/jobs/${id}`);
    $("#autoProgress").innerHTML = `<b>${esc(job.message || job.status)}</b><progress value="${job.progress}" max="1"></progress> ${(job.progress * 100).toFixed(0)}%`;
    if (job.status === "done") {
      $("#btnAutoRun").disabled = false;
      await applyCaseResult(job.result, job.result.completed_stage === "complete" ? "Complete investigation ready." : "Available analysis completed.");
      return;
    }
    if (job.status === "error") throw new Error(job.error || "Image analysis failed");
    setTimeout(() => watchAutoJob(id), 1500);
  } catch (error) {
    $("#btnAutoRun").disabled = false; $("#autoProgress").textContent = ""; notify(error.message, true);
  }
}

const PUBLIC_SAMPLE = {
  bytes: 28449879,
  sha256: "94e369dce57caf24970f90521191e98f9c086d837ecf197932f7d02baca6f37e",
};

async function sha256(file) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

$("#btnAutoRun").onclick = async () => {
  const file = $("#autoSceneFile").files[0];
  if (!file) return notify("Choose a georeferenced SAR GeoTIFF first.", true);
  $("#btnAutoRun").disabled = true; $("#autoProgress").textContent = "Uploading image…";
  if (window.OSI_PUBLIC_DEMO) {
    try {
      if (file.size !== PUBLIC_SAMPLE.bytes || await sha256(file) !== PUBLIC_SAMPLE.sha256) {
        throw new Error("The public demo accepts the downloadable sample only. Run the local or Docker build to analyse another GeoTIFF.");
      }
      $("#autoProgress").textContent = "Sample verified. Loading its published result…";
      const result = await api("/api/demo/run", {});
      await applyCaseResult(result, "Sample uploaded. This static public demo replays its precomputed result; the full FastAPI build performs the analysis.");
      log("verified public sample uploaded; loaded precomputed judge result");
    } catch (error) {
      notify(error.message, true);
    } finally {
      $("#btnAutoRun").disabled = false; $("#autoProgress").textContent = "";
    }
    return;
  }
  const form = new FormData(); form.append("file", file);
  try {
    let response;
    try { response = await fetch("/api/auto/run", { method: "POST", body: form }); }
    catch { throw new Error("Cannot reach the analysis service. Start the server or Docker container, then try again."); }
    const payload = await response.json().catch(() => ({ detail: `Upload failed with HTTP ${response.status}` }));
    if (!response.ok) throw new Error(payload.detail || "Upload failed");
    log(`one-image workflow queued as ${payload.job_id}`); watchAutoJob(payload.job_id);
  } catch (error) {
    $("#btnAutoRun").disabled = false; $("#autoProgress").textContent = ""; notify(error.message, true);
  }
};

function replayDelay() { return 720 / Number($("#replaySpeed").value || 1); }
function replayTime(label) {
  const observed = replay.result?.scene?.sensing_time?.slice(0, 16).replace("T", " ");
  $("#replayClock").textContent = observed ? `${label} · acquisition ${observed}Z` : label;
}
function stopReplayActivity() {
  clearTimeout(replay.timer);
  if (replay.frame) cancelAnimationFrame(replay.frame);
  replay.timer = replay.frame = null;
  replay.active = false;
}
function setReceptionReveal(fraction) {
  const image = layers.scene?.getElement?.() || layers.scene?._image;
  if (!image) return;
  image.classList.add("sar-reception");
  image.style.clipPath = `inset(0 0 ${(100 - fraction * 100).toFixed(2)}% 0)`;
}
function receiveFrame(now) {
  if (!replay.active || replay.kind !== "reception") return;
  if (!replay.lastFrame) replay.lastFrame = now;
  if (!replay.paused) replay.elapsedMs += Math.min(now - replay.lastFrame, 250) * Number($("#replaySpeed").value || 1);
  replay.lastFrame = now;
  const duration = activeReceptionSample().durationS;
  const fraction = Math.min(1, replay.elapsedMs / (duration * 1000));
  setReceptionReveal(fraction);
  $("#acquisitionProgress").style.width = `${fraction * 100}%`;
  $("#replayClock").textContent = replay.paused ? `Reception paused · ${Math.round(fraction * 100)}%` : `Receiving Sentinel-1 · ${Math.round(fraction * 100)}% · ${(replay.elapsedMs / 1000).toFixed(1)} / ${duration}s`;
  if (fraction >= 1) {
    replay.active = false; replay.frame = null; setStage("acquisition", "complete");
    $("#systemValue").textContent = "Received"; $("#operationState").textContent = "Received";
    $("#btnReplayPause").disabled = true; $("#btnReplayPause").textContent = "Pause";
    $("#replayClock").textContent = `Product received · ${duration}s slice`;
    notify("Sentinel-1 IW product slice received.");
    return;
  }
  replay.frame = requestAnimationFrame(receiveFrame);
}
function startRealReception() {
  stopReplayActivity(); resetResults(); resetStages();
  const sample = activeReceptionSample();
  replay.kind = "reception"; replay.paused = false; replay.elapsedMs = 0; replay.lastFrame = 0;
  S.inv = { id: sample.id, name: `${sample.platform || "Sentinel-1"} acquisition`, mode: "sar-reception" };
  S.scene = {
    bbox: sample.bbox, quicklook: sample.quicklook, prob_overlay: null,
    sensing_time: sample.start, detector: "not processed", processing_s: 0,
    platform: sample.platform || "Sentinel-1A", duration_s: sample.durationS, source_label: sample.sourceLabel || "Historic measured SAR",
    synthetic: false, detections: [], age_estimate: { status: "not estimated", reason: "acquisition only" },
  };
  S.detection = null; renderScene(); kpis(); goto("dashboard");
  if (window.OSI_PUBLIC_DEMO) addOpt($("#sceneSel"), "real-sar-sample", "Received Sentinel-1 measured sample", true);
  setReceptionReveal(0); setStage("acquisition", "running");
  $("#casePicker").value = "reception"; $("#btnReplayPause").disabled = false; $("#btnReplayPause").textContent = "Pause";
  $("#btnReplayRestart").textContent = "Restart";
  replay.active = true; replay.frame = requestAnimationFrame(receiveFrame);
}
async function revealReplayStage(id) {
  const r = replay.result;
  if (id === "acquisition") {
    S.scene = { ...r.scene, detections: [] }; S.detection = null;
    renderScene(); replayTime("Observed SAR loaded"); log("observed SAR acquisition loaded from the synthetic judge case");
  } else if (id === "preprocessing") {
    replayTime("Processed SAR preview"); log("processed: georeferenced, clipped and speckle-filtered for analysis");
  } else if (id === "detection") {
    S.scene = r.scene; S.detection = r.scene?.detections.find((d) => d.id === r.detection_id) || null;
    renderScene(); replayTime("Suspected slick extracted"); log(`${S.scene.detections.length} suspected dark formation(s) measured`);
  } else if (id === "drift") {
    S.drift = r.drift; $("#ageSlider").max = S.drift.backward.hypotheses.length; renderDrift();
    replayTime("Modelled origin and forecast"); log(`${S.drift.backward.hypotheses.length} release-time hypotheses reconstructed`);
  } else if (id === "ais") {
    S.ais = r.ais; renderAis(); renderRanking();
    replayTime("Historical AIS correlated"); log(`${S.ais.n_candidates} candidate vessel lead(s) ranked from ${S.ais.n_vessels_total} tracks`);
  } else if (id === "evidence") {
    await loadLedger(); kpis(); renderCandidates();
    replayTime("Evidence package ready"); log("auditable evidence record prepared for investigator review");
  }
}
function runReplayStage() {
  if (!replay.active || replay.paused || replay.kind !== "investigation") return;
  const entry = PIPELINE_STAGES[replay.index];
  if (!entry) return;
  const [id, label] = entry; setStage(id, "running"); replayTime(label);
  $("#acquisitionProgress").style.width = `${(replay.index / PIPELINE_STAGES.length) * 100}%`;
  clearTimeout(replay.timer);
  replay.timer = setTimeout(async () => {
    try {
      await revealReplayStage(id); setStage(id, "complete"); replay.index += 1;
      $("#acquisitionProgress").style.width = `${(replay.index / PIPELINE_STAGES.length) * 100}%`;
      if (replay.index >= PIPELINE_STAGES.length) {
        replay.active = false; $("#btnReplayPause").disabled = true; $("#btnReplayPause").textContent = "Pause";
        const slicks = reviewDetections(S.scene).length;
        $("#systemValue").textContent = "Watching"; $("#operationState").textContent = "Watching";
        $("#replayClock").textContent = `Cycle complete · 1 scene · ${slicks} slicks · next catalogue check 15 min`;
        notify(`Monitoring cycle complete. ${slicks} suspected slicks require analyst review.`);
      } else runReplayStage();
    } catch (error) {
      replay.active = false; setStage(id, "failed"); notify(error.message, true);
    }
  }, replayDelay());
}
async function startHistoricalReplay() {
  stopReplayActivity(); replay.kind = "investigation";
  $("#btnRunReplay").disabled = true; $("#btnStartDemo").disabled = true; $("#btnReplayRestart").disabled = true;
  notify("Scanning available Sentinel-1 scenes…");
  try {
    const result = await api("/api/demo/run", {});
    resetResults(); resetStages();
    replay.result = result; replay.index = 0; replay.paused = false; replay.active = true;
    S.inv = result.investigation; $("#casePicker").value = "demo"; kpis(); goto("dashboard");
    $("#btnReplayPause").disabled = false; $("#btnReplayPause").textContent = "Pause";
    $("#btnReplayRestart").textContent = "Restart";
    runReplayStage();
  } catch (error) { notify(error.message, true); }
  finally { $("#btnRunReplay").disabled = false; $("#btnStartDemo").disabled = false; $("#btnReplayRestart").disabled = false; }
}
$("#btnReplayPause").onclick = () => {
  if (!replay.active) return;
  replay.paused = !replay.paused; $("#btnReplayPause").textContent = replay.paused ? "Resume" : "Pause";
  if (replay.kind === "reception") {
    replay.lastFrame = performance.now();
    if (!replay.frame) replay.frame = requestAnimationFrame(receiveFrame);
    return;
  }
  clearTimeout(replay.timer);
  if (replay.paused) replayTime("Replay paused"); else runReplayStage();
};
$("#replaySpeed").onchange = () => {
  if (replay.kind === "reception") return;
  if (!replay.active || replay.paused) return;
  clearTimeout(replay.timer); runReplayStage();
};

$("#btnDemo").onclick = async () => {
  await startHistoricalReplay();
};
$("#btnStartDemo").onclick = startHistoricalReplay;
$("#btnRunReplay").onclick = startHistoricalReplay;
$("#btnReceiveSample").onclick = startRealReception;
$("#btnReceiveMap").onclick = startRealReception;
$("#btnReplayRestart").onclick = () => replay.kind === "reception" ? startRealReception() : startHistoricalReplay();
$("#btnFit").onclick = () => S.scene && map.fitBounds([[S.scene.bbox[1], S.scene.bbox[0]], [S.scene.bbox[3], S.scene.bbox[2]]]);
$("#chkScene").onchange = () => layers.scene?.setOpacity($("#chkScene").checked ? 0.85 : 0);
function setGroupVisibility(keys, visible) {
  keys.forEach((key) => {
    const layer = layers[key]; if (!layer) return;
    if (visible && !map.hasLayer(layer)) layer.addTo(map);
    if (!visible && map.hasLayer(layer)) map.removeLayer(layer);
  });
}
function applyImagePreview() {
  if (!layers.scene?._image) return;
  layers.scene._image.classList.toggle("contrast-preview", $("#chkEnhanced").checked);
}
$("#chkEnhanced").onchange = () => {
  applyImagePreview();
  if ($("#chkEnhanced").checked) notify("Contrast preview is a visual aid only. Detection and measurements use the original SAR pixels.");
};
$("#chkSlick").onchange = () => setGroupVisibility(["slick"], $("#chkSlick").checked);
$("#chkDriftLayers").onchange = () => setGroupVisibility(["ellipses", "back", "fwd", "vectors"], $("#chkDriftLayers").checked);
$("#chkAisLayers").onchange = () => setGroupVisibility(["tracks", "positions", "gaps"], $("#chkAisLayers").checked);
$("#btnExport").onclick = async () => {
  if (!S.inv) return;
  const inv = { ...S.inv };
  try {
    if (window.OSI_PUBLIC_DEMO) {
      const blob = new Blob([JSON.stringify(window.OSI_DEMO_DATA.evidence, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob); const a = document.createElement("a");
      a.href = url; a.download = `${inv.id}-evidence.json`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      notify("Evidence JSON exported."); return;
    }
    // Check availability before starting a normal attachment download (no blob URL).
    await api(`/api/evidence/${inv.id}`);
    const a = document.createElement("a"); a.href = `/api/evidence/${inv.id}/export`; a.download = `${inv.id}-evidence.json`;
    document.body.appendChild(a); a.click(); a.remove();
    notify("Evidence JSON download requested. A formatted PDF report is not implemented yet.");
  } catch (e) { notify(e.message, true); }
};
kpis();


/* ================= Phase 2: real-data connectors ================= */
function addOpt(sel, value, label, select = false) { if (![...sel.options].some((o) => o.value === value)) { const o = document.createElement("option"); o.value = value; o.textContent = label; sel.appendChild(o); } if (select) sel.value = value; }

async function refreshInvestigations() {
  try { const list = await api("/api/investigations"); const sel = $("#invSel"); sel.innerHTML = '<option value="">— select —</option>' + list.map((i) => `<option value="${i.id}">${esc(i.name)} · ${i.id} · ${i.mode}</option>`).join(""); } catch {}
}
$("#invSel").onchange = async (e) => {
  if (!e.target.value) return;
  try {
    const inv = await api(`/api/investigations/${e.target.value}`);
    resetResults(); resetStages(); S.inv = inv.investigation; S.scene = inv.scene; S.drift = inv.drift; S.ais = inv.ais;
    S.detection = S.scene?.detections.find((d) => d.id === S.drift?.detection_id) || null;
    if (S.scene) renderScene();
    if (S.drift) { $("#ageSlider").max = S.drift.backward.hypotheses.length; renderDrift(); }
    if (S.ais) { renderAis(); renderRanking(); }
    await loadLedger(); kpis(); completeAvailableStages(); log(`opened ${S.inv.id}`);
    notify(S.scene && !S.drift ? "Scene restored. Run drift and AIS to continue; older cases may not have saved full stage results." : "Saved investigation restored.");
    if (S.ais?.candidates.length) await showVessel(S.ais.candidates[0].mmsi); else goto(S.scene ? "satellite" : "dashboard");
  } catch (x) { notify(x.message, true); }
};
$("#btnNew").onclick = async () => {
  const name = "Real-data investigation " + new Date().toISOString().slice(0, 16).replace("T", " ");
  try {
    const inv = await api("/api/investigations", { name, mode: "real" }); resetResults(); resetStages(); S.inv = inv;
    ["sceneSel", "forcingSel", "aisSel"].forEach((id) => { const sel = $("#" + id); addOpt(sel, "", "Choose uploaded / fetched data", true); });
    log(`investigation ${S.inv.id} created (mode: real)`); kpis(); refreshInvestigations(); goto("satellite"); notify("Upload or fetch a radar scene. Check location and time coverage for every input.");
  } catch (e) { notify(e.message, true); }
};

/* ---- jobs ---- */
const jobWatchers = {};
async function pollJob(id, onDone, box) {
  const j = await api(`/api/data/jobs/${id}`);
  if (box) box.innerHTML = `<b>${j.kind}</b> ${j.status} ${(j.progress * 100).toFixed(0)}% <progress value="${j.progress}" max="1"></progress> ${esc(j.message || "")}${j.error ? `<div class="err">${esc(j.error)}</div>` : ""}`;
  if (j.status === "done") { onDone(j); return; }
  if (j.status === "error") { log(`job ${id} failed: ${j.error}`); return; }
  setTimeout(() => pollJob(id, onDone, box).catch((e) => box && (box.textContent = e.message)), 2000);
}
async function renderJobs() {
  try {
    const js = await api("/api/data/jobs");
    $("#jobList").innerHTML = js.length ? js.map((j) => `<div class="job"><span>${j.kind}</span><span class="${j.status === "error" ? "err" : j.status === "done" ? "ok" : ""}">${j.status}</span><progress class="bar" value="${j.progress}" max="1"></progress><span>${esc(j.message || j.error || "")}</span></div>`).join("") : "<i>no jobs yet</i>";
    const ups = await api("/api/uploads");
    $("#uploadList").innerHTML = ups.length ? ups.map((u) => `<div class="job"><span>${u.kind}</span><code>${u.upload_id}</code><span>${(u.bytes / 1e6).toFixed(1)} MB</span><span>${u.modified.slice(0, 16)}</span></div>`).join("") : "<i>nothing fetched yet</i>";
    const keep = { scene: $("#sceneSel").value, forcing: $("#forcingSel").value, ais: $("#aisSel").value };   // never clobber the user's choice
    ups.forEach((u) => { if (u.kind === "scene") addOpt($("#sceneSel"), u.upload_id, `fetched scene ${u.upload_id.slice(0, 8)}…`); if (u.kind === "forcing") addOpt($("#forcingSel"), u.upload_id, `forcing ${u.upload_id.slice(0, 8)}… (${u.modified.slice(0, 16)})`); if (u.kind === "ais") addOpt($("#aisSel"), u.upload_id, `AIS ${u.upload_id.slice(0, 8)}… (${(u.bytes / 1e6).toFixed(1)} MB)`); });
    $("#sceneSel").value = keep.scene; $("#forcingSel").value = keep.forcing; $("#aisSel").value = keep.ais;
  } catch {}
}

/* ---- connector status ---- */
async function renderStatus() {
  try {
    const st = await api("/api/data/status");
    $("#connStatus").innerHTML = `<table><tr><th>Connector</th><th>Status</th><th>Account / cost</th><th>Note</th></tr>` + st.connectors.map((c) => `<tr><td>${esc(c.name)}</td><td class="${c.ready ? "ok" : "err"}">${c.ready ? "ready" : "not configured"}</td><td>${esc(c.account)}</td><td class="note">${esc(c.note)}</td></tr>`).join("") + `</table><p class="small muted">Internet reachable from server: ${st.internet ? "yes" : "no (offline mode — bundled demo only)"}</p>`;
  } catch (e) { $("#connStatus").textContent = e.message; }
}

/* ---- Sentinel-1 ---- */
$("#btnS1Search").onclick = async () => {
  $("#s1Results").innerHTML = "searching CDSE catalogue…";
  try {
    const r = await api("/api/data/sentinel1/search", { provider: $("#s1prov").value, lon: +$("#s1lon").value, lat: +$("#s1lat").value, start: $("#s1from").value + "T00:00:00Z", end: $("#s1to").value + "T23:59:59Z", top: 20 });
    if (!r.products.length) { $("#s1Results").innerHTML = "<i>No IW GRD products found for that point/period (try a coastal point on a S1 orbit; S1B is out of service since Dec 2021).</i>"; return; }
    $("#s1Results").innerHTML = `<table><tr><th>Sensing (UTC)</th><th></th><th>Product</th><th>Size</th><th>Orbit</th></tr>` + r.products.map((p) => `<tr><td>${esc(p.sensing_start).slice(0, 16).replace("T", " ")}</td><td><button class="mini" data-id="${p.id}" title="${esc(p.name)}">Fetch + calibrate</button></td><td title="${esc(p.name)}">${esc(p.name).slice(0, 16)}…${p.cog ? " COG" : ""}</td><td>${p.size_mb ? p.size_mb + " MB" : "COG"}</td><td>${esc(p.orbit_direction || "")}</td></tr>`).join("") + "</table>";
    $("#s1Results").querySelectorAll("button").forEach((b) => b.onclick = async () => {
      b.disabled = true;
      try {
        const a = +$("#s1aoi").value, lon = +$("#s1lon").value, lat = +$("#s1lat").value;
        const j = await api("/api/data/sentinel1/fetch", { provider: $("#s1prov").value, product_id: b.dataset.id, downsample: +$("#s1factor").value, bbox: [lon - a, lat - a, lon + a, lat + a] });
        log(`S1 fetch job ${j.id} started`); const box = document.createElement("div"); b.parentElement.appendChild(box);
        pollJob(j.id, (jd) => { addOpt($("#sceneSel"), jd.result.upload_id, `S1 ${jd.result.safe.slice(0, 32)}…`, true); log(`scene ready as upload ${jd.result.upload_id} → page 2 Analyze`); renderJobs(); }, box);
      } catch (e) { alert(e.message); b.disabled = false; }
    });
  } catch (e) { $("#s1Results").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
};

/* ---- forcing ---- */
$("#btnForcing").onclick = async () => {
  if (!S.inv || !S.scene) return alert("Analyse a scene first (page 2) – the forcing box and time come from the scene.");
  try {
    const j = await api("/api/data/forcing/build", { investigation_id: S.inv.id, currents: "hycom", hours_back: Math.max(12, +$("#hoursIn").value + 2), hours_fwd: Math.max(6, +$("#fwdIn").value + 2) });
    log(`forcing job ${j.id}: HYCOM + Open-Meteo`);
    pollJob(j.id, (jd) => { addOpt($("#forcingSel"), jd.result.upload_id, `${jd.result.current_source.split(" (")[0]} + ERA5 · ${jd.result.time_range[0].slice(0, 16)}`, true); log(`forcing ready: mean current ${jd.result.mean_current_ms.toFixed(2)} m/s, wind ${jd.result.mean_wind_ms.toFixed(1)} m/s`); renderJobs(); }, $("#forcingJob"));
  } catch (e) { alert(e.message); }
};

/* ---- AIS ---- */
$("#btnAisFetch").onclick = async () => {
  if (!S.inv || !S.scene) return alert("Analyse a scene first – the AIS window is derived from the scene time and bbox.");
  try {
    const j = await api("/api/data/ais/fetch", { investigation_id: S.inv.id, source: $("#aisSrcSel").value, hours_back: Math.max(12, +$("#hoursIn").value + 2), radius_deg: 0.6 });
    log(`AIS job ${j.id} (${$("#aisSrcSel").value}) started – daily files are 200–2500 MB, streamed & cached`);
    pollJob(j.id, (jd) => { addOpt($("#aisSel"), jd.result.upload_id, `${jd.result.source.split(" (")[0]} · ${jd.result.n_in_bbox} msgs / ${jd.result.n_vessels} vessels`, true); log(`AIS ready: ${jd.result.n_in_bbox} messages, ${jd.result.n_vessels} vessels`); renderJobs(); }, $("#aisJob"));
  } catch (e) { alert(e.message); }
};

document.querySelector('#nav button[data-page="data"]').addEventListener("click", () => { renderStatus(); renderJobs(); });
$("#casePicker").onchange = (event) => {
  if (event.target.value === "demo") startHistoricalReplay();
  else if (event.target.value === "reception") startRealReception();
  else if (!S.inv) goto("dashboard");
};
renderStageStatus();
if (!window.OSI_PUBLIC_DEMO) { refreshInvestigations(); renderJobs(); setInterval(renderJobs, 15000); }

// Keep a case/stage change from overtaking an in-flight analysis or case restore.
let caseBusy = false;
function caseAction(action) {
  return async function(event) {
    if (caseBusy) return;
    caseBusy = true;
    const controls = [...document.querySelectorAll('#sidebar button, #sidebar input, #sidebar select, #btnStartDemo')];
    const disabled = controls.map((el) => el.disabled);
    controls.forEach((el) => el.disabled = true);
    $("main").setAttribute("aria-busy", "true");
    try { await action.call(this, event); }
    catch (e) { notify(e.message, true); }
    finally {
      controls.forEach((el, i) => el.disabled = disabled[i]);
      caseBusy = false; $("main").removeAttribute("aria-busy"); kpis();
    }
  };
}
['btnDemo', 'btnNew', 'btnAnalyze', 'btnDrift', 'btnAis'].forEach((id) => {
  const button = $('#' + id); button.onclick = caseAction(button.onclick);
});
$('#invSel').onchange = caseAction($('#invSel').onchange);

if (window.OSI_PUBLIC_DEMO) {
  document.body.classList.add("public-demo");
  $("#sampleSceneDownload").href = "samples/OSI_sample_20250314.tif";
  $("#autoSceneFile").title = "Upload the downloadable sample to replay its verified result";
  $("#btnAutoRun").title = "The static demo accepts the downloadable sample; arbitrary GeoTIFFs need the full deployment";
  ["btnNew", "btnDrift", "btnAis", "btnForcing", "btnAisFetch", "btnS1Search"].forEach((id) => {
    const control = document.getElementById(id); if (control) { control.disabled = true; control.title = "Available in the full FastAPI deployment"; }
  });
  $("#sceneFile").disabled = true;
  $("#sceneFile").title = "The public demo analyses its bundled measured sample; arbitrary GeoTIFFs need the full deployment";
  $("#btnAnalyze").title = "Analyse the selected bundled scene using its published baseline result";
  $("#health").textContent = "● Public replay ready";
}
loadLiveCatalogue();
setInterval(loadLiveCatalogue, 300000);
