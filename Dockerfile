# One container with both the API and the Streamlit app, for a single public link
# (Hugging Face Spaces, Render, or any host that runs a Dockerfile).
# Local multi-container setup is still docker-compose.yml.
FROM python:3.12-slim

WORKDIR /srv
COPY backend/requirements.txt backend-requirements.txt
COPY frontend/requirements.txt frontend-requirements.txt
RUN pip install --no-cache-dir -r backend-requirements.txt -r frontend-requirements.txt

COPY backend/app backend/app
COPY frontend frontend
# Validation results the source selection learns from, and a snapshot of cached CIMIS and
# Open-Meteo data so the demo orchards load fast (and work without a CIMIS key).
COPY data/validation data/validation
COPY data/demo_cache data/demo_cache
COPY start.sh start.sh

# Hosts that run containers as a non-root user (e.g. Hugging Face) need to write the cache.
RUN mkdir -p data/cache && chmod -R a+rwX data

ENV DATA_DIR=/srv/data \
    DATA_SOURCE=cimis \
    BACKEND_URL=http://127.0.0.1:8000 \
    PYTHONUNBUFFERED=1

# The public port: hosts set $PORT (Render); Hugging Face uses 7860.
EXPOSE 7860
CMD ["sh", "start.sh"]
