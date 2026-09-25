const STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search";
const BBOX = [72.2, 18.6, 73.0, 19.5];
const NASA_LAYER = "VIIRS_NOAA21_CorrectedReflectance_TrueColor";

async function search(collection, start, end, limit) {
  const response = await fetch(STAC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collections: [collection], bbox: BBOX, datetime: `${start}/${end}`, limit }),
  });
  if (!response.ok) throw new Error(`Planetary Computer returned HTTP ${response.status}`);
  return (await response.json()).features || [];
}

function radarView(feature) {
  return { id: feature.id, bbox: feature.bbox, properties: {
    datetime: feature.properties?.datetime, platform: feature.properties?.platform,
    "sat:orbit_state": feature.properties?.["sat:orbit_state"], "sar:instrument_mode": feature.properties?.["sar:instrument_mode"],
  }, assets: { rendered_preview: feature.assets?.rendered_preview, thumbnail: feature.assets?.thumbnail } };
}

function opticalView(feature) {
  return { id: feature.id, bbox: feature.bbox, properties: {
    datetime: feature.properties?.datetime, "eo:cloud_cover": feature.properties?.["eo:cloud_cover"],
  }, assets: { rendered_preview: feature.assets?.rendered_preview, thumbnail: feature.assets?.thumbnail } };
}

async function nasaContext(now) {
  const imageUrl = (date) => {
    const params = new URLSearchParams({
      SERVICE: "WMS", REQUEST: "GetMap", VERSION: "1.1.1", LAYERS: NASA_LAYER, STYLES: "",
      FORMAT: "image/jpeg", TRANSPARENT: "FALSE", HEIGHT: "512", WIDTH: "512",
      SRS: "EPSG:4326", BBOX: BBOX.join(","), TIME: date.toISOString().slice(0, 10),
    });
    return `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?${params}`;
  };
  let selected = new Date(now);
  let selectedUrl = imageUrl(selected);
  for (let offset = 0; offset < 3; offset += 1) {
    const candidate = new Date(now.getTime() - offset * 86400000);
    const candidateUrl = imageUrl(candidate);
    try {
      const probe = await fetch(candidateUrl);
      if (probe.ok && String(probe.headers.get("content-type")).startsWith("image/") && (await probe.arrayBuffer()).byteLength > 5000) {
        selected = candidate; selectedUrl = candidateUrl; break;
      }
    } catch {}
  }
  const fallback = new Date(selected.getTime() - 86400000);
  return { source: "NASA GIBS", sensor: "NOAA-21 VIIRS", layer: NASA_LAYER,
    date: selected.toISOString().slice(0, 10), image_url: selectedUrl,
    fallback_date: fallback.toISOString().slice(0, 10), fallback_image_url: imageUrl(fallback) };
}

module.exports = async function handler(_request, response) {
  try {
    const now = new Date();
    const start = new Date(now.getTime() - 120 * 86400000);
    const radar = (await search("sentinel-1-grd", start.toISOString(), now.toISOString(), 20))
      .filter((feature) => !feature.properties?.["sar:instrument_mode"] || feature.properties["sar:instrument_mode"] === "IW")
      .sort((a, b) => String(b.properties?.datetime).localeCompare(String(a.properties?.datetime)))
      .slice(0, 8).map(radarView);
    if (!radar.length) return response.status(200).json({ area: "Mumbai coast", bbox: BBOX, checked_at: now.toISOString(), radar: [], optical: [], nasa: await nasaContext(now) });
    const observed = new Date(radar[0].properties.datetime);
    const opticalEnd = new Date(Math.min(now, new Date(observed.getTime() + 10 * 86400000)));
    const optical = (await search("sentinel-2-l2a", new Date(observed.getTime() - 10 * 86400000).toISOString(), opticalEnd.toISOString(), 100))
      .sort((a, b) => {
        const score = (feature) => Math.abs(new Date(feature.properties?.datetime) - observed) / 86400000 + Number(feature.properties?.["eo:cloud_cover"] ?? 100) / 10;
        return score(a) - score(b);
      }).slice(0, 5).map(opticalView);
    response.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    return response.status(200).json({ area: "Mumbai coast", bbox: BBOX, checked_at: now.toISOString(), radar, optical, nasa: await nasaContext(now) });
  } catch (error) {
    return response.status(502).json({ error: error.message });
  }
}
