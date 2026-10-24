#!/bin/sh
# Start the API inside the container and the Streamlit app on the public port.
set -e

# First start: seed the cache with the demo snapshot (copies only files not already there).
cp -rn /srv/data/demo_cache/. /srv/data/cache/ 2>/dev/null || true

# The API is only reachable from inside the container; the app talks to it over HTTP.
uvicorn app.main:app --app-dir /srv/backend --host 127.0.0.1 --port 8000 &

exec streamlit run /srv/frontend/app.py \
    --server.address 0.0.0.0 \
    --server.port "${PORT:-7860}" \
    --server.headless true \
    --browser.gatherUsageStats false
