# Redis DevRel Memory Demo

[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/frontend-React-61DAFB?logo=react&logoColor=000)](https://react.dev/)
[![Redis](https://img.shields.io/badge/memory-Redis-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![Docker Compose](https://img.shields.io/badge/runtime-Docker_Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Last Commit](https://img.shields.io/github/last-commit/MatthewFeroz/redis-devrel)](https://github.com/MatthewFeroz/redis-devrel/commits/main)
[![Top Language](https://img.shields.io/github/languages/top/MatthewFeroz/redis-devrel)](https://github.com/MatthewFeroz/redis-devrel)

Short full-stack demo for showing how Redis-backed memory changes an LLM chat experience over time.

## What Runs

This repo runs as four services:
- `frontend`: static React app served by Nginx
- `backend`: FastAPI app that owns the HTTP API and Anthropic calls
- `agent-memory-server`: Redis Agent Memory Server (AMS)
- `redis`: Redis Open Source with search and JSON modules

## Prerequisites

Install these before you start:
- Docker Desktop
- An Anthropic API key
- A Hugging Face token for the default AMS embedding model

Verify Docker is running before you continue.

## Quick Start

1. Copy `.env.example` to `.env`.
2. Open `.env` and replace the placeholder secrets.
3. From the repo root, run:

```bash
docker compose up --build
```

If you want the stack to keep running in the background, use:

```bash
docker compose up --build -d
```

Compose keeps host-facing and container-facing addresses separate on purpose:
- your browser uses `localhost:*`
- containers talk to each other by service name such as `backend`, `agent-memory-server`, and `redis`
- the Compose file overrides internal URLs so you do not have to rewrite app config by hand

## First-Run Checks

After startup, verify these URLs:
- Frontend: `http://localhost:3000`
- Backend docs: `http://localhost:8000/docs`
- Backend health: `http://localhost:8000/health`
- Agent Memory Server health: `http://localhost:32769/v1/health`

You can also inspect the running services with:

```bash
docker compose ps
```

Expected services:
- `frontend`
- `backend`
- `agent-memory-server`
- `redis`

## Required Environment Variables

Set these in `.env` before startup:
- `ANTHROPIC_API_KEY`
- `HF_TOKEN`

The rest of the defaults in `.env.example` are already wired for local Docker Compose.

## Stopping The Stack

To stop the running services:

```bash
docker compose down
```

To stop services and also remove the Redis data volume:

```bash
docker compose down -v
```

Use `-v` only if you want to wipe stored memory and start fresh.

## Troubleshooting

### Port Already Allocated

If Compose fails with a message like `Bind for 0.0.0.0:6379 failed: port is already allocated`, another local process or container is already using that host port.

Common fixes:
- stop the conflicting container or process
- change the host port in `.env`

Example alternate ports:

```env
FRONTEND_PORT=3001
BACKEND_PORT=8001
AMS_PORT=32770
REDIS_PORT=6380
```

Then rerun:

```bash
docker compose up --build
```

### Frontend Loads But Chat Fails

Check:
- `http://localhost:8000/health`
- `http://localhost:32769/v1/health`
- `docker compose logs --tail 100`

If the backend is up but chat requests fail, the usual causes are:
- missing or invalid `ANTHROPIC_API_KEY`
- missing or invalid Hugging Face token for AMS embeddings
- one of the services exited during startup
