#!/usr/bin/env bash
set -e

# Backend: FastAPI, bound to loopback only — never reachable from outside
# the container. Streamlit talks to it over http://localhost:8000, same
# as it does in local dev, so no code changes needed for this part.
uvicorn src.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Give the backend a moment to come up before the frontend starts hitting it.
sleep 2

# Frontend: Streamlit — this is the port Hugging Face exposes publicly.
streamlit run streamlit_app.py \
    --server.port 7860 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false &
FRONTEND_PID=$!

# If either process dies, bring the whole container down so Hugging Face
# restarts it, rather than silently limping along with only one half
# working.
wait -n "$BACKEND_PID" "$FRONTEND_PID"
exit $?