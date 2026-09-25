const STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search";
const BBOX = [72.2, 18.6, 73.0, 19.5];

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

module.exports = async function handler(_request, response) {
  try {
    const now = new Date();
    const start = new Date(now.getTime() - 120 * 86400000);
    const radar = (await search("sentinel-1-grd", start.toISOString(), now.toISOString(), 20))
      .filter((feature) => !feature.properties?.["sar:instrument_mode"] || feature.properties["sar:instrument_mode"] === "IW")
      .sort((a, b) => String(b.properties?.datetime).localeCompare(String(a.properties?.datetime)))
      .slice(0, 8).map(radarView);
    if (!radar.length) return response.status(200).json({ area: "Mumbai coast", bbox: BBOX, checked_at: now.toISOString(), radar: [], optical: [] });
    const observed = new Date(radar[0].properties.datetime);
    const opticalEnd = new Date(Math.min(now, new Date(observed.getTime() + 10 * 86400000)));
    const optical = (await search("sentinel-2-l2a", new Date(observed.getTime() - 10 * 86400000).toISOString(), opticalEnd.toISOString(), 100))
      .sort((a, b) => {
        const score = (feature) => Math.abs(new Date(feature.properties?.datetime) - observed) / 86400000 + Number(feature.properties?.["eo:cloud_cover"] ?? 100) / 10;
        return score(a) - score(b);
      }).slice(0, 5).map(opticalView);
    response.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    return response.status(200).json({ area: "Mumbai coast", bbox: BBOX, checked_at: now.toISOString(), radar, optical });
  } catch (error) {
    return response.status(502).json({ error: error.message });
  }
}
