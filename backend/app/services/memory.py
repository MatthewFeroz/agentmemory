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
from datetime import UTC, datetime, timedelta

import httpx
from agent_memory_client import MemoryAPIClient, MemoryClientConfig
from agent_memory_client.filters import UserId
from agent_memory_client.models import (
    ClientMemoryRecord,
    MemoryMessage,
    MemoryTypeEnum,
    MemoryStrategyConfig,
    WorkingMemory,
)

from backend.app.config import Settings


MONTH_NAME_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)"
)
EVENT_DATE_PATTERN = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{MONTH_NAME_PATTERN}\s+\d{{1,2}},\s+\d{{4}}|"
    r"today|yesterday|tomorrow|last week|next week)"
)


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

        self._max_retries = 3

    async def load_working_memory(
        self,
        session_id: str,
        user_id: str | None = None,
        long_term_memory_strategy: MemoryStrategyConfig | None = None,
    ) -> WorkingMemory:
        """
        Return the full working memory document for one session.

        We keep this helper separate from load_conversation_history() because
        some backend features need more than just the message list:
        - archive listing needs metadata from working_memory.data
        - archived chat reload needs the whole transcript
        - long-term chat persistence needs the stored user_id relationship

        When `long_term_memory_strategy` is provided, it is attached when the
        session is created so AMS can apply that strategy to later transcript
        updates for the same session.
        """
        kwargs = dict(
            session_id=session_id,
            user_id=user_id,
            namespace=self._settings.memory_namespace,
        )
        if long_term_memory_strategy is not None:
            kwargs["long_term_memory_strategy"] = long_term_memory_strategy

        _created, working_memory = await self._with_retry(
            self._client.get_or_create_working_memory,
            **kwargs,
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
        long_term_memory_strategy: MemoryStrategyConfig | None = None,
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

        When `long_term_memory_strategy` is provided, AMS automatically
        extracts long-term facts from the conversation in the background.
        """
        # First fetch the latest stored state so we don't accidentally throw
        # away older messages when we write the update.
        existing_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
            long_term_memory_strategy=long_term_memory_strategy,
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

    async def search_long_term_facts(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[str]:
        """
        Search long-term facts relevant to the current request.

        This method queries AMS native long-term memory rather than reusing
        working memory as a durable fact store.
        """
        results = await self._with_retry(
            self._client.search_long_term_memory,
            text=query,
            user_id=UserId(eq=user_id),
            limit=limit,
            optimize_query=False,
        )
        return [
            memory.text
            for memory in results.memories
            if hasattr(memory, "text")
        ]

    async def build_hydrated_long_term_prompt(
        self,
        session_id: str,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> dict:
        """
        Return an Anthropic-ready prompt hydrated by AMS memory_prompt().

        Request flow:
        1. AMS loads the current session transcript from working memory.
        2. AMS searches native long-term memory for the same user.
        3. AMS returns ready-to-send messages with remembered context injected.

        This keeps long-term retrieval inside AMS itself:
        working memory supplies session context and long-term memory supplies
        durable facts for the same user.
        """
        prompt_result = await self._with_retry(
            self._client.memory_prompt,
            query=query,
            session_id=session_id,
            namespace=self._settings.memory_namespace,
            user_id=user_id,
            long_term_search={
                "limit": limit,
                "user_id": {"eq": user_id},
            },
            optimize_query=False,
        )

        system_sections: list[str] = []
        anthropic_messages: list[dict] = []

        for message in prompt_result.get("messages", []):
            role = message.get("role")
            text = self._coerce_message_content_text(message.get("content"))
            if not text:
                continue
            if role == "system":
                system_sections.append(text)
                continue
            anthropic_messages.append(
                {
                    "role": role,
                    "content": text,
                }
            )

        system_prompt = self._settings.system_prompt
        if system_sections:
            system_prompt = f"{system_prompt}\n\n" + "\n\n".join(system_sections)

        return {
            "system_prompt": system_prompt,
            "messages": anthropic_messages,
            "long_term_memories": [
                self._memory_record_to_fact_dict(memory)
                for memory in prompt_result.get("long_term_memories", [])
                if isinstance(memory, dict) or hasattr(memory, "text")
            ],
        }

    async def list_long_term_facts(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Return the user's currently remembered long-term facts.

        This powers the frontend sidebar's "Remembered Facts" list.

        AMS search is query-based, so this method uses a broad search phrase to
        surface a representative set of durable memories for the user.
        """
        results = await self._with_retry(
            self._client.search_long_term_memory,
            text="user identity preferences events conferences launches audience",
            user_id=UserId(eq=user_id),
            limit=limit,
            optimize_query=False,
        )
        return [
            self._memory_record_to_fact_dict(memory)
            for memory in results.memories
            if hasattr(memory, "text")
        ]

    async def store_long_term_facts(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> None:
        """
        Extract and persist explicit long-term memories from one user turn.

        Request flow:
        1. `/chat` stores the transcript turn in session working memory.
        2. This method inspects the latest user message for "rememberable" data.
        3. New memories are written to AMS native long-term memory.
        4. Future chats retrieve them through native long-term search.

        State flow:
        - `session_id` tells us which chat produced the memory
        - `user_id` ties the memory to the same person across many chats
        - `memory_type` tells us whether the record is semantic or episodic
        - `event_date` is populated only for time-grounded episodic memories
        """
        extracted_memories = self._extract_long_term_memories(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
        )
        if not extracted_memories:
            return

        await self._with_retry(
            self._client.create_long_term_memory,
            extracted_memories,
            deduplicate=True,
        )
        await self._wait_for_long_term_indexing(
            user_id=user_id,
            expected_texts=[memory.text for memory in extracted_memories],
        )

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
                    f"The team shipped {self._strip_trailing_event_date(value)}.",
                    ["product", "shipping"],
                    [self._strip_trailing_event_date(value)],
                ),
            ),
            (
                re.compile(r"\bwe launched (?P<value>[^.!?\n]+)", re.IGNORECASE),
                lambda value: (
                    f"The team launched {self._strip_trailing_event_date(value)}.",
                    ["product", "launch"],
                    [self._strip_trailing_event_date(value)],
                ),
            ),
            (
                re.compile(
                    r"\bour next conference is (?P<value>[^.!?\n]+)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The next conference is {self._strip_trailing_event_date(value)}.",
                    ["events", "conference"],
                    [self._strip_trailing_event_date(value)],
                ),
            ),
        ]

        memories: list[ClientMemoryRecord] = []
        seen_signatures: set[tuple[str, str | None, str | None]] = set()

        for pattern, builder in fact_specs:
            for match in pattern.finditer(normalized_message):
                text, topics, entities = builder(match.group("value"))
                memory = ClientMemoryRecord(
                    text=text,
                    session_id=session_id,
                    user_id=user_id,
                    topics=topics,
                    entities=entities,
                    memory_type=MemoryTypeEnum.SEMANTIC,
                )
                signature = self._memory_signature(memory)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                memories.append(memory)

        for memory in self._extract_episodic_long_term_memories(
            session_id=session_id,
            user_id=user_id,
            user_message=normalized_message,
        ):
            signature = self._memory_signature(memory)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            memories.append(memory)

        return memories

    def _extract_episodic_long_term_memories(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> list[ClientMemoryRecord]:
        """
        Extract time-grounded event memories from explicit dated statements.

        We keep this deliberately rule-based so the demo remains explainable:
        a memory becomes episodic only when the sentence contains both:
        - an event-style verb ("visited", "launched", "attended", ...)
        - a date phrase we can ground to a concrete calendar date
        """
        event_specs = [
            (
                re.compile(
                    rf"\bi visited (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user visited {value.strip()} on {grounded_date}.",
                    ["events", "visit"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bi went to (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user went to {value.strip()} on {grounded_date}.",
                    ["events", "visit"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bi attended (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user attended {value.strip()} on {grounded_date}.",
                    ["events", "attendance"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bwe launched (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The team launched {value.strip()} on {grounded_date}.",
                    ["product", "launch", "events"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bwe shipped (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The team shipped {value.strip()} on {grounded_date}.",
                    ["product", "shipping", "events"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bwe (?:presented|spoke) at (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The team presented at {value.strip()} on {grounded_date}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bour next conference is (?P<value>.+?)\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The next conference is {value.strip()} on {grounded_date}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
            ),
        ]

        episodic_memories: list[ClientMemoryRecord] = []
        reference_now = datetime.now(UTC)

        for pattern, builder in event_specs:
            for match in pattern.finditer(user_message):
                grounded = self._ground_event_date(match.group("date"), reference_now)
                if grounded is None:
                    continue
                event_date, grounded_label = grounded
                text, topics, entities = builder(
                    match.group("value"),
                    grounded_label,
                )
                episodic_memories.append(
                    ClientMemoryRecord(
                        text=text,
                        session_id=session_id,
                        user_id=user_id,
                        topics=topics,
                        entities=entities,
                        memory_type=MemoryTypeEnum.EPISODIC,
                        event_date=event_date,
                    )
                )

        return episodic_memories

    def _memory_record_to_fact_dict(self, memory) -> dict:
        """
        Normalize AMS memory records into the API's remembered-fact shape.

        This keeps the frontend independent from the exact AMS SDK model class.
        """
        if isinstance(memory, dict):
            memory_type = memory.get("memory_type")
            event_date = memory.get("event_date")
            created_at = memory.get("created_at")
            return {
                "text": memory.get("text"),
                "topics": list(memory.get("topics") or []),
                "entities": list(memory.get("entities") or []),
                "source_session_id": memory.get("session_id"),
                "memory_type": memory_type,
                "event_date": event_date,
                "created_at": created_at,
            }

        memory_type = getattr(memory, "memory_type", None)
        if isinstance(memory_type, MemoryTypeEnum):
            memory_type = memory_type.value

        event_date = getattr(memory, "event_date", None)
        created_at = getattr(memory, "created_at", None)

        return {
            "text": memory.text,
            "topics": list(getattr(memory, "topics", []) or []),
            "entities": list(getattr(memory, "entities", []) or []),
            "source_session_id": getattr(memory, "session_id", None),
            "memory_type": memory_type,
            "event_date": event_date.isoformat() if event_date else None,
            "created_at": created_at.isoformat() if created_at else None,
        }

    def _memory_signature(self, memory) -> tuple[str, str | None, str | None]:
        """
        Build a stable deduplication key for semantic and episodic memories.

        We include `memory_type` and `event_date` so the same text can exist as
        different kinds of memory when that is intentional.
        """
        memory_type = getattr(memory, "memory_type", None)
        if isinstance(memory_type, MemoryTypeEnum):
            memory_type = memory_type.value

        event_date = getattr(memory, "event_date", None)
        event_date_iso = event_date.isoformat() if event_date else None

        return (memory.text, memory_type, event_date_iso)

    def _coerce_message_content_text(self, content) -> str:
        """
        Normalize AMS memory_prompt content into plain text.

        memory_prompt() returns structured content blocks. Anthropic expects
        a plain string for each text message in this demo.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return content.get("text", "")
        if isinstance(content, list):
            return "\n".join(
                self._coerce_message_content_text(item)
                for item in content
                if self._coerce_message_content_text(item)
            )
        return ""

    async def _wait_for_long_term_indexing(
        self,
        user_id: str,
        expected_texts: list[str],
        max_wait_seconds: float = 6.0,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        """
        Poll AMS until newly created long-term memories become searchable.

        Long-term indexing is asynchronous. Waiting briefly here makes the
        remembered-facts panel and the next chat turn behave more consistently.
        """
        pending_texts = {text for text in expected_texts if text}
        attempts = max(1, int(max_wait_seconds / poll_interval_seconds))

        for _ in range(attempts):
            resolved_texts: set[str] = set()
            for text in pending_texts:
                results = await self._with_retry(
                    self._client.search_long_term_memory,
                    text=text,
                    user_id=UserId(eq=user_id),
                    limit=5,
                    optimize_query=False,
                )
                if any(getattr(memory, "text", None) == text for memory in results.memories):
                    resolved_texts.add(text)

            pending_texts -= resolved_texts
            if not pending_texts:
                return

            await asyncio.sleep(poll_interval_seconds)

    def _strip_trailing_event_date(self, value: str) -> str:
        """
        Remove a trailing date phrase from event text before semantic storage.

        Example:
        "Redis 8 on April 10, 2026" -> "Redis 8"

        This lets us keep a timeless semantic fact alongside a separate,
        time-grounded episodic record for the same event.
        """
        cleaned = re.sub(
            rf"\s+(?:on|during)\s+{EVENT_DATE_PATTERN}\s*$",
            "",
            value.strip(),
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" ,")

    def _ground_event_date(
        self,
        raw_date: str,
        reference_now: datetime,
    ) -> tuple[datetime, str] | None:
        """
        Resolve a supported date phrase to a concrete UTC event timestamp.

        Assumptions:
        - We store event dates at midnight UTC because many user statements
          identify a day, not a precise time.
        - Relative phrases are grounded against the server's current UTC date.
        """
        normalized = raw_date.strip().lower()

        if normalized == "today":
            grounded_day = reference_now.date()
        elif normalized == "yesterday":
            grounded_day = (reference_now - timedelta(days=1)).date()
        elif normalized == "tomorrow":
            grounded_day = (reference_now + timedelta(days=1)).date()
        elif normalized == "last week":
            grounded_day = (reference_now - timedelta(days=7)).date()
        elif normalized == "next week":
            grounded_day = (reference_now + timedelta(days=7)).date()
        else:
            for date_format in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(raw_date.strip(), date_format)
                    grounded_day = parsed.date()
                    break
                except ValueError:
                    continue
            else:
                return None

        event_date = datetime(
            grounded_day.year,
            grounded_day.month,
            grounded_day.day,
            tzinfo=UTC,
        )
        grounded_label = (
            f"{event_date.strftime('%B')} {event_date.day}, {event_date.year}"
        )
        return event_date, grounded_label

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
