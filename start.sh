#!/bin/sh
# Start the API inside the container and the Streamlit app on the public port.
set -e

# First start: seed the cache with the demo snapshot (copies only files not already there).
cp -rn /srv/data/demo_cache/. /srv/data/cache/ 2>/dev/null || true

# The API is only reachable from inside the container; the app talks to it over HTTP.
uvicorn app.main:app --app-dir /srv/backend --host 127.0.0.1 --port 8000 &

# Warm up the evidence summary (about 15 s the first time) so the first visitor doesn't wait.
(sleep 20; python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/evidence', timeout=300)" || true) &

# Run from the frontend folder so Streamlit picks up frontend/.streamlit/config.toml (theme).
cd /srv/frontend
exec streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port "${PORT:-7860}" \
    --server.headless true \
    --browser.gatherUsageStats false
