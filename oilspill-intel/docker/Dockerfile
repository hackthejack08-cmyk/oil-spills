FROM python:3.11-slim

ARG INSTALL_ML=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OSI_DATA_DIR=/srv/runtime \
    OSI_DEMO_DIR=/srv/demo/data \
    OSI_MODEL_DIR=/srv/models

RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY backend/requirements-runtime.txt /srv/requirements-runtime.txt
RUN pip install --no-cache-dir -r /srv/requirements-runtime.txt \
 && if [ "$INSTALL_ML" = "1" ]; then \
      pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir segmentation-models-pytorch; \
    fi

RUN useradd --create-home --uid 10001 osi \
 && mkdir -p /srv/runtime/uploads /srv/models \
 && chown -R osi:osi /srv
COPY --chown=osi:osi backend/app /srv/backend/app
COPY --chown=osi:osi frontend/static /srv/frontend/static
COPY --chown=osi:osi demo/data /srv/demo/data

USER osi
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
