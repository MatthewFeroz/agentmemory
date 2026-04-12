# Redis DevRel — AI Chat Backend

Backend-first starter for the Redis DevRel technical project.

This is a **documented FastAPI service** that connects to Anthropic's Claude API (Haiku 4.5). Every file and function is thoroughly commented so the code can be explained line-by-line during a technical presentation.

Redis-backed memory is intentionally left for the next phase so the application foundation stays easy to explain and demo.

## Repo Layout

```
backend/
  app/
    main.py             ← FastAPI app, endpoints (/chat, /health)
    config.py           ← Environment variable loading via pydantic-settings
    models.py           ← Pydantic request/response schemas
    services/
      anthropic.py      ← Anthropic SDK wrapper (Claude client)
.env.example            ← Template for required environment variables
requirements.txt        ← Pinned Python dependencies
```

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your environment variables
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 4. Start the server
uvicorn backend.app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** to interact with the API via Swagger UI.

## API Endpoints

| Method | Path      | Description                          |
|--------|-----------|--------------------------------------|
| GET    | `/health` | Server health check + model info     |
| POST   | `/chat`   | Send a message to Claude, get a reply|

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

- **FastAPI** — async Python web framework with auto-generated docs
- **Anthropic SDK** (v0.94.0) — official Python client for Claude
- **Claude Haiku 4.5** — fast, cost-effective model for conversational AI
- **pydantic-settings** — typed environment variable management

## What's Next (Task 2)

The `session_id` field in the API is a placeholder for Redis integration:
- **Short-term memory**: Store chat history per session in Redis
- **Long-term memory**: Extract and persist facts/preferences across sessions
- **Semantic retrieval**: Use Redis Vector Library for relevant memory lookup
