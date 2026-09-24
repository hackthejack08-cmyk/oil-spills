# Continuous oil-spill monitoring

OSI monitors an area, not one uploaded image. Each cycle:

1. Query Sentinel-1 GRD products intersecting the AOI and time window.
2. Ignore product IDs already completed for that monitor.
3. Stream and calibrate only the AOI pixels.
4. Run segmentation and retain every slick candidate above the review threshold.
5. Keep separate candidates from the same acquisition, then link nearby detections from later passes into incidents.
6. Run drift and AIS attribution per selected incident; never treat a vessel score as proof.

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

For an always-on deployment, invoke the scan endpoint every 15 minutes or forward Copernicus Data Space PULL subscription notifications to it. The static Vercel UI cannot run a background satellite monitor after the browser closes; the FastAPI service must run on an always-on host.

## Data choices

- Operational catalogue: Copernicus Data Space Sentinel-1 GRD STAC / Subscriptions.
- Prototype pixel access: Microsoft Planetary Computer Sentinel-1 GRD COGs, because AOI windows can be streamed without downloading a full product.
- Training and validation: GlobalOSD-SAR (DOI `10.5281/zenodo.15286918`, CC BY 4.0). Its point labels are suitable for detection screening and look-alike evaluation, but not pixel-mask IoU by themselves.

The UI's full monitoring cycle remains synthetic and labelled as such. The separate Sentinel-1 reception view uses historic measured SAR.
