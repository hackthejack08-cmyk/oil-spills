FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libgomp1 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt
COPY backend /srv/backend
COPY frontend /srv/frontend
COPY demo /srv/demo
WORKDIR /srv/backend
ENV OSI_DATA_DIR=/srv/runtime OSI_DEMO_DIR=/srv/demo/data OSI_MODEL_DIR=/srv/models
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
