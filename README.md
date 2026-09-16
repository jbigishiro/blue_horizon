# 🏨 Blue Horizon — AI Concierge for Luxury Hotels

An AI-powered concierge system for a fictional luxury hotel. Guests can ask about
amenities and policies, check room availability and pricing, and actually book or
cancel a stay — all through natural conversation.

Built as an end-to-end learning project covering schema design, text-to-SQL,
retrieval-augmented generation (RAG), structured multi-turn agent design, and
production deployment.

## What it does

- **Answers policy/amenity questions** by retrieving from a vector index of the
  hotel's FAQs, amenities, and local recommendations (RAG)
- **Answers data questions** ("what rooms are available?", "how many bookings do we
  have today?") by generating and safely executing SQL against a real Postgres
  database (text-to-SQL)
- **Books and cancels rooms** through a structured, multi-turn conversation with an
  explicit confirm-before-acting step — no reservation is ever created or cancelled
  without the guest saying yes first
- **Routes each message automatically** to whichever of the above actually applies,
  or just responds to small talk

## Architecture

```mermaid
flowchart TD
    UI["Streamlit chat UI"] -->|HTTP| API["FastAPI /chat endpoint"]
    API --> Orchestrator["Intent Router"]
    Orchestrator -->|knowledge question| RAG["RAG Agent<br/>(LlamaIndex + Redis)"]
    Orchestrator -->|data question| SQL["Text-to-SQL Agent<br/>(validated, read-only)"]
    Orchestrator -->|booking/cancellation| Action["Booking Agent<br/>(structured slot-filling)"]
    Orchestrator -->|small talk| Chat["Chitchat"]
    RAG --> Redis[(Redis<br/>vector index)]
    SQL --> PGRead[(Postgres<br/>read-only role)]
    Action --> PGWrite[(Postgres<br/>full-access role)]
    API --> Session[(Redis<br/>session state)]
```

Two data stores, deliberately separated by access level:
- **Postgres (Neon)** — structured hotel data across 19 tables (rooms, bookings,
  staff, payments, feedback, etc.). The SQL agent connects through a **dedicated
  read-only role** so a bad SQL generation can never modify data, regardless of
  what the application-level validation catches.
- **Redis** — both the vector index for RAG (FAQs/amenities/recommendations
  embedded via OpenAI) and per-session conversation state (what's being booked,
  what's awaiting confirmation).

## Tech stack

| Layer | Technology |
|---|---|
| LLM | OpenAI (GPT-4o)
| Structured data | Neon Postgres + SQLAlchemy |
| Vector search / session state | Redis + LlamaIndex |
| Backend API | FastAPI + slowapi (rate limiting) |
| Frontend | Streamlit |
| Testing | pytest |
| Deployment | Render 

## Project structure

```
blue-horizon/
├── scr/
|   ├──agents/
|      ├── booking_agent.py     # The only module allowed to write bookings
|      ├── nl2sql_agent.py      # Text-to-SQL: generate, validate, execute
|      ├── rag_agent.py         # RAG query against the Redis vector index
|      ├── orchestrator.py      # Intent classification + dispatch
|   ├──config/
|      ├──constants.py          # constants value are stored here
|   ├──utils/
|      ├── db.py                # Database engine/session setup
|      ├── models.py            # SQLAlchemy models
|      ├── redis_index.py       # Shared Redis index schema
|      ├── schema_context.py    # Auto-generates DB schema description for the LLM
|      ├── seed_db.py           # Loads CSV data into Postgres
|      └── session_store.py      # Redis-backed per-session conversation state
|   ├──data/                    # Source CSVs
│   ├── main.py                 # FastAPI app, session state, /chat endpoint
│   └── streamlit_app.py        # Chat UI

├── requirements.txt         # stores the required libraries
└── .env                     # Not committed 
```

## Setup

### 1. Prerequisites
- Python 3.12
- A [Neon](https://neon.tech) Postgres database
- A Redis instance with the Search/Query capability enabled (Redis Cloud or Upstash)
- An OpenAI API key

### 2. Environment variables
Create `.env` in the project root:

```dotenv
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
READONLY_DATABASE_URL=postgresql://readonly_user:pass@host/db?sslmode=require
REDIS_URL=redis://default:pass@host:port
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o                 
```

### 3. Install and seed
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python src/utils/seed_db.py --reset       # loads data/*.csv into Postgres
python src/utils/redis_index.py --reset  # embeds FAQs/amenities into Redis
```

### 4. Run locally
```bash
# terminal 1 — backend
cd src
uvicorn main:app --reload --port 8000

# terminal 2 — frontend
cd src
streamlit run streamlit_app.py
```

## Testing

```bash
pip install pytest pytest-mock
pytest -v
```

## Deployment

Currently deployed on:
- **Backend**: Render (`render.yaml` included)

See `render.yaml` for required environment variables. CORS is restricted via
`FRONTEND_ORIGIN` once both services are live.

## Known limitations

Being upfront about what this is and isn't:

- **No real authentication.** The "Guest ID" field is a plain number input
  simulating a logged-in session — anyone can act as any customer ID. Not
  suitable for real guest data without a proper auth layer.
- **Conversation history isn't used for cross-topic context.** The SQL and RAG
  agents each see only the current message — a follow-up like "what about a
  cheaper one?" right after an unrelated question won't have context. The
  booking agent's structured draft is the one place multi-turn memory is fully
  reliable.

