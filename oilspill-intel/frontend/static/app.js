/* OSI dashboard – vanilla JS + Leaflet (BSD-2). No external tiles: works fully offline. */
const S = { inv: null, scene: null, detection: null, drift: null, ais: null, uploads: {}, timeline: [] };
window.S = S;
const $ = (q) => document.querySelector(q);
const api = async (path, body, method) => {
  const r = await fetch(path, body ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  return r.json();
};
const fmt = (x, d = 2) => (x == null ? "–" : Number(x).toFixed(d));
const log = (msg) => { S.timeline.push(`${new Date().toISOString().slice(11, 19)}Z  ${msg}`); $("#timeline").innerHTML = S.timeline.map((t) => `<li>${t}</li>`).join(""); };

/* ---------------- map ---------------- */
const map = L.map("map", { zoomControl: true, attributionControl: false }).setView([19.4, 70.6], 9);
L.control.scale({ imperial: false }).addTo(map);
const layers = {
  coast: L.geoJSON(null, { style: { color: "#4a6a9a", weight: 1 } }).addTo(map),
  graticule: L.layerGroup().addTo(map),
  scene: null, prob: null,
  slick: L.geoJSON(null, { style: { color: "#ff5a3c", weight: 2, fillOpacity: 0.25 } }).addTo(map),
  ellipses: L.layerGroup().addTo(map), back: L.layerGroup().addTo(map), fwd: L.layerGroup().addTo(map),
  vectors: L.layerGroup().addTo(map), tracks: L.layerGroup().addTo(map), positions: L.layerGroup().addTo(map), gaps: L.layerGroup().addTo(map),
};
fetch("/api/demo/coastline").then((r) => r.json()).then((g) => layers.coast.addData(g)).catch(() => {});
for (let lat = -80; lat <= 80; lat += 1) L.polyline([[lat, -180], [lat, 180]], { color: "#152036", weight: 0.5, interactive: false }).addTo(layers.graticule);
for (let lon = -180; lon <= 180; lon += 1) L.polyline([[-85, lon], [85, lon]], { color: "#152036", weight: 0.5, interactive: false }).addTo(layers.graticule);
const legend = (rows) => ($("#legend").innerHTML = rows.map(([c, t]) => `<div><span class="sw" style="background:${c}"></span>${t}</div>`).join(""));
legend([["#ff5a3c", "Detected slick"], ["#ffb648", "Origin ellipse 50 % / 90 %"], ["#3ec5ff", "Backward centre track"], ["#39d98a", "Forward forecast"], ["#c8d3ea", "Vessel track"], ["#ff3cf0", "AIS gap"]]);

/* ---------------- nav ---------------- */
document.querySelectorAll("#nav button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#nav button, .page").forEach((e) => e.classList.remove("active"));
  b.classList.add("active"); $(`#page-${b.dataset.page}`).classList.add("active"); setTimeout(() => map.invalidateSize(), 50);
});
const goto = (p) => document.querySelector(`#nav button[data-page=${p}]`).click();

/* ---------------- health ---------------- */
api("/api/health").then((h) => { $("#health").textContent = `● API online · CNN weights: ${h.cnn_weights ? "loaded" : "absent → baseline detector"} · offline mode`; $("#healthBox").textContent = JSON.stringify(h, null, 1); })
  .catch(() => ($("#health").textContent = "● API offline"));

/* ---------------- uploads ---------------- */
async function upload(kind, file, sel) {
  const fd = new FormData(); fd.append("kind", kind); fd.append("file", file);
  const r = await fetch("/api/satellite/upload", { method: "POST", body: fd }); const j = await r.json();
  if (!r.ok) throw new Error(j.detail);
  const o = document.createElement("option"); o.value = j.upload_id; o.textContent = `${file.name} (${(j.bytes / 1e6).toFixed(1)} MB, sha ${j.sha256.slice(0, 8)}…)`; sel.appendChild(o); sel.value = j.upload_id;
  log(`uploaded ${kind} ${file.name} sha256=${j.sha256.slice(0, 12)}`);
}
$("#sceneFile").onchange = (e) => upload("scene", e.target.files[0], $("#sceneSel")).catch((x) => alert(x.message));
$("#aisFile").onchange = (e) => upload("ais", e.target.files[0], $("#aisSel")).catch((x) => alert(x.message));

/* ---------------- stage 1: investigation + scene ---------------- */
async function ensureInv() {
  if (S.inv) return S.inv;
  S.inv = await api("/api/investigations", { name: "Investigation " + new Date().toISOString().slice(0, 16), mode: "demo" });
  log(`investigation ${S.inv.id} created`); return S.inv;
}
async function analyzeScene() {
  await ensureInv(); $("#btnAnalyze").disabled = true;
  try {
    const wind = $("#windIn").value ? Number($("#windIn").value) : null;
    S.scene = await api("/api/satellite/analyze", { investigation_id: S.inv.id, scene: $("#sceneSel").value, wind_ms: wind });
    renderScene(); log(`scene analysed by ${S.scene.detector} in ${S.scene.processing_s}s → ${S.scene.detections.length} object(s)`);
  } catch (e) { alert(e.message); } finally { $("#btnAnalyze").disabled = false; }
}
function renderScene() {
  const s = S.scene; const b = s.bbox; const bounds = [[b[1], b[0]], [b[3], b[2]]];
  if (layers.scene) map.removeLayer(layers.scene); if (layers.prob) map.removeLayer(layers.prob);
  layers.scene = L.imageOverlay(s.quicklook, bounds, { opacity: 0.95 }).addTo(map);
  layers.prob = L.imageOverlay(s.prob_overlay, bounds, { opacity: $("#chkProb").checked ? 0.8 : 0 }).addTo(map);
  layers.slick.clearLayers(); s.detections.forEach((d) => layers.slick.addData({ type: "Feature", properties: d, geometry: d.geometry.polygon_geojson }));
  layers.slick.eachLayer((l) => l.bindTooltip(`${l.feature.properties.label} · ${fmt(l.feature.properties.geometry.area_km2)} km²`));
  map.fitBounds(bounds);
  $("#sceneInfo").innerHTML = `<div class="card"><h4>Scene</h4>sensing ${s.sensing_time || "?"} · detector <b>${s.detector}</b> · ${s.processing_s}s<br>
    <span class="muted">${s.age_estimate.status}: ${s.age_estimate.reason}</span></div>`;
  $("#detList").innerHTML = s.detections.map((d) => `<div class="card"><h4>#${d.rank} <span class="badge ${d.label.includes("look") ? "la" : "oil"}">${d.label}</span> oil-likelihood ${fmt(d.oil_likelihood)}</h4>
    area ${fmt(d.geometry.area_km2, 3)} km² · perimeter ${fmt(d.geometry.perimeter_km)} km · L×W ${fmt(d.geometry.length_km)}×${fmt(d.geometry.width_km)} km<br>
    orientation ${fmt(d.geometry.orientation_deg, 0)}° · elongation ${fmt(d.geometry.elongation, 1)} · compactness ${fmt(d.geometry.compactness, 3)}<br>
    centroid ${fmt(d.geometry.centroid_lat, 4)}, ${fmt(d.geometry.centroid_lon, 4)} · ${d.geometry.pixel_count} px · contrast ${fmt(d.geometry.mean_contrast_db, 1)} dB · seg conf ${fmt(d.seg_confidence)}<br>
    ${d.lookalike_penalties.length ? "<b>look-alike rules:</b> " + d.lookalike_penalties.map((p) => p.rule).join(", ") : "no look-alike rule triggered"}<br>
    <button class="primary" onclick="selectDet('${d.id}')">use for drift</button></div>`).join("");
  if (!S.detection && s.detections.length) S.detection = s.detections.reduce((a, b) => (a.oil_likelihood > b.oil_likelihood ? a : b));
  kpis();
}
window.selectDet = (id) => { S.detection = S.scene.detections.find((d) => d.id === id); goto("drift"); };
$("#btnAnalyze").onclick = analyzeScene;
$("#chkProb").onchange = () => layers.prob && layers.prob.setOpacity($("#chkProb").checked ? 0.8 : 0);

/* ---------------- stage 2: drift ---------------- */
async function runDrift() {
  if (!S.detection) return alert("Analyse a scene first");
  $("#btnDrift").disabled = true;
  try {
    S.drift = await api("/api/drift/hindcast", { investigation_id: S.inv.id, detection_id: S.detection.id, forcing: $("#forcingSel").value, hours: +$("#hoursIn").value, forward_hours: +$("#fwdIn").value });
    $("#ageSlider").max = S.drift.backward.hypotheses.length; renderDrift(); log(`hindcast: ${S.drift.backward.hypotheses.length} age hypotheses, wind ${S.drift.wind_at_slick_ms} m/s`);
  } catch (e) { alert(e.message); } finally { $("#btnDrift").disabled = false; }
}
function renderDrift() {
  const d = S.drift; ["ellipses", "back", "fwd", "vectors"].forEach((k) => layers[k].clearLayers());
  L.polyline(d.backward.centre_track.map((p) => [p[1], p[0]]), { color: "#3ec5ff", weight: 2, dashArray: "4 4" }).addTo(layers.back);
  if (d.forward && $("#chkFwd").checked) {
    L.polyline(d.forward.centre_track.map((p) => [p[1], p[0]]), { color: "#39d98a", weight: 2, dashArray: "2 6" }).addTo(layers.fwd);
    const last = d.forward.hypotheses[d.forward.hypotheses.length - 1];
    if (last) L.geoJSON(last.ellipse90, { style: { color: "#39d98a", weight: 1, fillOpacity: 0.12 } }).bindTooltip(`forecast +${last.age_hours} h (90 %)`).addTo(layers.fwd);
  }
  const age = +$("#ageSlider").value; const h = d.backward.hypotheses[Math.min(age, d.backward.hypotheses.length) - 1];
  $("#ageLbl").textContent = `${h.age_hours} h  →  ${h.time.slice(0, 16)}Z`;
  L.geoJSON(h.ellipse90, { style: { color: "#ffb648", weight: 1, fillOpacity: 0.10, dashArray: "3 3" } }).addTo(layers.ellipses);
  L.geoJSON(h.ellipse50, { style: { color: "#ffb648", weight: 2, fillOpacity: 0.25 } }).bindTooltip(`origin hypothesis ${h.age_hours} h · 50 % ellipse ${fmt(h.ellipse50.semi_axes_km[0], 1)}×${fmt(h.ellipse50.semi_axes_km[1], 1)} km`).addTo(layers.ellipses);
  h.particles.forEach((p) => L.circleMarker([p[1], p[0]], { radius: 1.5, color: "#ffb648", opacity: 0.5, interactive: false }).addTo(layers.ellipses));
  // all-hypothesis envelope
  d.backward.hypotheses.forEach((x) => L.circleMarker([x.centre[1], x.centre[0]], { radius: 3, color: "#3ec5ff", fillOpacity: 1 }).bindTooltip(`age ${x.age_hours} h · spread ${fmt(x.spread_km, 1)} km`).addTo(layers.back));
  if ($("#chkVec").checked) drawVectors(h);
  $("#driftInfo").innerHTML = `<div class="card"><h4>Origin estimate</h4>Release window: <b>${d.origin_window.earliest.slice(0, 16)}Z → ${d.origin_window.latest.slice(0, 16)}Z</b><br>
    Selected hypothesis ${h.age_hours} h: centre ${fmt(h.centre[1], 4)}, ${fmt(h.centre[0], 4)} · spread σ ${fmt(h.spread_km, 2)} km<br>
    Wind drift deflected ${d.params.wind_deflection_deg ? d.params.wind_deflection_deg.join("–") + "° to the " + (d.params.hemisphere === "S" ? "left" : "right") + " (" + d.params.hemisphere + " hemisphere)" : "–"} · land: ${d.params.land_interaction || "–"}${h.stranded_fraction ? ` · ${Math.round(h.stranded_fraction * 100)} % of particles beached at this age` : ""}<br>
    Wind at slick ${d.wind_at_slick_ms} m/s (modelled) <span class="muted">${d.wind_at_slick_ms < 2.5 || d.wind_at_slick_ms > 10 ? "⚠ outside 2.5–10 m/s detection window" : "✓ inside 2.5–10 m/s detection window"}</span><br>
    <span class="muted small">${d.origin_window.note}</span></div>`;
  $("#driftParams").textContent = JSON.stringify(d.params, null, 1);
  kpis();
}
function drawVectors(h) {
  // vectors are drawn from the hypothesis centre outward as a schematic of the forcing sampled server-side (particles' mean displacement)
  const c = h.centre; const tr = S.drift.backward.centre_track; const i = Math.max(1, tr.findIndex((p) => p[2] === h.time));
  if (i < 1) return; const a = tr[i - 1], b = tr[i];
  const arrow = (from, dlon, dlat, color, label) => L.polyline([[from[1], from[0]], [from[1] + dlat, from[0] + dlon]], { color, weight: 3 }).bindTooltip(label).addTo(layers.vectors);
  arrow(c, (a[0] - b[0]) * 6, (a[1] - b[1]) * 6, "#3ec5ff", "net surface drift (current + wind factor) — direction the oil moved");
  arrow(c, 0.08 * Math.sign(a[0] - b[0] || 1), 0.06, "#b0c4de", `wind 10 m ≈ ${S.drift.wind_at_slick_ms} m/s (schematic)`);
}
$("#btnDrift").onclick = runDrift; $("#ageSlider").oninput = () => S.drift && renderDrift();
$("#chkVec").onchange = $("#chkFwd").onchange = () => S.drift && renderDrift();

/* ---------------- stage 3: AIS ---------------- */
async function runAis() {
  if (!S.drift) return alert("Run the hindcast first");
  $("#btnAis").disabled = true;
  try {
    S.ais = await api("/api/ais/analyze", { investigation_id: S.inv.id, ais: $("#aisSel").value, radius_km: +$("#radiusIn").value });
    renderAis(); renderRanking(); loadLedger(); log(`AIS: ${S.ais.n_vessels_total} vessels, ${S.ais.n_candidates} candidates after filtering; dropped ${JSON.stringify(S.ais.cleaning.dropped)}`);
  } catch (e) { alert(e.message); } finally { $("#btnAis").disabled = false; }
}
const catColor = { tanker: "#ff9f43", cargo: "#c8d3ea", fishing: "#7bd88f", passenger: "#a29bfe", other: "#8fa0c0" };
function visibleTypes() { return [...document.querySelectorAll(".fType:checked")].map((e) => e.value); }
function renderAis() {
  const a = S.ais; ["tracks", "positions", "gaps"].forEach((k) => layers[k].clearLayers());
  const types = visibleTypes(); const only = $("#chkOnlyCand").checked;
  const t0 = new Date(a.window[0]).getTime(), t1 = new Date(a.window[1]).getTime();
  const tSel = t0 + (t1 - t0) * (+$("#timeSlider").value / 100); $("#timeLbl").textContent = new Date(tSel).toISOString().slice(0, 16) + "Z";
  a.tracks.forEach((tr) => {
    if (!types.includes(tr.category) || (only && !tr.candidate)) return;
    const ll = tr.track.coordinates.map((p) => [p[1], p[0]]);
    L.polyline(ll, { color: catColor[tr.category], weight: tr.candidate ? 2 : 1, opacity: tr.candidate ? 0.9 : 0.4 }).bindTooltip(`${tr.name} · ${tr.mmsi} · ${tr.category}`).on("click", () => showVessel(tr.mmsi)).addTo(layers.tracks);
    tr.gaps.forEach((g) => L.circleMarker([g.lat, g.lon], { radius: 7, color: "#ff3cf0", weight: 2, fillOpacity: 0.2 }).bindTooltip(`AIS gap ${g.minutes} min · ${tr.name}`).addTo(layers.gaps));
    // position at slider time (nearest real fix within 10 min)
    let best = null; tr.times.forEach((t, i) => { const dt = Math.abs(new Date(t).getTime() - tSel); if (dt < 600000 && (!best || dt < best.dt)) best = { dt, i }; });
    if (best) L.circleMarker(ll[best.i], { radius: 5, color: catColor[tr.category], fillOpacity: 1 }).bindTooltip(`${tr.name} · SOG ${tr.sog[best.i]} kn · COG ${tr.cog[best.i]}°`).addTo(layers.positions);
  });
  $("#aisInfo").innerHTML = `<div class="card"><h4>AIS quality ${a.synthetic ? '<span class="badge la">SYNTHETIC DATA</span>' : ""}</h4>raw ${a.cleaning.n_raw} → kept ${a.cleaning.n_kept} · dropped ${JSON.stringify(a.cleaning.dropped)}<br>
    ${a.n_vessels_total} vessels in file · ${a.n_candidates} inside ${a.search_radius_km} km of origin envelope during ${a.window[0].slice(0, 16)}Z → ${a.window[1].slice(0, 16)}Z</div>`;
  $("#vesselList").innerHTML = `<table><tr><th>MMSI</th><th>Name</th><th>Type</th><th>fixes</th><th>gaps</th></tr>` +
    a.tracks.filter((t) => types.includes(t.category) && (!only || t.candidate)).map((t) => `<tr class="clickable" onclick="showVessel(${t.mmsi})"><td>${t.mmsi}</td><td>${t.name}</td><td>${t.category}</td><td>${t.n}</td><td>${t.gaps.length}</td></tr>`).join("") + `</table>`;
  kpis();
}
$("#btnAis").onclick = runAis; $("#timeSlider").oninput = () => S.ais && renderAis();
document.querySelectorAll(".fType, #chkOnlyCand").forEach((e) => (e.onchange = () => S.ais && renderAis()));

/* ---------------- ranking + evidence ---------------- */
function renderRanking() {
  const c = S.ais.candidates; const ev = (x, n) => x.evidence.find((e) => e.name === n);
  $("#rankTable").innerHTML = `<table><tr><th>#</th><th>Vessel</th><th>Corr.</th><th>Dist km</th><th>Δt</th><th>Traj</th><th>Behav</th><th>AIS Q</th></tr>` +
    c.map((x) => `<tr class="clickable" onclick="showVessel(${x.mmsi})"><td>${x.rank}</td><td>${x.name}<br><span class="muted">${x.mmsi} · ${x.category}</span></td>
      <td><b>${(x.correlation * 100).toFixed(0)}%</b><div class="bar"><i style="width:${x.correlation * 100}%"></i></div></td>
      <td>${fmt(x.min_distance_km, 1)}</td><td>${x.best_age_hours == null ? "–" : `−${x.best_age_hours}h${x.time_offset_min ? (x.time_offset_min > 0 ? "+" : "") + x.time_offset_min + "m" : ""}`}</td>
      <td>${(ev(x, "trajectory").score * 100).toFixed(0)}%</td><td>${(ev(x, "behaviour").score * 100).toFixed(0)}%</td><td>${(ev(x, "ais_quality").score * 100).toFixed(0)}%</td></tr>`).join("") + `</table>
      <p class="muted small">${S.ais.disclaimer}</p>`;
}
window.showVessel = async (mmsi) => {
  const v = await api(`/api/vessels/${mmsi}?investigation_id=${S.inv.id}`); const c = v.correlation; goto("evidence");
  layers.tracks.eachLayer((l) => l.setStyle({ weight: 1, opacity: 0.35 }));
  const ll = v.trajectory.geojson.coordinates.map((p) => [p[1], p[0]]);
  const hl = L.polyline(ll, { color: "#fff", weight: 3 }).addTo(layers.tracks); map.fitBounds(hl.getBounds().pad(0.3));
  const det = S.detection, g = det.geometry, h = S.drift.backward.hypotheses[(c && c.best_age_hours ? c.best_age_hours : 4) - 1];
  $("#evidencePanel").innerHTML = `
    <div class="card"><h4>${v.vessel.name} · MMSI ${v.vessel.mmsi} ${v.vessel.imo ? "· " + v.vessel.imo : ""} · ${v.vessel.category}</h4>
      ${c ? `<b>Correlation ${(c.correlation * 100).toFixed(0)}%</b> (rank ${c.rank}) — <span class="muted">consistency with evidence, not proof</span>` : "<span class='muted'>not a candidate (outside spatio-temporal window)</span>"}</div>
    <div class="card"><h4>1 · Satellite evidence</h4>scene ${S.scene.sensing_time} · detector ${S.scene.detector}<br>segmentation confidence ${fmt(det.seg_confidence)} · oil-likelihood after look-alike rules ${fmt(det.oil_likelihood)} (${det.label})</div>
    <div class="card"><h4>2 · Slick geometry</h4>${fmt(g.area_km2, 2)} km² · ${fmt(g.length_km, 1)}×${fmt(g.width_km, 2)} km · axis ${fmt(g.orientation_deg, 0)}° · elongation ${fmt(g.elongation, 1)}</div>
    <div class="card"><h4>3 · Drift evidence</h4>Origin window ${S.drift.origin_window.earliest.slice(11, 16)}Z–${S.drift.origin_window.latest.slice(11, 16)}Z · best-matching age ${c ? c.best_age_hours : "–"} h → cloud centre ${h ? fmt(h.centre[1], 3) + ", " + fmt(h.centre[0], 3) : ""} ±${h ? fmt(h.spread_km, 1) : ""} km (1σ) · engine ${S.drift.params.engine}</div>
    <div class="card"><h4>4 · AIS evidence</h4>${v.trajectory.n_fixes} fixes ${v.trajectory.t_start.slice(11, 16)}Z–${v.trajectory.t_end.slice(11, 16)}Z · gaps: ${v.trajectory.gaps.length ? v.trajectory.gaps.map((x) => `${x.minutes} min @ ${x.start.slice(11, 16)}Z`).join(", ") : "none"}</div>
    ${c ? `<div class="card"><h4>5 · Score breakdown</h4>${c.evidence.map((e) => `<div class="ev"><span>${e.name} <span class="muted">w=${e.weight}</span></span><div class="bar"><i style="width:${e.score * 100}%"></i></div><span>${(e.score * 100).toFixed(0)}%</span><span class="muted small" style="grid-column:1/4">${e.explanation}</span></div>`).join("")}</div>
    <div class="card"><h4>6 · Uncertainty & limitations</h4><ul class="small">${[...c.limitations, "Origin position uncertainty grows with age hypothesis (see ellipses)", "Spill age not observable from one scene → window, not instant", "Scores are rule-based v1 weights; not calibrated on labelled incidents"].map((l) => `<li>${l}</li>`).join("")}</ul></div>` : ""}`;
};
async function loadLedger() {
  const e = await api(`/api/evidence/${S.inv.id}`);
  $("#ledger").innerHTML = `<table><tr><th>kind</th><th>ref</th><th>sha256</th><th>time</th></tr>` + e.items.map((i) => `<tr><td>${i.kind}</td><td>${i.ref_id}</td><td>${i.sha256.slice(0, 14)}…</td><td>${i.created_at.slice(11, 19)}</td></tr>`).join("") + `</table>`;
}

/* ---------------- dashboard ---------------- */
function kpis() {
  const d = S.detection, dr = S.drift, a = S.ais; const top = a && a.candidates[0];
  const h = dr && dr.backward.hypotheses[3];
  $("#kpis").innerHTML = [
    ["Investigation", S.inv ? S.inv.id : "–"], ["Scene", S.scene ? (S.scene.sensing_time || "").slice(0, 16) : "–"],
    ["Spill confidence", d ? (d.oil_likelihood * 100).toFixed(0) + "%" : "–"], ["Area", d ? fmt(d.geometry.area_km2, 2) + " km²" : "–"],
    ["Centroid", d ? `${fmt(d.geometry.centroid_lat, 3)}, ${fmt(d.geometry.centroid_lon, 3)}` : "–"],
    ["Origin (4 h hyp.)", h ? `${fmt(h.centre[1], 3)}, ${fmt(h.centre[0], 3)} ±${fmt(h.spread_km, 1)} km` : "–"],
    ["Candidate vessels", a ? `${a.n_candidates} / ${a.n_vessels_total}` : "–"], ["Top candidate", top ? `${top.name} (${(top.correlation * 100).toFixed(0)}%)` : "–"],
  ].map(([k, v]) => `<div class="kpi"><b>${v}</b><span>${k}</span></div>`).join("");
}
$("#btnDemo").onclick = async () => {
  $("#btnDemo").disabled = true; log("offline demo started");
  try {
    const r = await api("/api/demo/run", {});
    S.inv = r.investigation; S.scene = r.scene; S.detection = r.scene.detections.find((d) => d.id === r.detection_id); S.drift = r.drift; S.ais = r.ais;
    $("#ageSlider").max = S.drift.backward.hypotheses.length;
    renderScene(); renderDrift(); renderAis(); renderRanking(); loadLedger();
    log(`demo complete: ${S.scene.detections.length} detection(s), ${S.drift.backward.hypotheses.length} age hypotheses, top candidate ${S.ais.candidates[0].name}`);
    goto("satellite");
  } catch (e) { alert(e.message); } finally { $("#btnDemo").disabled = false; }
};
kpis();


/* ================= Phase 2: real-data connectors ================= */
const esc = (x) => String(x ?? "").replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));
function addOpt(sel, value, label, select = false) { if (![...sel.options].some((o) => o.value === value)) { const o = document.createElement("option"); o.value = value; o.textContent = label; sel.appendChild(o); } if (select) sel.value = value; }

async function refreshInvestigations() {
  try { const list = await api("/api/investigations"); const sel = $("#invSel"); sel.innerHTML = '<option value="">— select —</option>' + list.map((i) => `<option value="${i.id}">${esc(i.name)} · ${i.id} · ${i.mode}</option>`).join(""); } catch {}
}
$("#invSel").onchange = async (e) => {
  if (!e.target.value) return;
  try {
    const inv = await api(`/api/investigations/${e.target.value}`);
    S.inv = inv; S.scene = inv.detections.length ? { detections: inv.detections, sensing_time: inv.scenes?.[0]?.sensing_time, bbox: inv.scenes?.[0]?.bbox } : null;
    S.detection = inv.detections.length ? inv.detections.reduce((a, b) => (a.oil_likelihood > b.oil_likelihood ? a : b)) : null; S.drift = null; S.ais = null;
    log(`opened ${inv.id} (${inv.detections.length} detections stored)`); if (S.scene && S.scene.bbox) { renderScene(); goto("satellite"); } kpis();
  } catch (x) { alert(x.message); }
};
$("#btnNew").onclick = async () => {
  const name = prompt("Investigation name", "Real-data investigation " + new Date().toISOString().slice(0, 10)); if (!name) return;
  S.inv = await api("/api/investigations", { name, mode: "real" }); S.scene = S.detection = S.drift = S.ais = null; log(`investigation ${S.inv.id} created (mode: real)`); kpis(); refreshInvestigations(); goto("satellite");
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
refreshInvestigations(); renderJobs(); setInterval(renderJobs, 15000);
