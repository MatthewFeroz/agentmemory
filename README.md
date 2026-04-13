# Redis DevRel Memory Demo

Short full-stack demo for showing how Redis-backed memory changes an LLM chat experience over time.

## Project Status

This repo currently has:
- a FastAPI backend that talks to Claude
- a React frontend with three memory modes
- Redis Agent Memory Server handling working memory and long-term memory storage

## Three Phases

### Phase 1: No Memory
The assistant is stateless. Every message is treated as a brand-new request.

### Phase 2: Short-Term Memory
The assistant remembers the current conversation thread. This is session memory tied to a `session_id`.

### Phase 3: Long-Term Memory
The assistant remembers facts across chats. This uses a stable `user_id`, archived conversations, and a remembered-facts layer backed by Redis through Agent Memory Server.

## Current Demo State

The app now supports:
- `No Memory` mode
- `Short-Term Memory` mode
- `Long-Term Memory` mode with chat archive and remembered facts panel