"""Redis-backed short-term and long-term memory service via Agent Memory Server.

This file exists to keep all "memory system" logic in one place.

Why a separate service?
1. main.py should stay focused on HTTP request handling.
2. AnthropicService should stay focused on talking to Claude.
3. Agent Memory Server has its own SDK, response models, and concepts.
   Hiding that behind MemoryService makes the rest of the app easier to read.

This service now covers both memory layers used in the demo:
- Short-term memory: session transcript stored in working memory
- Long-term memory: reusable facts tied to a stable user_id
- Archive support: list and reload past long-term chat sessions
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import httpx
from agent_memory_client import MemoryAPIClient, MemoryClientConfig
from agent_memory_client.filters import UserId
from agent_memory_client.models import (
    ClientMemoryRecord,
    MemoryMessage,
    WorkingMemory,
)

from backend.app.config import Settings


class MemoryService:
    """
    Small wrapper around the Agent Memory Server Python SDK.

    This service hides the SDK details behind app-specific operations such as:
    1. loading/storing one session's transcript
    2. listing archived chats for one long-term user identity
    3. storing and searching explicit long-term facts

    That keeps the rest of the application from needing to know:
    - which SDK class we use
    - what a WorkingMemory object looks like
    - how long-term fact records are created and searched
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

        # We store long-term facts in a dedicated per-user profile session.
        # This keeps the implementation easy to explain:
        # - normal session_id values store chat transcripts
        # - one hidden profile session per user stores reusable facts
        #
        # Important design note:
        # We store these facts as AMS memory records inside working memory so
        # the data model still matches Agent Memory Server concepts. If the
        # AMS long-term vector search path is fully configured later, the same
        # facts can also be pushed into native long-term memory search.
        self._profile_session_prefix = "long-term-profile"
        self._max_retries = 3

    async def load_working_memory(
        self,
        session_id: str,
        user_id: str | None = None,
    ) -> WorkingMemory:
        """
        Return the full working memory document for one session.

        We keep this helper separate from load_conversation_history() because
        some backend features need more than just the message list:
        - archive listing needs metadata from working_memory.data
        - archived chat reload needs the whole transcript
        - long-term chat persistence needs the stored user_id relationship
        """
        _created, working_memory = await self._with_retry(
            self._client.get_or_create_working_memory,
            session_id=session_id,
            user_id=user_id,
            namespace=self._settings.memory_namespace,
        )

        return working_memory

    async def load_conversation_history(
        self,
        session_id: str,
        user_id: str | None = None,
    ) -> list[dict]:
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
        working_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
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
        user_id: str | None = None,
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
        existing_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
        )

        # We create explicit UTC timestamps for every message.
        # This keeps ordering unambiguous and avoids SDK warnings about
        # missing created_at values.
        now = datetime.now(UTC)

        # Start from the full existing message list. This preserves earlier
        # turns because put_working_memory() expects the entire replacement
        # document, not just the new delta.
        updated_messages = list(existing_memory.messages)

        updated_data = self._build_chat_data(
            existing_data=existing_memory.data,
            user_message=user_message,
            assistant_message=assistant_message,
            updated_messages=updated_messages,
            now=now,
        )

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
            user_id=user_id or existing_memory.user_id,
            context=existing_memory.context,
            data=updated_data,
            memories=existing_memory.memories,
            messages=updated_messages,
            long_term_memory_strategy=existing_memory.long_term_memory_strategy,
            ttl_seconds=existing_memory.ttl_seconds,
            last_accessed=now,
        )

        # Write the entire updated working memory document back to the server.
        await self._with_retry(
            self._client.put_working_memory,
            session_id=session_id,
            memory=updated_working_memory,
            user_id=user_id,
        )

    async def list_long_term_chats(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """
        Return archived chat summaries for one long-term user identity.

        Agent Memory Server already stores sessions keyed by session_id and can
        filter them by user_id. We use that built-in capability so we do not
        need a second archive index in Redis.
        """
        session_list = await self._with_retry(
            self._client.list_sessions,
            limit=limit,
            namespace=self._settings.memory_namespace,
            user_id=user_id,
        )

        chats: list[dict] = []
        for session_id in session_list.sessions:
            if self._is_profile_session(session_id):
                continue
            working_memory = await self.load_working_memory(
                session_id=session_id,
                user_id=user_id,
            )
            chats.append(self._build_chat_summary(working_memory))

        # Sort newest-first so the archive dropdown feels natural in the UI.
        chats.sort(
            key=lambda chat: chat["last_updated"] or "",
            reverse=True,
        )

        return chats

    async def load_long_term_chat(
        self,
        session_id: str,
        user_id: str,
    ) -> dict:
        """
        Load one archived long-term chat transcript and its display metadata.

        This is used when the frontend user selects an older chat from the
        archive and needs the transcript restored into the interface.
        """
        working_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
        )

        summary = self._build_chat_summary(working_memory)
        return {
            "session_id": session_id,
            "label": summary["label"],
            "messages": self._working_memory_to_messages(working_memory),
        }

    async def store_long_term_facts(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> None:
        """
        Extract a small set of explicit user/project facts and store them.

        For the demo, we want something easy to explain live:
        - a user states a fact clearly
        - we persist that fact into long-term memory
        - a later chat can retrieve it by user_id

        We intentionally use a narrow, explicit extractor instead of a
        "store everything automatically" strategy because it is predictable
        during the presentation.
        """
        extracted_memories = self._extract_long_term_memories(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
        )
        if not extracted_memories:
            return

        profile_memory = await self._load_long_term_profile(user_id)
        now = datetime.now(UTC)
        existing_data = dict(profile_memory.data or {})
        existing_memories = list(profile_memory.memories)
        existing_texts = {
            memory.text
            for memory in existing_memories
            if hasattr(memory, "text")
        }

        new_memories = [
            memory
            for memory in extracted_memories
            if memory.text not in existing_texts
        ]
        if not new_memories:
            return

        updated_memories = [*existing_memories, *new_memories]

        updated_profile = WorkingMemory(
            session_id=self._profile_session_id(user_id),
            namespace=profile_memory.namespace,
            user_id=user_id,
            context=profile_memory.context,
            data={
                **existing_data,
                "chat_label": "Long-term Profile",
                "last_updated": now.isoformat(),
                "source_session_id": session_id,
            },
            memories=updated_memories,
            messages=profile_memory.messages,
            long_term_memory_strategy=profile_memory.long_term_memory_strategy,
            ttl_seconds=profile_memory.ttl_seconds,
            last_accessed=now,
        )

        await self._with_retry(
            self._client.put_working_memory,
            session_id=self._profile_session_id(user_id),
            memory=updated_profile,
            user_id=user_id,
        )

        # Best-effort AMS native long-term memory indexing.
        #
        # Why "best effort"?
        # In a fully configured Agent Memory Server deployment, this gives us
        # the official long-term vector search path described in the docs.
        # In this local interview/demo environment, the search endpoint can
        # fail if the memory server is missing an embedding provider. We do
        # not let that break the app because the Redis-backed profile session
        # still preserves the remembered facts and keeps the demo explainable.
        try:
            await self._client.create_long_term_memory(new_memories, deduplicate=True)
        except Exception:
            pass

    async def search_long_term_facts(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[str]:
        """
        Search long-term facts that may help answer the current request.

        We run two lightweight searches:
        1. the user's actual message
        2. a generic profile/facts query

        Why both?
        Semantic search works best with a relevant query, but demo prompts like
        "What do you remember about me?" are intentionally broad. The generic
        fallback helps surface profile-style facts such as names/preferences.
        """
        # First try the official AMS long-term search path, but only when the
        # memory server is known to have the extra embedding configuration it
        # needs for semantic retrieval.
        #
        # This project intentionally defaults that feature OFF because the
        # local interview environment uses Anthropic for chat generation but
        # does not provide the extra AMS-side embedding configuration needed
        # for reliable long-term vector search.
        if self._settings.prefer_ams_long_term_search:
            try:
                results = await self._client.search_long_term_memory(
                    text=query,
                    user_id=UserId(eq=user_id),
                    limit=limit,
                )
                native_facts = [
                    memory.text
                    for memory in results.memories
                    if hasattr(memory, "text")
                ]
                if native_facts:
                    return native_facts[:limit]
            except Exception:
                # Fallback to the profile session below.
                pass

        profile_memory = await self._load_long_term_profile(user_id)
        facts = [
            memory.text
            for memory in profile_memory.memories
            if hasattr(memory, "text")
        ]

        if not facts:
            return []

        return self._rank_facts_for_query(facts=facts, query=query, limit=limit)

    def _build_chat_data(
        self,
        existing_data: dict | None,
        user_message: str,
        assistant_message: str,
        updated_messages: list[MemoryMessage],
        now: datetime,
    ) -> dict:
        """
        Build the metadata blob stored alongside a session's transcript.

        Working memory already has a flexible `data` dictionary, which makes it
        a convenient place to store archive-friendly metadata:
        - chat label for the archive dropdown
        - message count for quick summaries
        - a short preview snippet
        - timestamps for sorting
        """
        data = dict(existing_data or {})

        if not data.get("chat_label"):
            data["chat_label"] = self._build_chat_label(user_message)
            data["started_at"] = now.isoformat()

        data["message_count"] = len(updated_messages) + 2
        data["last_updated"] = now.isoformat()
        data["preview"] = self._build_preview(assistant_message or user_message)

        return data

    def _build_chat_summary(self, working_memory: WorkingMemory) -> dict:
        """
        Convert a full working memory object into list-view metadata.

        The archive dropdown does not need the whole transcript up front.
        This summary gives the frontend enough information to label and sort
        stored conversations before it loads one in full.
        """
        data = dict(working_memory.data or {})
        messages = working_memory.messages
        last_updated = data.get("last_updated")

        if last_updated is None and getattr(working_memory, "last_accessed", None):
            last_updated = working_memory.last_accessed.isoformat()

        preview = data.get("preview")
        if preview is None and messages:
            preview = self._build_preview(messages[-1].content)

        return {
            "session_id": working_memory.session_id,
            "label": data.get("chat_label") or f"Chat {working_memory.session_id}",
            "message_count": data.get("message_count") or len(messages),
            "last_updated": last_updated,
            "preview": preview,
        }

    def _working_memory_to_messages(self, working_memory: WorkingMemory) -> list[dict]:
        """
        Normalize stored SDK message objects into API response dictionaries.

        This keeps the archive endpoint payload consistent with the frontend's
        existing message renderer.
        """
        messages: list[dict] = []
        for message in working_memory.messages:
            messages.append(
                {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat()
                    if getattr(message, "created_at", None)
                    else None,
                }
            )

        return messages

    def _extract_long_term_memories(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> list[ClientMemoryRecord]:
        """
        Extract long-term memories from explicit user statements.

        The point of this demo is not "perfect information extraction."
        The point is to show a clean, explainable bridge from user statement
        to persisted memory. We therefore look only for a few clear patterns.
        """
        normalized_message = user_message.strip()
        if not normalized_message:
            return []

        fact_specs = [
            (
                re.compile(r"\bmy name is (?P<value>[^.!?\n]+)", re.IGNORECASE),
                lambda value: (
                    f"The user's name is {value.strip()}.",
                    ["identity", "name"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(r"\bi prefer (?P<value>[^.!?\n]+)", re.IGNORECASE),
                lambda value: (
                    f"The user prefers {value.strip()}.",
                    ["preferences"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    r"\bour audience prefers (?P<value>[^.!?\n]+)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The Redis DevRel audience prefers {value.strip()}.",
                    ["audience", "preferences"],
                    [value.strip(), "Redis DevRel audience"],
                ),
            ),
            (
                re.compile(r"\bwe shipped (?P<value>[^.!?\n]+)", re.IGNORECASE),
                lambda value: (
                    f"The team shipped {value.strip()}.",
                    ["product", "shipping"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(r"\bwe launched (?P<value>[^.!?\n]+)", re.IGNORECASE),
                lambda value: (
                    f"The team launched {value.strip()}.",
                    ["product", "launch"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    r"\bour next conference is (?P<value>[^.!?\n]+)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The next conference is {value.strip()}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
            ),
        ]

        memories: list[ClientMemoryRecord] = []
        seen_texts: set[str] = set()

        for pattern, builder in fact_specs:
            for match in pattern.finditer(normalized_message):
                text, topics, entities = builder(match.group("value"))
                if text in seen_texts:
                    continue
                seen_texts.add(text)
                memories.append(
                    ClientMemoryRecord(
                        text=text,
                        session_id=session_id,
                        user_id=user_id,
                        topics=topics,
                        entities=entities,
                    )
                )

        return memories

    async def _load_long_term_profile(self, user_id: str) -> WorkingMemory:
        """
        Load the hidden profile session that stores reusable long-term facts.

        We implement long-term memory as a dedicated working-memory document
        per user because it is dependable in demos and easy to reason about.
        """
        return await self.load_working_memory(
            session_id=self._profile_session_id(user_id),
            user_id=user_id,
        )

    def _profile_session_id(self, user_id: str) -> str:
        """
        Build the reserved session ID used for a user's long-term profile.
        """
        safe_user_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", user_id).strip("-")
        if not safe_user_id:
            safe_user_id = "default-user"
        return f"{self._profile_session_prefix}-{safe_user_id}"

    def _is_profile_session(self, session_id: str) -> bool:
        """
        Return True when a session ID belongs to the hidden profile store.

        The archive dropdown should show only actual chats, not the internal
        profile record we use to persist long-term facts.
        """
        return session_id.startswith(f"{self._profile_session_prefix}-")

    def _rank_facts_for_query(
        self,
        facts: list[str],
        query: str,
        limit: int,
    ) -> list[str]:
        """
        Rank stored facts against the current query using simple term overlap.

        We deliberately keep this lightweight and deterministic:
        - if the query overlaps stored facts, show the best matches first
        - otherwise, fall back to the most recently stored facts
        """
        query_terms = {
            term
            for term in re.findall(r"[a-z0-9]+", query.lower())
            if len(term) > 2
        }
        scored_facts: list[tuple[int, int, str]] = []

        for index, fact in enumerate(facts):
            fact_terms = {
                term
                for term in re.findall(r"[a-z0-9]+", fact.lower())
                if len(term) > 2
            }
            overlap_score = len(query_terms & fact_terms)
            scored_facts.append((overlap_score, index, fact))

        if any(score > 0 for score, _, _ in scored_facts):
            scored_facts.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [fact for _, _, fact in scored_facts[:limit]]

        return list(reversed(facts))[:limit]

    async def _with_retry(self, operation, /, *args, **kwargs):
        """
        Retry AMS SDK calls that fail with transient HTTP transport errors.

        Why this exists:
        In local/demo setups the Agent Memory Server can occasionally drop a
        connection without returning an HTTP response. That is frustrating in a
        live demo because the underlying Redis-backed memory is usually fine;
        the failure is often just a transient transport hiccup.

        We retry a few times for network-level failures only.
        We do NOT retry logical HTTP errors such as validation failures.
        """
        last_error = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectError) as error:
                last_error = error
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(0.2 * attempt)

        raise last_error

    def _build_chat_label(self, user_message: str) -> str:
        """
        Build a short, human-readable label for the archive dropdown.

        We derive the label from the first user message because it is easy to
        explain in the demo and avoids an extra model call just to title chats.
        """
        cleaned = " ".join(user_message.strip().split())
        if not cleaned:
            return "Untitled Chat"

        if len(cleaned) <= 36:
            return cleaned

        return f"{cleaned[:33].rstrip()}..."

    def _build_preview(self, text: str) -> str:
        """
        Truncate long text for archive list previews.

        The archive UI only needs a quick hint of what the chat contains,
        not the full last message.
        """
        cleaned = " ".join(text.strip().split())
        if len(cleaned) <= 72:
            return cleaned

        return f"{cleaned[:69].rstrip()}..."

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
