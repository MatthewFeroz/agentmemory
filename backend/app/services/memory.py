"""Redis-backed short-term and long-term memory service via Agent Memory Server.

Centralizes all memory system logic so that the rest of the application
does not need to interact with the AMS SDK directly.

This service covers both memory layers:

- **Short-term memory**: Session transcript stored in working memory.
- **Long-term memory**: Durable facts tied to a stable ``user_id``,
  persisted across sessions.
- **Archive support**: Listing and reloading past long-term chat
  sessions.
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
    ForgetPolicy,
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
"""Regex fragment matching abbreviated and full English month names."""

EVENT_DATE_PATTERN = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{MONTH_NAME_PATTERN}\s+\d{{1,2}},\s+\d{{4}}|"
    r"today|yesterday|tomorrow|last week|next week)"
)
"""Regex fragment matching supported date phrase formats."""

LONG_TERM_FACT_FALLBACK_QUERIES = (
    "user name",
    "user preferences",
    "audience preferences",
    "conference",
    "product launch",
    "shipped feature",
    "team roadmap",
    "important facts about the user",
)
"""Seed queries used when a broad long-term search returns no results."""

REGEX_ORIGIN_TOPIC = "demo-regex"
"""Topic attached to every record produced by the regex extractor.

Used by the Facts panel to distinguish deterministic-regex origin from
AMS discrete-strategy origin (which does not carry this topic). Absence
of this tag on a record is a demo-time proxy for "extracted by AMS".
"""


class MemoryService:
    """Wrapper around the Agent Memory Server Python SDK.

    Hides SDK details behind application-specific operations:

    1. Loading and storing a session's conversation transcript.
    2. Listing archived chats for a long-term user identity.
    3. Storing and searching explicit long-term facts.

    Attributes:
        _settings: Application configuration.
        _config: AMS client configuration.
        _client: The AMS SDK client instance.
        _max_retries: Number of retry attempts for transient HTTP
            errors against AMS.
    """

    def __init__(self, settings: Settings) -> None:
        """Initializes the memory service and AMS client.

        The client communicates with Agent Memory Server over HTTP.
        AMS then handles persistence into Redis::

            FastAPI app → MemoryAPIClient → Agent Memory Server → Redis

        Args:
            settings: Application configuration containing the AMS
                base URL and optional namespace.
        """
        self._settings = settings
        self._config = MemoryClientConfig(
            base_url=settings.memory_api_url,
        )
        self._client = MemoryAPIClient(self._config)
        self._max_retries = 3

    def build_default_long_term_memory_strategy(
        self,
    ) -> MemoryStrategyConfig:
        """Returns the default AMS extraction strategy for long-term mode.

        The ``discrete`` strategy lets ``put_working_memory()`` trigger
        background extraction of user facts without requiring
        application-side parsing.

        Returns:
            A ``MemoryStrategyConfig`` set to the ``discrete`` strategy.
        """
        return MemoryStrategyConfig(strategy="discrete")

    async def load_working_memory(
        self,
        session_id: str,
        user_id: str | None = None,
        long_term_memory_strategy: MemoryStrategyConfig | None = None,
    ) -> WorkingMemory:
        """Returns the full working memory document for one session.

        Separate from ``load_conversation_history()`` because some
        operations need more than just the message list — for example,
        archive listing needs metadata from ``working_memory.data``.

        When ``long_term_memory_strategy`` is provided it is attached
        at session creation time so AMS can apply that strategy to
        subsequent transcript updates.

        Args:
            session_id: The conversation session to load.
            user_id: Optional stable user identity to associate.
            long_term_memory_strategy: Optional extraction strategy
                to attach when the session is first created.

        Returns:
            The ``WorkingMemory`` document for the session.
        """
        kwargs = dict(
            session_id=session_id,
            user_id=user_id,

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
        """Returns prior chat messages in Anthropic's expected format.

        Transforms AMS ``MemoryMessage`` objects into plain dicts::

            [
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."},
            ]

        Args:
            session_id: The conversation session to load history for.
            user_id: Optional stable user identity to associate.

        Returns:
            A list of message dicts with ``role`` and ``content``
            keys, ordered chronologically.
        """
        working_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
        )

        return [
            {"role": message.role, "content": message.content}
            for message in working_memory.messages
        ]

    async def store_conversation_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        user_id: str | None = None,
        long_term_memory_strategy: MemoryStrategyConfig | None = None,
    ) -> None:
        """Appends the latest user/assistant exchange to working memory.

        AMS ``put_working_memory()`` replaces the full message list,
        so this method reads the current state, appends the new turn,
        and writes the complete updated list back.

        Args:
            session_id: The conversation session to update.
            user_message: The user's message text.
            assistant_message: Claude's response text.
            user_id: Optional stable user identity.
            long_term_memory_strategy: Optional extraction strategy.
                When provided, AMS automatically extracts long-term
                facts from the conversation in the background.
        """
        existing_memory = await self.load_working_memory(
            session_id=session_id,
            user_id=user_id,
            long_term_memory_strategy=long_term_memory_strategy,
        )

        now = datetime.now(UTC)
        updated_messages = list(existing_memory.messages)

        updated_data = self._build_chat_data(
            existing_data=existing_memory.data,
            user_message=user_message,
            assistant_message=assistant_message,
            updated_messages=updated_messages,
            now=now,
        )

        updated_messages.append(
            MemoryMessage(
                role="user",
                content=user_message,
                created_at=now,
            )
        )
        updated_messages.append(
            MemoryMessage(
                role="assistant",
                content=assistant_message,
                created_at=now,
            )
        )

        updated_working_memory = WorkingMemory(
            session_id=session_id,
            namespace=existing_memory.namespace,
            user_id=user_id or existing_memory.user_id,
            context=existing_memory.context,
            data=updated_data,
            memories=existing_memory.memories,
            messages=updated_messages,
            long_term_memory_strategy=(
                existing_memory.long_term_memory_strategy
            ),
            ttl_seconds=existing_memory.ttl_seconds,
            last_accessed=now,
        )

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
        """Returns archived chat summaries for one long-term user.

        AMS stores sessions keyed by ``session_id`` and supports
        filtering by ``user_id``, so no separate archive index is
        needed.

        Args:
            user_id: The stable user identity to list chats for.
            limit: Maximum number of sessions to return.

        Returns:
            A list of chat summary dicts sorted newest-first, each
            containing ``session_id``, ``label``, ``message_count``,
            ``last_updated``, and ``preview``.
        """
        session_list = await self._with_retry(
            self._client.list_sessions,
            limit=limit,

            user_id=user_id,
        )

        chats: list[dict] = []
        for session_id in session_list.sessions:
            working_memory = await self.load_working_memory(
                session_id=session_id,
                user_id=user_id,
            )
            chats.append(self._build_chat_summary(working_memory))

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
        """Loads one archived long-term chat transcript with metadata.

        Args:
            session_id: The archived session to load.
            user_id: The stable user identity that owns the chat.

        Returns:
            A dict containing ``session_id``, ``label``, and
            ``messages`` (a list of normalized message dicts).
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

    async def build_hydrated_long_term_prompt(
        self,
        session_id: str,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> dict:
        """Returns an Anthropic-ready prompt hydrated with AMS memory.

        The request flow:

        1. AMS loads the current session transcript from working
           memory.
        2. AMS searches native long-term memory for the same user.
        3. AMS returns ready-to-send messages with remembered context
           injected.

        Args:
            session_id: The current conversation session.
            user_id: The stable user identity for long-term retrieval.
            query: The current user message, used as the long-term
                search query.
            limit: Maximum number of long-term memories to retrieve.

        Returns:
            A dict containing:
                - ``system_prompt``: The enriched system prompt.
                - ``messages``: Anthropic-formatted message list.
                - ``long_term_memories``: Retrieved long-term fact
                  dicts.
        """
        prompt_result = await self._with_retry(
            self._client.memory_prompt,
            query=query,
            session_id=session_id,

            user_id=user_id,
            long_term_search={
                "limit": limit,
                "user_id": {"eq": user_id},
            },
            optimize_query=False,
        )

        system_sections: list[str] = []
        anthropic_messages: list[dict] = []

        for message in prompt_result.get("messages") or []:
            role = message.get("role")
            text = self._coerce_message_content_text(
                message.get("content")
            )
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
            system_prompt = (
                f"{system_prompt}\n\n" + "\n\n".join(system_sections)
            )

        return {
            "system_prompt": system_prompt,
            "messages": anthropic_messages,
            "long_term_memories": [
                self._memory_record_to_fact_dict(memory)
                for memory in (
                    prompt_result.get("long_term_memories") or []
                )
                if isinstance(memory, dict) or hasattr(memory, "text")
            ],
        }

    async def list_long_term_facts(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Returns the user's currently remembered long-term facts.

        Uses a broad search phrase to surface a representative set of
        durable memories. Falls back to multiple targeted seed queries
        when the broad search returns no results.

        Args:
            user_id: The stable user identity whose facts to load.
            limit: Maximum number of facts to return.

        Returns:
            A list of normalized fact dicts.
        """
        try:
            results = await self._search_long_term_memory_records(
                text=(
                    "user identity preferences events "
                    "conferences launches audience"
                ),
                user_id=user_id,
                limit=limit,
            )
        except Exception as error:
            print(
                "[WARN] Broad long-term fact search failed; "
                f"falling back to targeted seed queries: {error}"
            )
            return await self._scan_long_term_facts_by_seed_queries(
                user_id=user_id,
                limit=limit,
            )

        facts = [
            self._memory_record_to_fact_dict(memory)
            for memory in results
            if hasattr(memory, "text")
        ]
        if facts:
            return facts

        return await self._scan_long_term_facts_by_seed_queries(
            user_id=user_id,
            limit=limit,
        )

    async def store_long_term_facts(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> None:
        """Extracts and persists long-term memories from a user turn.

        The pipeline:

        1. The ``/chat`` endpoint stores the transcript turn in
           session working memory.
        2. This method inspects the user message for recognizable
           fact patterns.
        3. Extracted memories are written to AMS native long-term
           memory.
        4. Future chats retrieve them through long-term search.

        Args:
            session_id: The chat session that produced the message.
            user_id: The stable user identity to associate with
                extracted facts.
            user_message: The raw user message to extract facts from.
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
            expected_texts=[
                memory.text for memory in extracted_memories
            ],
        )

    async def delete_long_term_memories(
        self,
        memory_ids: list[str],
    ) -> int:
        """Deletes one or more long-term memories by their server IDs.

        Args:
            memory_ids: Server-assigned identifiers of the memories
                to remove.

        Returns:
            The number of memories that were actually deleted.

        Raises:
            Exception: Re-raised from the underlying SDK when the
                delete operation fails.
        """
        result = await self._with_retry(
            self._client.delete_long_term_memories,
            memory_ids,
        )
        return len(memory_ids) if result else 0

    async def update_long_term_memory(
        self,
        memory_id: str,
        updates: dict,
    ) -> dict:
        """Patches a single long-term memory with the provided fields.

        Only the keys present in ``updates`` are sent to AMS; omitted
        fields remain unchanged on the server.

        Args:
            memory_id: Server-assigned identifier of the memory to
                update.
            updates: A dict of field names to new values. Accepted
                keys mirror the ``EditMemoryRecordRequest`` schema:
                ``text``, ``topics``, ``entities``, ``memory_type``,
                ``event_date``.

        Returns:
            A normalized fact dict reflecting the updated record.

        Raises:
            Exception: Re-raised from the underlying SDK when the
                update operation fails.
        """
        updated_record = await self._with_retry(
            self._client.edit_long_term_memory,
            memory_id,
            updates,
        )
        return self._memory_record_to_fact_dict(updated_record)

    async def forget_long_term_memories(
        self,
        *,
        user_id: str,
        max_age_days: int | None = None,
        max_inactive_days: int | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Runs a policy-driven forgetting pass over long-term memories.

        AMS evaluates every memory for the given user against the
        supplied thresholds and removes those that exceed them.

        Args:
            user_id: The stable user identity whose memories should
                be evaluated.
            max_age_days: Remove memories created more than this many
                days ago.
            max_inactive_days: Remove memories not accessed within
                this many days.
            dry_run: When ``True``, return what would be deleted
                without actually removing anything.

        Returns:
            A dict with ``scanned``, ``deleted``, ``deleted_ids``,
            and ``dry_run`` keys.

        Raises:
            Exception: Re-raised from the underlying SDK when the
                forget operation fails.
        """
        policy = ForgetPolicy(
            max_age_days=max_age_days,
            max_inactive_days=max_inactive_days,
        )
        result = await self._with_retry(
            self._client.forget_long_term_memories,
            policy,
            user_id=user_id,
            dry_run=dry_run,
        )
        return {
            "scanned": result.scanned,
            "deleted": result.deleted,
            "deleted_ids": list(result.deleted_ids),
            "dry_run": result.dry_run,
        }

    def _build_chat_data(
        self,
        existing_data: dict | None,
        user_message: str,
        assistant_message: str,
        updated_messages: list[MemoryMessage],
        now: datetime,
    ) -> dict:
        """Builds the metadata blob stored alongside a session transcript.

        The working memory ``data`` dictionary stores archive-friendly
        metadata: chat label, message count, preview snippet, and
        timestamps.

        Args:
            existing_data: Previously stored metadata, or ``None``.
            user_message: The latest user message.
            assistant_message: The latest assistant reply.
            updated_messages: The message list before appending the
                current turn.
            now: Current UTC timestamp.

        Returns:
            An updated metadata dict.
        """
        data = dict(existing_data or {})

        if not data.get("chat_label"):
            data["chat_label"] = self._build_chat_label(user_message)
            data["started_at"] = now.isoformat()

        data["message_count"] = len(updated_messages) + 2
        data["last_updated"] = now.isoformat()
        data["preview"] = self._build_preview(
            assistant_message or user_message
        )

        return data

    def _build_chat_summary(
        self, working_memory: WorkingMemory
    ) -> dict:
        """Converts a working memory object into list-view metadata.

        Produces a lightweight summary suitable for archive list
        endpoints without exposing the full transcript.

        Args:
            working_memory: The full working memory document.

        Returns:
            A dict with ``session_id``, ``label``, ``message_count``,
            ``last_updated``, and ``preview``.
        """
        data = dict(working_memory.data or {})
        messages = working_memory.messages
        last_updated = data.get("last_updated")

        if last_updated is None and getattr(
            working_memory, "last_accessed", None
        ):
            last_updated = working_memory.last_accessed.isoformat()

        preview = data.get("preview")
        if preview is None and messages:
            preview = self._build_preview(messages[-1].content)

        return {
            "session_id": working_memory.session_id,
            "label": (
                data.get("chat_label")
                or f"Chat {working_memory.session_id}"
            ),
            "message_count": (
                data.get("message_count") or len(messages)
            ),
            "last_updated": last_updated,
            "preview": preview,
        }

    def _working_memory_to_messages(
        self, working_memory: WorkingMemory
    ) -> list[dict]:
        """Normalizes stored messages into API response dicts.

        Args:
            working_memory: The working memory document containing
                the message list.

        Returns:
            A list of dicts with ``role``, ``content``, and optional
            ``timestamp`` keys.
        """
        return [
            {
                "role": message.role,
                "content": message.content,
                "timestamp": (
                    message.created_at.isoformat()
                    if getattr(message, "created_at", None)
                    else None
                ),
            }
            for message in working_memory.messages
        ]

    def _extract_long_term_memories(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> list[ClientMemoryRecord]:
        """Extracts long-term memories from explicit user statements.

        Uses a small set of clear regex patterns to bridge user
        statements to persisted memories.  Three layers are applied in
        order:

        1. **Semantic-only** patterns (name, preferences) — always
           produce a single semantic memory.
        2. **Dual-mode** patterns (shipped, launched, conference) —
           always produce a semantic memory with the date stripped, and
           additionally produce an episodic memory when a date phrase
           is present and can be grounded.
        3. **Episodic-only** patterns (visited, attended, etc.) —
           produce an episodic memory only when a date is present.

        A catch-all ``"remember ..."`` pattern runs only when no
        specific or dual-mode patterns matched.

        Args:
            session_id: The originating chat session.
            user_id: The stable user identity.
            user_message: The raw user message to scan.

        Returns:
            A list of ``ClientMemoryRecord`` objects ready for
            submission to AMS. May be empty if no patterns matched.
        """
        normalized_message = user_message.strip()
        if not normalized_message:
            return []

        reference_now = datetime.now(UTC)
        memories: list[ClientMemoryRecord] = []
        seen: set[tuple[str, str | None, str | None]] = set()

        # --- Semantic-only patterns ------------------------------------
        # Each value capture stops at sentence-ending punctuation and
        # conjunctions so compound sentences produce separate clean
        # facts.
        semantic_only_specs = [
            (
                re.compile(
                    r"\bmy name is (?P<value>[^.!?,\n]+?)"
                    r"(?:\s+(?:and|but)\b|[.!?,\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The user's name is {value.strip()}.",
                    ["identity", "name"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    r"\bi prefer (?P<value>[^.!?,\n]+?)"
                    r"(?:\s+(?:and|but)\b|[.!?,\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The user prefers {value.strip()}.",
                    ["preferences"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    r"\bour audience prefers (?P<value>[^.!?,\n]+?)"
                    r"(?:\s+(?:and|but)\b|[.!?,\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The Redis DevRel audience prefers {value.strip()}.",
                    ["audience", "preferences"],
                    [value.strip(), "Redis DevRel audience"],
                ),
            ),
        ]

        for pattern, builder in semantic_only_specs:
            for match in pattern.finditer(normalized_message):
                text, topics, entities = builder(
                    match.group("value")
                )
                self._add_unique_memory(
                    ClientMemoryRecord(
                        text=text,
                        session_id=session_id,
                        user_id=user_id,
                        topics=topics,
                        entities=entities,
                        memory_type=MemoryTypeEnum.SEMANTIC,
                    ),
                    seen,
                    memories,
                )

        # --- Dual-mode patterns ----------------------------------------
        # Each regex captures an optional trailing date phrase.  A
        # semantic memory (date stripped) is always produced.  When a
        # date is present and can be grounded an episodic memory is
        # produced as well.
        dual_specs = [
            (
                re.compile(
                    rf"\bwe shipped (?P<value>.+?)"
                    rf"(?:\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN}))"
                    r"?(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The team shipped {value.strip()}.",
                    ["product", "shipping"],
                    [value.strip()],
                ),
                lambda value, grounded_date: (
                    f"The team shipped {value.strip()} on "
                    f"{grounded_date}.",
                    ["product", "shipping", "events"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bwe launched (?P<value>.+?)"
                    rf"(?:\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN}))"
                    r"?(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The team launched {value.strip()}.",
                    ["product", "launch"],
                    [value.strip()],
                ),
                lambda value, grounded_date: (
                    f"The team launched {value.strip()} on "
                    f"{grounded_date}.",
                    ["product", "launch", "events"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bour next conference is (?P<value>.+?)"
                    rf"(?:\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN}))"
                    r"?(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value: (
                    f"The next conference is {value.strip()}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
                lambda value, grounded_date: (
                    f"The next conference is {value.strip()} on "
                    f"{grounded_date}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
            ),
        ]

        for pattern, sem_builder, epi_builder in dual_specs:
            for match in pattern.finditer(normalized_message):
                value = match.group("value")
                text, topics, entities = sem_builder(value)
                self._add_unique_memory(
                    ClientMemoryRecord(
                        text=text,
                        session_id=session_id,
                        user_id=user_id,
                        topics=topics,
                        entities=entities,
                        memory_type=MemoryTypeEnum.SEMANTIC,
                    ),
                    seen,
                    memories,
                )
                raw_date = match.group("date")
                if raw_date:
                    grounded = self._ground_event_date(
                        raw_date, reference_now
                    )
                    if grounded is not None:
                        event_date, label = grounded
                        text, topics, entities = epi_builder(
                            value, label
                        )
                        self._add_unique_memory(
                            ClientMemoryRecord(
                                text=text,
                                session_id=session_id,
                                user_id=user_id,
                                topics=topics,
                                entities=entities,
                                memory_type=MemoryTypeEnum.EPISODIC,
                                event_date=event_date,
                            ),
                            seen,
                            memories,
                        )

        # --- Catch-all -------------------------------------------------
        # "remember ..." in any common form.  Only used when no
        # specific or dual-mode patterns matched to avoid duplication.
        if not memories:
            catchall_pattern = re.compile(
                r"\bremember(?:\s+(?:that|this))?\s*[,:;]?\s*"
                r"(?P<value>[^.!?\n]+)",
                re.IGNORECASE,
            )
            for match in catchall_pattern.finditer(normalized_message):
                raw = match.group("value").strip()
                text = f"{raw[0].upper()}{raw[1:]}."
                self._add_unique_memory(
                    ClientMemoryRecord(
                        text=text,
                        session_id=session_id,
                        user_id=user_id,
                        topics=["user-stated"],
                        entities=[],
                        memory_type=MemoryTypeEnum.SEMANTIC,
                    ),
                    seen,
                    memories,
                )

        # --- Episodic-only patterns ------------------------------------
        for memory in self._extract_episodic_long_term_memories(
            session_id=session_id,
            user_id=user_id,
            user_message=normalized_message,
            reference_now=reference_now,
        ):
            self._add_unique_memory(memory, seen, memories)

        return memories

    def _extract_episodic_long_term_memories(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        reference_now: datetime,
    ) -> list[ClientMemoryRecord]:
        """Extracts time-grounded event memories from dated statements.

        Only patterns that are **exclusively** episodic live here.
        Patterns that also produce a semantic memory (shipped, launched,
        conference) are handled as dual-mode specs in
        ``_extract_long_term_memories()``.

        A memory becomes episodic only when the sentence contains both
        an event-style verb (``visited``, ``attended``, etc.) and a
        date phrase that can be grounded to a concrete calendar date.

        Args:
            session_id: The originating chat session.
            user_id: The stable user identity.
            user_message: The normalized user message to scan.
            reference_now: The current UTC datetime for resolving
                relative date phrases.

        Returns:
            A list of episodic ``ClientMemoryRecord`` objects with
            populated ``event_date`` fields.
        """
        event_specs = [
            (
                re.compile(
                    rf"\bi visited (?P<value>.+?)\s+(?:on\s+)?"
                    rf"(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user visited {value.strip()} on "
                    f"{grounded_date}.",
                    ["events", "visit"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bi went to (?P<value>.+?)\s+(?:on\s+)?"
                    rf"(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user went to {value.strip()} on "
                    f"{grounded_date}.",
                    ["events", "visit"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bi attended (?P<value>.+?)\s+(?:on\s+)?"
                    rf"(?P<date>{EVENT_DATE_PATTERN})(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The user attended {value.strip()} on "
                    f"{grounded_date}.",
                    ["events", "attendance"],
                    [value.strip()],
                ),
            ),
            (
                re.compile(
                    rf"\bwe (?:presented|spoke) at (?P<value>.+?)"
                    rf"\s+(?:on\s+)?(?P<date>{EVENT_DATE_PATTERN})"
                    r"(?:[.!?\n]|$)",
                    re.IGNORECASE,
                ),
                lambda value, grounded_date: (
                    f"The team presented at {value.strip()} on "
                    f"{grounded_date}.",
                    ["events", "conference"],
                    [value.strip()],
                ),
            ),
        ]

        episodic_memories: list[ClientMemoryRecord] = []

        for pattern, builder in event_specs:
            for match in pattern.finditer(user_message):
                grounded = self._ground_event_date(
                    match.group("date"), reference_now
                )
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
        """Normalizes an AMS memory record into the API fact shape.

        Handles both raw dicts and SDK model objects so callers do
        not need to check the type.

        Args:
            memory: An AMS memory record, either a dict or an SDK
                model object with ``text``, ``topics``, ``entities``,
                etc. attributes.

        Returns:
            A normalized dict with ``text``, ``topics``, ``entities``,
            ``source_session_id``, ``memory_type``, ``event_date``,
            and ``created_at`` keys.
        """
        if isinstance(memory, dict):
            return {
                "id": memory.get("id"),
                "text": memory.get("text"),
                "topics": list(memory.get("topics") or []),
                "entities": list(memory.get("entities") or []),
                "source_session_id": memory.get("session_id"),
                "memory_type": memory.get("memory_type"),
                "event_date": memory.get("event_date"),
                "created_at": memory.get("created_at"),
            }

        memory_type = getattr(memory, "memory_type", None)
        if isinstance(memory_type, MemoryTypeEnum):
            memory_type = memory_type.value

        event_date = getattr(memory, "event_date", None)
        created_at = getattr(memory, "created_at", None)

        return {
            "id": getattr(memory, "id", None),
            "text": memory.text,
            "topics": list(getattr(memory, "topics", []) or []),
            "entities": list(getattr(memory, "entities", []) or []),
            "source_session_id": getattr(memory, "session_id", None),
            "memory_type": memory_type,
            "event_date": (
                event_date.isoformat() if event_date else None
            ),
            "created_at": (
                created_at.isoformat() if created_at else None
            ),
        }

    def _memory_signature(
        self, memory
    ) -> tuple[str, str | None, str | None]:
        """Builds a stable deduplication key for a memory record.

        Includes ``memory_type`` and ``event_date`` so the same text
        can exist as different memory kinds when intentional.

        Args:
            memory: A memory record (SDK model or dict-like).

        Returns:
            A ``(text, memory_type, event_date_iso)`` tuple.
        """
        memory_type = getattr(memory, "memory_type", None)
        if isinstance(memory_type, MemoryTypeEnum):
            memory_type = memory_type.value

        event_date = getattr(memory, "event_date", None)
        event_date_iso = event_date.isoformat() if event_date else None

        return (memory.text, memory_type, event_date_iso)

    def _add_unique_memory(
        self,
        memory: ClientMemoryRecord,
        seen: set[tuple[str, str | None, str | None]],
        target: list[ClientMemoryRecord],
    ) -> None:
        """Appends a memory to *target* unless it duplicates one already seen.

        Uses ``_memory_signature()`` for deduplication so the same text
        can coexist as different memory types when intentional. Every
        record accepted here is tagged with ``REGEX_ORIGIN_TOPIC`` so the
        Facts panel can distinguish regex-origin records from AMS
        discrete-strategy records at display time.

        Args:
            memory: The candidate memory record.
            seen: Mutable set of signatures already collected.
            target: Mutable list to append to on success.
        """
        signature = self._memory_signature(memory)
        if signature in seen:
            return

        existing_topics = list(memory.topics or [])
        if REGEX_ORIGIN_TOPIC not in existing_topics:
            existing_topics.append(REGEX_ORIGIN_TOPIC)
            memory.topics = existing_topics

        seen.add(signature)
        target.append(memory)

    def _coerce_message_content_text(self, content) -> str:
        """Normalizes AMS ``memory_prompt`` content into plain text.

        ``memory_prompt()`` may return structured content blocks.
        This method flattens them into a single string.

        Args:
            content: A string, dict with a ``text`` key, or a list
                of such elements.

        Returns:
            The extracted plain text, or an empty string.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return content.get("text", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = self._coerce_message_content_text(item)
                if text:
                    parts.append(text)
            return "\n".join(parts)
        return ""

    async def _search_long_term_memory_records(
        self,
        *,
        text: str,
        user_id: str,
        limit: int,
        search_mode: str | None = None,
        allow_keyword_fallback: bool = True,
    ) -> list:
        """Searches AMS long-term memories with optional fallback.

        Semantic search is preferred when the backend is configured
        to rely on AMS vector retrieval. Falls back to keyword search
        on failure when allowed.

        Args:
            text: The search query text.
            user_id: The stable user identity to filter by.
            limit: Maximum number of results.
            search_mode: Explicit search mode override. When ``None``,
                the mode is determined by configuration.
            allow_keyword_fallback: Whether to retry with keyword
                search if semantic search fails.

        Returns:
            A list of AMS memory record objects.

        Raises:
            Exception: Re-raised from the underlying SDK when all
                search attempts fail.
        """
        if search_mode is not None:
            search_modes = [search_mode]
        elif self._settings.prefer_ams_long_term_search:
            search_modes = ["semantic"]
            if allow_keyword_fallback:
                search_modes.append("keyword")
        else:
            search_modes = ["keyword"]

        last_error: Exception | None = None
        for mode in search_modes:
            try:
                search_kwargs = dict(
                    text=text,
                    user_id=UserId(eq=user_id),
                    limit=limit,
                    optimize_query=False,
                )
                if mode != "semantic":
                    search_kwargs["search_mode"] = mode

                results = await self._with_retry(
                    self._client.search_long_term_memory,
                    **search_kwargs,
                )
                return list(results.memories)
            except Exception as error:
                last_error = error
                if mode == "semantic" and allow_keyword_fallback:
                    print(
                        "[WARN] Semantic long-term search failed; "
                        "retrying with keyword search: "
                        f"{error}"
                    )
                    continue
                raise

    async def _scan_long_term_facts_by_seed_queries(
        self,
        *,
        user_id: str,
        limit: int,
        extra_queries: list[str] | None = None,
    ) -> list[dict]:
        """Collects facts using several targeted seed queries.

        AMS retrieval quality depends on query phrasing. When a single
        broad query returns no matches, this method sweeps multiple
        high-signal prompts and merges deduplicated results.

        Args:
            user_id: The stable user identity to search.
            limit: Maximum total facts to return.
            extra_queries: Additional queries to prepend before the
                built-in seed list.

        Returns:
            A deduplicated list of normalized fact dicts.
        """
        fact_map: dict[
            tuple[str, str | None, str | None], dict
        ] = {}
        queries: list[str] = []
        for query in extra_queries or []:
            if query and query not in queries:
                queries.append(query)
        for query in LONG_TERM_FACT_FALLBACK_QUERIES:
            if query not in queries:
                queries.append(query)

        for query in queries:
            try:
                memories = (
                    await self._search_long_term_memory_records(
                        text=query,
                        user_id=user_id,
                        limit=limit,
                        allow_keyword_fallback=False,
                    )
                )
            except Exception as error:
                print(
                    "[WARN] Seeded long-term fact scan failed "
                    f"for '{query}': {error}"
                )
                continue

            for memory in memories:
                if not hasattr(memory, "text"):
                    continue
                fact = self._memory_record_to_fact_dict(memory)
                signature = (
                    fact.get("text"),
                    fact.get("memory_type"),
                    fact.get("event_date"),
                )
                fact_map[signature] = fact
                if len(fact_map) >= limit:
                    return list(fact_map.values())

        return list(fact_map.values())

    async def _wait_for_long_term_indexing(
        self,
        user_id: str,
        expected_texts: list[str],
        max_wait_seconds: float = 6.0,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        """Polls AMS until newly created long-term memories are searchable.

        Long-term indexing is asynchronous. A brief wait here improves
        consistency for immediately subsequent reads.

        Args:
            user_id: The stable user identity that owns the memories.
            expected_texts: Memory text strings to poll for.
            max_wait_seconds: Maximum total time to wait.
            poll_interval_seconds: Delay between poll attempts.
        """
        pending_texts = {text for text in expected_texts if text}
        attempts = max(
            1, int(max_wait_seconds / poll_interval_seconds)
        )

        for _ in range(attempts):
            resolved_texts: set[str] = set()
            for text in pending_texts:
                try:
                    results = (
                        await self._search_long_term_memory_records(
                            text=text,
                            user_id=user_id,
                            limit=5,
                        )
                    )
                except Exception as error:
                    print(
                        "[WARN] Skipping long-term indexing wait "
                        "because search is unavailable: "
                        f"{error}"
                    )
                    return
                if any(
                    getattr(memory, "text", None) == text
                    for memory in results
                ):
                    resolved_texts.add(text)

            pending_texts -= resolved_texts
            if not pending_texts:
                return

            await asyncio.sleep(poll_interval_seconds)

    def _ground_event_date(
        self,
        raw_date: str,
        reference_now: datetime,
    ) -> tuple[datetime, str] | None:
        """Resolves a date phrase to a concrete UTC timestamp.

        Relative phrases (``today``, ``yesterday``, ``next week``,
        etc.) are grounded against the server's current UTC date.
        Absolute dates are parsed from ISO or natural-language
        formats. All dates are stored at midnight UTC.

        Args:
            raw_date: The raw date string extracted from user input.
            reference_now: The current UTC datetime for resolving
                relative phrases.

        Returns:
            A ``(event_date, label)`` tuple where ``event_date`` is a
            midnight-UTC ``datetime`` and ``label`` is a
            human-readable string like ``"April 10, 2026"``.
            Returns ``None`` if the date cannot be parsed.
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
            for date_format in (
                "%Y-%m-%d",
                "%B %d, %Y",
                "%b %d, %Y",
            ):
                try:
                    parsed = datetime.strptime(
                        raw_date.strip(), date_format
                    )
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
            f"{event_date.strftime('%B')} {event_date.day}, "
            f"{event_date.year}"
        )
        return event_date, grounded_label

    async def _with_retry(self, operation, /, *args, **kwargs):
        """Retries AMS SDK calls that fail with transient HTTP errors.

        Retries up to ``_max_retries`` times for network-level
        failures (connection drops, timeouts). Logical HTTP errors
        such as validation failures are not retried.

        Args:
            operation: The async callable to invoke.
            *args: Positional arguments forwarded to ``operation``.
            **kwargs: Keyword arguments forwarded to ``operation``.

        Returns:
            The result of the successful ``operation`` call.

        Raises:
            httpx.RemoteProtocolError: If all retries are exhausted.
            httpx.ReadTimeout: If all retries are exhausted.
            httpx.ConnectError: If all retries are exhausted.
        """
        last_error = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except (
                httpx.RemoteProtocolError,
                httpx.ReadTimeout,
                httpx.ConnectError,
            ) as error:
                last_error = error
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(0.2 * attempt)

        raise last_error

    def _build_chat_label(self, user_message: str) -> str:
        """Builds a short label for a chat from the first user message.

        Args:
            user_message: The first user message in the session.

        Returns:
            A truncated label no longer than 36 characters, or
            ``"Untitled Chat"`` for empty messages.
        """
        cleaned = " ".join(user_message.strip().split())
        if not cleaned:
            return "Untitled Chat"

        if len(cleaned) <= 36:
            return cleaned

        return f"{cleaned[:33].rstrip()}..."

    def _build_preview(self, text: str) -> str:
        """Truncates text for archive list previews.

        Args:
            text: The full message text to preview.

        Returns:
            A truncated string no longer than 72 characters.
        """
        cleaned = " ".join(text.strip().split())
        if len(cleaned) <= 72:
            return cleaned

        return f"{cleaned[:69].rstrip()}..."

    async def close(self) -> None:
        """Closes the underlying HTTP client.

        Called during application shutdown to release open network
        resources explicitly.
        """
        await self._client.close()
