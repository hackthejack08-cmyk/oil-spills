# Docker deployment

The default image is deliberately small and runs the transparent baseline detector. Build with `INSTALL_ML=1` only when a trained U-Net checkpoint will be mounted at `/srv/models/unet_s1_oil.pt`.

## Run locally

```bash
docker compose up --build
```

Open `http://localhost:8000`. Uploaded data and the SQLite evidence ledger persist in the `osi_runtime` volume.

## Use authorized historical AIS automatically

Place the provider CSV at `deployment-data/ais.csv` and set this in `.env`:

```text
OSI_DEFAULT_AIS_CSV=/srv/imports/ais.csv
```

Without that file, an uploaded image still receives detection, geometry, hindcast and forecast results. Vessel ranking is intentionally omitted rather than fabricated.

## Publish to Docker Hub

Create Docker Hub access-token secrets named `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` in the GitHub repository. Run the **Publish Docker image** workflow, or push a version tag. It publishes:

```text
DOCKERHUB_USERNAME/osi-sih26143:latest
```

Deploy that image elsewhere with:

```bash
OSI_IMAGE=DOCKERHUB_USERNAME/osi-sih26143:latest docker compose pull
OSI_IMAGE=DOCKERHUB_USERNAME/osi-sih26143:latest docker compose up -d --no-build
```

For an agency deployment, put TLS and authentication in front of port 8000, store private AIS outside the image, restrict the runtime volume, and keep one application worker because the current job queue and SQLite database are single-node components.

## Scale after the pilot

Keep the same API and UI, but replace the in-process job dictionary with Redis + a worker queue, SQLite with PostgreSQL/PostGIS, and uploaded rasters with S3-compatible object storage. Multiple stateless API replicas can then sit behind a load balancer. These services are deliberately not required for the laptop/hackathon build.
