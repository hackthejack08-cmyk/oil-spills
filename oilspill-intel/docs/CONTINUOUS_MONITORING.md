# Continuous oil-spill monitoring

OSI monitors an area, not one uploaded image. Each cycle:

1. Query Sentinel-1 GRD products intersecting the AOI and time window.
2. Ignore product IDs already completed for that monitor.
3. Stream and calibrate only the AOI pixels.
4. Run segmentation and retain every slick candidate above the review threshold.
5. Keep separate candidates from the same acquisition, then link nearby detections from later passes into incidents.
6. Run drift and AIS attribution per selected incident; never treat a vessel score as proof.

## Verification workflow

The monitor follows the evidence order described in Bellingcat's marine-oil-spill guide instead of treating every dark radar patch as oil:

1. **Fresh radar:** poll Sentinel-1 IW GRD acquisitions as they appear in the catalogue.
2. **Earlier passes:** compare recent scenes over the same area; persistent dark water or repeated natural patterns are warnings, not confirmations.
3. **Look-alike screen:** apply geometry, contrast, coastline and wind checks for calm water, algae, sediment, surfactants and other dark-feature look-alikes.
4. **Optical context:** find a Sentinel-2 scene intersecting the same AOI within ±10 days, balancing acquisition-time gap against cloud cover. NASA GIBS/VIIRS can add broad context. Optical imagery corroborates the review but does not replace SAR screening; missing or cloudy EO is explicitly marked inconclusive.
5. **Environment and drift:** obtain acquisition time from the radar metadata, fetch currents and wind automatically, and generate backward and forward drift hypotheses with visible uncertainty.
6. **Vessel evidence:** correlate authorised historical AIS tracks with every plausible origin time and rank investigative leads. A score is never proof of responsibility.
7. **Audit:** preserve source IDs, times, parameters, outputs and hashes for analyst review.

The public dashboard now queries the live Planetary Computer STAC catalogue every five minutes while it is open. It shows the latest real Sentinel-1 acquisition, recent-scene count and the best time/cloud-balanced Sentinel-2 companion. This EO pairing is contextual evidence for manual review; automated pixel-level SAR–EO validation is not yet implemented. The judge result remains the labelled, deterministic replay. Actual pixel streaming and detection use the FastAPI monitor described below.

The implemented endpoint is `POST /api/data/monitor/scan`. It is idempotent, runs as a background job, and supports up to 50 newly discovered products per cycle. `GET /api/data/monitor/{monitor_id}` returns products, slick events and grouped incidents.

```json
{
  "monitor_id": "mumbai-coast",
  "bbox": [72.2, 18.6, 73.0, 19.5],
  "start": "2026-09-24T00:00:00Z",
  "end": "2026-09-25T00:00:00Z",
  "max_products": 12,
  "downsample": 8,
  "min_score": 0.65
}
```

For an always-on deployment, invoke the scan endpoint every 15 minutes or forward Copernicus Data Space PULL subscription notifications to it. This is **near-real-time orbital monitoring**, not a continuous video stream: latency is governed by satellite revisit, ground processing and catalogue publication. The static Vercel UI cannot run a background satellite monitor after the browser closes; the FastAPI service must run on an always-on host.

## Data choices

- Operational catalogue: Copernicus Data Space Sentinel-1 GRD STAC / Subscriptions.
- Prototype pixel access: Microsoft Planetary Computer Sentinel-1 GRD COGs, because AOI windows can be streamed without downloading a full product.
- Training and validation: GlobalOSD-SAR (DOI `10.5281/zenodo.15286918`, CC BY 4.0). Its point labels are suitable for detection screening and look-alike evaluation, but not pixel-mask IoU by themselves.
- Optical corroboration: Sentinel-2 L2A from Planetary Computer; NASA GIBS/VIIRS is optional broad-area context and is too coarse to be the primary detector for small slicks.

The UI's full monitoring cycle remains synthetic and labelled as such. The separate Sentinel-1 reception view uses historic measured SAR.
