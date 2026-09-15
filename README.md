---
title: Blue Horizon Concierge
emoji: 🏨
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Blue Horizon Concierge

A hotel concierge chat app: FastAPI backend (intent routing, booking/
cancellation slot-filling, SQL + RAG lookups) with a Streamlit frontend,
both running in this one container.

## Required secrets

Set these under **Space settings → Variables and secrets** (never commit
them to the repo):

| Name | Purpose |
|---|---|
| `OPENAI_API_KEY` | LLM calls (intent classification, extraction, RAG, etc.) |
| `MODEL_NAME` | Model to use, e.g. `gpt-4o-mini` |
| `DATABASE_URL` | Postgres connection string (Supabase/Railway/Neon) |
| `REDIS_URL` | Redis connection string, for session storage |
| `FRONTEND_ORIGIN` | Optional — see note below |

`FRONTEND_ORIGIN` mainly matters if a browser ever calls the FastAPI API
directly. Since Streamlit calls it server-side over localhost inside this
same container, it's not strictly required here, but it's good practice
to leave it set correctly in case that changes later.

## Notes for Postgres

Supabase/Neon typically require SSL — if your DB URL doesn't already
include it, append `?sslmode=require`.

## Cold starts

Free-tier CPU Spaces sleep after a period of inactivity; the first
request after a sleep will be slow while the container restarts.