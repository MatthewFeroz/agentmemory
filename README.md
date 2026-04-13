# Redis DevRel - AI Chat App

Full-stack starter for the Redis DevRel technical project.

This repo contains:
- a documented FastAPI backend that connects to Anthropic's Claude API
- a React + Vite frontend for chatting with the backend

The code is intentionally heavily commented so the implementation can be
explained line-by-line during a technical presentation.

## Repo Layout

```text
backend/
  app/
    main.py             <- FastAPI app, endpoints (/chat, /health)
    config.py           <- Environment variable loading via pydantic-settings
    models.py           <- Pydantic request/response schemas
    services/
      anthropic.py      <- Anthropic SDK wrapper (Claude client)
      memory.py         <- Agent Memory Server wrapper for short-term memory
frontend/
  src/
    App.jsx             <- Main chat UI
  package.json          <- Frontend dependencies and scripts
  bun.lock              <- Bun lockfile
.env.example            <- Template for required environment variables
requirements.txt        <- Pinned Python dependencies
```

## Quick Start

```bash
# 1. Create and activate a virtual environment for the backend
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# 2. Install backend dependencies
pip install -r requirements.txt

# 3. Install frontend dependencies with Bun
cd frontend
bun install
cd ..

# 4. Set up your environment variables
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY and MEMORY_API_URL if needed

# 5. Start the backend
uvicorn backend.app.main:app --reload --port 8000

# 6. Start the frontend
cd frontend
bun run dev
```

Open:
- `http://localhost:8000/docs` for the FastAPI Swagger UI
- `http://localhost:5173` for the frontend chat app

## API Endpoints

| Method | Path      | Description                            |
|--------|-----------|----------------------------------------|
| GET    | `/health` | Server health check + model info       |
| POST   | `/chat`   | Send a message to Claude, get a reply  |

## Example Usage

```bash
# Health check
curl http://localhost:8000/health

# Send a chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, my name is Matthew!", "session_id": "demo-1"}'
```

## Tech Stack

- FastAPI - async Python web framework with auto-generated docs
- Anthropic SDK - official Python client for Claude
- Agent Memory Server client - short-term memory via Redis-backed working memory
- React 19 + Vite - frontend chat interface
- Bun - frontend package manager and lockfile
- pydantic-settings - typed environment variable management
