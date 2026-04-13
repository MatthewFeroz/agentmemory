"""Redis-backed short-term memory service via Agent Memory Server.

This file exists to keep all "memory system" logic in one place.

Why a separate service?
1. main.py should stay focused on HTTP request handling.
2. AnthropicService should stay focused on talking to Claude.
3. Agent Memory Server has its own SDK, response models, and concepts.
   Hiding that behind MemoryService makes the rest of the app easier to read.

For Task 2 Part 2, we are intentionally implementing ONLY short-term memory:
- We load a session's working memory before calling Claude.
- We append the newest user + assistant turn after Claude responds.
- Agent Memory Server stores that working memory in Redis for us.

Long-term memory will be added later as a separate layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_memory_client import MemoryAPIClient, MemoryClientConfig
from agent_memory_client.models import MemoryMessage, WorkingMemory

from backend.app.config import Settings


class MemoryService:
    """
    Small wrapper around the Agent Memory Server Python SDK.

    This service exposes exactly the two operations our chat endpoint needs:
    1. load_conversation_history(session_id)
    2. store_conversation_turn(session_id, user_message, assistant_message)

    That keeps the rest of the application from needing to know:
    - which SDK class we use
    - what a WorkingMemory object looks like
    - that put_working_memory() replaces the full message list
    """

    def __init__(self, settings: Settings) -> None:
        """
        Create the Agent Memory Server client.

        Important detail:
        The client talks to Agent Memory Server over HTTP.
        Agent Memory Server then handles persistence into Redis.
        So the architecture is:

            FastAPI app -> MemoryAPIClient -> Agent Memory Server -> Redis

        not:

            FastAPI app -> Redis directly
        """
        # Save settings so other methods can access the namespace if needed.
        self._settings = settings

        # MemoryClientConfig only needs the base URL for the server in our
        # simple setup. We do not pass auth headers because the local Docker
        # instance is running in a simple development configuration.
        self._config = MemoryClientConfig(
            base_url=settings.memory_api_url,
            default_namespace=settings.memory_namespace,
        )

        # The SDK client is async-capable even though creating the object is
        # synchronous. Actual network calls happen in async methods below.
        self._client = MemoryAPIClient(self._config)

    async def load_conversation_history(self, session_id: str) -> list[dict]:
        """
        Return prior chat messages in Anthropic's expected format.

        Output shape:
            [
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."},
            ]

        Why we transform the messages:
        Agent Memory Server returns MemoryMessage objects.
        Anthropic expects a plain list of dictionaries.
        This method is the adapter between those two formats.
        """
        # get_or_create_working_memory() does two jobs:
        #   1. If the session already exists, it returns the existing memory.
        #   2. If the session does NOT exist, it creates an empty session.
        #
        # That means our chat endpoint can stay simple: it never has to worry
        # about "does this session already exist?" branching logic.
        _created, working_memory = await self._client.get_or_create_working_memory(
            session_id=session_id,
            namespace=self._settings.memory_namespace,
        )

        # Convert the SDK model objects into the exact message structure that
        # AnthropicService.chat() already understands.
        conversation_history: list[dict] = []
        for message in working_memory.messages:
            conversation_history.append(
                {
                    "role": message.role,
                    "content": message.content,
                }
            )

        return conversation_history

    async def store_conversation_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """
        Append the latest user/assistant exchange into working memory.

        Critical behavior to understand:
        Agent Memory Server's put_working_memory() REPLACES the session's
        working memory. It does not append automatically.

        So the algorithm here must be:
        1. Read the current working memory
        2. Copy its existing messages
        3. Add the new user message
        4. Add the new assistant message
        5. Write the entire updated list back
        """
        # First fetch the latest stored state so we don't accidentally throw
        # away older messages when we write the update.
        _created, existing_memory = await self._client.get_or_create_working_memory(
            session_id=session_id,
            namespace=self._settings.memory_namespace,
        )

        # We create explicit UTC timestamps for every message.
        # This keeps ordering unambiguous and avoids SDK warnings about
        # missing created_at values.
        now = datetime.now(UTC)

        # Start from the full existing message list. This preserves earlier
        # turns because put_working_memory() expects the entire replacement
        # document, not just the new delta.
        updated_messages = list(existing_memory.messages)

        # Append the new user message.
        updated_messages.append(
            MemoryMessage(
                role="user",
                content=user_message,
                created_at=now,
            )
        )

        # Append Claude's reply as the assistant message in the same turn.
        updated_messages.append(
            MemoryMessage(
                role="assistant",
                content=assistant_message,
                created_at=now,
            )
        )

        # Build the full WorkingMemory payload that will replace the stored
        # session state on the server.
        #
        # We carry forward existing fields so this method is conservative:
        # it updates messages while preserving any context/memories/data that
        # Agent Memory Server may already be tracking for the same session.
        updated_working_memory = WorkingMemory(
            session_id=session_id,
            namespace=existing_memory.namespace,
            user_id=existing_memory.user_id,
            context=existing_memory.context,
            data=existing_memory.data,
            memories=existing_memory.memories,
            messages=updated_messages,
            long_term_memory_strategy=existing_memory.long_term_memory_strategy,
            ttl_seconds=existing_memory.ttl_seconds,
            last_accessed=now,
        )

        # Write the entire updated working memory document back to the server.
        await self._client.put_working_memory(
            session_id=session_id,
            memory=updated_working_memory,
        )

    async def close(self) -> None:
        """
        Close the underlying HTTP client.

        We call this during FastAPI shutdown so open network resources are
        cleaned up explicitly instead of being left to process teardown.
        """
        # The SDK exposes an async close even though its signature looks
        # deceptively small at first glance. We await it so the underlying
        # HTTP transport shuts down cleanly.
        await self._client.close()
