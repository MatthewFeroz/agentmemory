# Redis DevRel Memory Demo

Short full-stack demo for showing how Redis-backed memory changes an LLM chat experience over time.

## What Runs

This repo now packages cleanly into four runtime pieces:
- `frontend`: static React build served by Nginx
- `backend`: FastAPI app that calls Claude and Agent Memory Server
- `agent-memory-server`: Redis Agent Memory Server (AMS)
- `redis`: official Redis Open Source image for working memory, archival data, and search/vector features in Redis 8

Request flow stays easy to explain during a walkthrough:

1. The browser calls the frontend container.
2. Nginx proxies `/api/*` requests to the FastAPI backend.
3. The backend sends chat requests to Anthropic and memory requests to AMS.
4. AMS persists and searches memory in Redis.

That separation matters for the demo because the backend still teaches the right architecture:
- `AnthropicService` owns model calls.
- `MemoryService` owns AMS calls.
- Redis remains visible as the persistence layer, not hidden inside the app.

## Docker Deployment

The simplest way to share this project with other people is a single `docker compose` stack.

1. Copy `.env.example` to `.env`.
2. Fill in the real API keys you want to use.
3. Run:

```bash
docker compose up --build
```

Compose keeps host-facing and container-facing addresses separate on purpose:
- your browser uses `localhost:*`
- containers talk to each other by service name such as `backend`, `agent-memory-server`, and `redis`
- the Compose file overrides internal URLs so you do not have to rewrite the app config by hand

After startup:
- Frontend: `http://localhost:3000`
- Backend docs: `http://localhost:8000/docs`
- Backend health: `http://localhost:8000/health`
- Agent Memory Server: `http://localhost:32769`

## Why One Compose File

This project is a good fit for one Compose file because each service has a single, teachable responsibility:
- `frontend` packages the UI and reverse proxy.
- `backend` packages the application logic and API contract.
- `agent-memory-server` packages the Redis-native memory layer.
- `redis` packages persistence and search.

For this demo, AMS runs in `asyncio` task mode inside one container. That is the right default for other people running the project locally because it removes the need for a separate background worker. If you later want a more production-like AMS deployment, switch AMS to its default Docket backend and run a dedicated `task-worker` container.

## Version Strategy

Do not default to floating `latest` tags in your shared Compose file. That gives you newer images, but it also makes the demo less reproducible.

Use this policy instead:
- Pin known-good Redis image tags in `.env.example`.
- Pin known-good AMS image tags in `.env.example`.
- Pin Python packages in `requirements.txt`.
- Upgrade intentionally, then rebuild and smoke-test the stack.

As of April 13, 2026, the current upstream versions I verified were:
- `redislabs/agent-memory-server:0.15.2`
- `redis:8.6.2`
- `agent-memory-client==0.14.0`
- `fastapi==0.135.3`
- `uvicorn==0.44.0`
- `anthropic==0.94.1`

## Three Memory Modes

### No Memory

The assistant is stateless. Every message is treated as a brand-new request.

### Short-Term Memory

The assistant remembers the current conversation thread. This is session memory tied to a `session_id`.

### Long-Term Memory

The assistant remembers facts across chats. This uses a stable `user_id`, archived conversations, and a remembered-facts layer backed by Redis through Agent Memory Server.
