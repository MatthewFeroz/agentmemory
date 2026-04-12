# AGENTS.md

## Project Purpose

This repository is for a Redis DevRel technical project centered on AI memory management.

The final goal is to build and present a demo application that shows how Redis can help an LLM remember information across conversations. The project starts with a basic chat application and then evolves into a Redis-backed memory system with:

- short-term memory for active chat context
- long-term memory for facts and preferences that persist across chats
- a presentation-ready demo that clearly shows memory being stored and retrieved

## Current Scope

The current codebase only implements the backend foundation.

At this stage, the backend:
- exposes an API for chat requests
- connects to the Anthropic API
- returns Claude responses to the caller

At this stage, the backend does not yet:
- store chat history in Redis
- retrieve relevant memories from Redis
- implement cross-session memory

## Planned Evolution

The intended build sequence for this repository is:

1. Backend-only chat service using Anthropic
2. Redis integration for session persistence
3. RedisVL integration for semantic retrieval and message history
4. Agent memory support for long-term memory across chats
5. Demo and presentation materials for the Redis DevRel team

## Engineering Intent

The code should remain easy to explain in a live demo. That means:

- clear structure
- explicit comments
- simple API contracts
- small, understandable modules
- room to layer Redis features in incrementally
