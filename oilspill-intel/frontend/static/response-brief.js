/* Plain-language handoff from existing results; no new scientific predictions. */
function buildResponseBrief(state, { detections = state.scene?.detections || [], now = Date.now(), publicDemo = false } = {}) {
  const scene = state.scene;
  if (!scene) return null;
  const number = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : "unavailable";
  const line = (v) => String(v ?? "unavailable").replace(/[\r\n\t]/g, " ");
  const utc = (v) => Number.isFinite(typeof v === "number" ? v : Date.parse(v)) ? new Date(v).toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC") : "Time unavailable";
  const coordinates = (lon, lat) => Number.isFinite(lon) && Number.isFinite(lat)
    && Math.abs(lon) <= 180 && Math.abs(lat) <= 90
    ? `${Math.abs(lat).toFixed(4)}°${lat < 0 ? "S" : "N"}, ${Math.abs(lon).toFixed(4)}°${lon < 0 ? "W" : "E"}` : "Location unavailable";
  const processed = Boolean(scene.detector && scene.detector !== "not processed");
  const det = processed && detections.find((item) => item.id === state.detection?.id);
  // Only summarize downstream results linked to this exact selected detection/run.
  const drift = det && state.drift?.detection_id === det.id ? state.drift : null;
  const ais = drift?.drift_run_id && state.ais?.detection_id === det.id
    && state.ais?.drift_run_id === drift.drift_run_id ? state.ais : null;
  const forcing = drift?.forcing_source || {};
  const synthetic = scene.synthetic || state.inv?.mode === "demo" || ais?.synthetic
    || forcing.synthetic === true || forcing.synthetic === "true";
  const basis = synthetic ? "Contains synthetic data · demonstration only"
    : publicDemo && processed ? "Historical image · published analysis"
    : processed ? "Image analysis · confirmation pending" : "Image preview · analysis pending";
  const captured = Date.parse(scene.sensing_time);
  const ageHours = (now - captured) / 3600000;
  let observed = utc(scene.sensing_time);
  if (synthetic) observed += " · scenario time";
  else if (Number.isFinite(ageHours) && ageHours >= 0) {
    observed += ageHours >= 24 ? ` · ${Math.floor(ageHours / 24)} days old`
      : ageHours >= 1 ? ` · ${Math.floor(ageHours)} hours old` : ` · ${Math.floor(ageHours * 60)} minutes old`;
  }
  const bbox = scene.bbox || [];
  const centre = scene.metadata?.centre || (bbox.length === 4
    ? { lon: (bbox[0] + bbox[2]) / 2, lat: (bbox[1] + bbox[3]) / 2 } : {});
  const location = det ? coordinates(det.geometry?.centroid_lon, det.geometry?.centroid_lat)
    : `${coordinates(centre.lon, centre.lat)} (image centre)`;
  const finding = !processed ? "Not analysed; no spill conclusion available."
    : !detections.length ? "No candidate passed the review filter; this does not establish oil-free water."
    : `${detections.length} ${detections.length === 1 ? "candidate" : "candidates"} for review${det ? `; selected area ${number(det.geometry?.area_km2, 2)} km²` : "; select a candidate for drift and vessel review"}.`;
  const end = drift?.forward?.hypotheses?.at(-1);
  const env = drift?.environment || {};
  const forecast = end ? `${number(end.age_hours)} h from image time, valid at ${utc(end.time)}; modelled centre ${coordinates(end.centre?.[0], end.centre?.[1])}${env.forecast_toward ? `; toward ${line(env.forecast_toward)}` : ""}${Number.isFinite(end.spread_km) ? `; spread ${number(end.spread_km)} km (1σ)` : ""}.`
    : "Not available for this candidate.";
  const origin = drift?.origin_window;
  const lead = ais?.candidates?.[0];
  const leadText = lead ? `${line(lead.name)} · MMSI ${line(lead.mmsi)} · evidence match ${number(lead.correlation * 100, 0)}/100 (uncalibrated).`
    : ais ? "No vessel matched the available records; untracked vessels may still exist." : "Historical AIS correlation unavailable for this candidate.";
  const rows = [
    ["Observed", observed], ["Location", location], ["Finding", finding],
    ["Possible release window", origin ? `${utc(origin.earliest)} to ${utc(origin.latest)} (model hypotheses).` : "Not estimated."],
    ["Movement estimate", forecast], ["Highest-ranked vessel lead", leadText],
  ];
  const checks = [];
  if (synthetic) checks.push("Demonstration data: do not use this case to dispatch a response or accuse a vessel.");
  if (!Number.isFinite(captured)) checks.push("Acquisition time is missing. Obtain it before time-dependent interpretation.");
  else if (ageHours < 0) checks.push("Acquisition time is in the future. Check the image metadata.");
  if (!processed) checks.push("Run SAR analysis before drawing a spill conclusion.");
  else if (detections.length) checks.push("Confirm suspected oil using additional observations; radar look-alikes can produce false alerts.");
  if (!drift && det) checks.push("Matched wind/current and drift results are missing; movement is unknown.");
  if (drift && !end) checks.push("A forward forecast is missing; the release window alone does not predict future movement.");
  if (end && !synthetic && Date.parse(end.time) < now) checks.push("This forecast period has ended. Obtain newer observations and forcing before using it for a current response.");
  if (!ais && det) checks.push("Historical AIS matching is incomplete; no responsible vessel has been established.");
  for (const limitation of lead?.limitations || []) checks.push(line(limitation));
  if (end) checks.push("Coastal arrival time and affected facilities have not been calculated. Review coastal exposure separately.");
  const sources = [...new Set([scene.metadata?.source || scene.source_label || "Image metadata", env.wind_source || forcing.wind_source,
    env.current_source || forcing.current_source, forcing.title].filter(Boolean).map(line))].join("; ");
  const reference = `${line(state.inv?.id)} / ${line(scene.scene_id || state.inv?.id)} / ${det ? line(det.id) : "no selected detection"}`;
  const text = ["OSI RESPONSE BRIEF", basis, `Prepared: ${utc(now)}`, `Case / scene / detection: ${reference}`, "",
    ...rows.map(([label, value]) => `${label}: ${value}`), "", "CHECK BEFORE HANDOFF",
    ...checks.map((check) => `- ${check}`), "", `Sources: ${sources}`,
    "Analyst review required. Vessel scores indicate investigation leads; they do not establish responsibility."].join("\n");
  return { basis, rows, checks, text };
}
if (typeof module !== "undefined") module.exports = { buildResponseBrief };
