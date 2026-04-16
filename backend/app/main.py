"""FastAPI application entry point for the chat backend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.models import (
    ChatRequest,
    ChatResponse,
    DeleteFactsRequest,
    DeleteFactsResponse,
    ForgetFactsRequest,
    ForgetFactsResponse,
    HealthResponse,
    LongTermChatResponse,
    LongTermChatsResponse,
    LongTermFactsResponse,
    MemoryContext,
    UpdateFactRequest,
    UpdateFactResponse,
)
from backend.app.services.anthropic import AnthropicService
from backend.app.services.memory import MemoryService


_anthropic_service: AnthropicService | None = None
_memory_service: MemoryService | None = None


def _resolve_long_term_user_id(request_user_id: str | None) -> str:
    """Resolves the stable identity used for long-term memory.

    Falls back to the configured default when the client does not
    supply an explicit ``user_id``.

    Args:
        request_user_id: The user ID from the client request, or
            ``None`` if not provided.

    Returns:
        The resolved user identity string.
    """
    settings = get_settings()
    return request_user_id or settings.default_long_term_user_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application startup and shutdown lifecycle.

    Initializes shared service instances on startup and tears down
    open connections on shutdown.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application between startup and
        shutdown phases.
    """
    global _anthropic_service, _memory_service

    settings = get_settings()
    _anthropic_service = AnthropicService(settings)
    _memory_service = MemoryService(settings)

    print(f"[OK] Chat backend started, model={settings.anthropic_model}")
    yield

    if _memory_service is not None:
        await _memory_service.close()

    print("[BYE] Chat backend shutting down")


app = FastAPI(
    title="Redis DevRel Chat API",
    description=(
        "A chat backend powered by Anthropic's Claude API with Redis-backed "
        "short-term and long-term memory through Agent Memory Server."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Check if the server is running",
)
@app.get(
    "/api/health",
    response_model=HealthResponse,
    include_in_schema=False,
)
async def health_check():
    """Returns basic backend health information.

    Returns:
        A ``HealthResponse`` with the current status and configured
        model name.
    """
    settings = get_settings()
    return HealthResponse(status="ok", model=settings.anthropic_model)


@app.get(
    "/long-term/chats",
    response_model=LongTermChatsResponse,
    tags=["Chat"],
    summary="List archived long-term chats for a user",
)
@app.get(
    "/api/long-term/chats",
    response_model=LongTermChatsResponse,
    include_in_schema=False,
)
async def list_long_term_chats(
    user_id: str | None = Query(
        default=None,
        description="Stable user identifier whose archived chats should be listed.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of archived chats to return.",
    ),
):
    """Returns archived chat summaries for one stable user identity.

    Args:
        user_id: Stable user identifier. Falls back to the configured
            default when not provided.
        limit: Maximum number of archived chats to return.

    Returns:
        A ``LongTermChatsResponse`` containing the resolved user ID
        and a list of chat summaries.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            or 500 if the archive listing fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    resolved_user_id = _resolve_long_term_user_id(user_id)

    try:
        chats = await _memory_service.list_long_term_chats(
            user_id=resolved_user_id,
            limit=limit,
        )
    except Exception as error:
        print(f"[ERROR] Long-term archive list error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list long-term chats: {error}",
        )

    return LongTermChatsResponse(user_id=resolved_user_id, chats=chats)


@app.get(
    "/long-term/chats/{session_id}",
    response_model=LongTermChatResponse,
    tags=["Chat"],
    summary="Load one archived long-term chat transcript",
)
@app.get(
    "/api/long-term/chats/{session_id}",
    response_model=LongTermChatResponse,
    include_in_schema=False,
)
async def get_long_term_chat(
    session_id: str,
    user_id: str | None = Query(
        default=None,
        description="Stable user identifier that owns the archived chat.",
    ),
):
    """Returns the stored transcript for one archived long-term chat.

    Args:
        session_id: The archived chat session to load.
        user_id: Stable user identifier. Falls back to the configured
            default when not provided.

    Returns:
        A ``LongTermChatResponse`` containing the session ID, label,
        and full message transcript.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            or 500 if the archive load fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    resolved_user_id = _resolve_long_term_user_id(user_id)

    try:
        chat = await _memory_service.load_long_term_chat(
            session_id=session_id,
            user_id=resolved_user_id,
        )
    except Exception as error:
        print(f"[ERROR] Long-term archive load error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load long-term chat: {error}",
        )

    return LongTermChatResponse(
        user_id=resolved_user_id,
        session_id=chat["session_id"],
        label=chat["label"],
        messages=chat["messages"],
    )


@app.get(
    "/long-term/facts",
    response_model=LongTermFactsResponse,
    tags=["Chat"],
    summary="Load remembered long-term facts for a user",
)
@app.get(
    "/api/long-term/facts",
    response_model=LongTermFactsResponse,
    include_in_schema=False,
)
async def get_long_term_facts(
    user_id: str | None = Query(
        default=None,
        description="Stable user identifier whose remembered facts should be loaded.",
    ),
    limit: int = Query(
        default=12,
        ge=1,
        le=50,
        description="Maximum number of remembered facts to return.",
    ),
):
    """Returns the current long-term fact set for one user identity.

    Args:
        user_id: Stable user identifier. Falls back to the configured
            default when not provided.
        limit: Maximum number of facts to return.

    Returns:
        A ``LongTermFactsResponse`` containing the resolved user ID
        and a list of remembered facts.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            or 500 if the fact retrieval fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    resolved_user_id = _resolve_long_term_user_id(user_id)

    try:
        facts = await _memory_service.list_long_term_facts(
            user_id=resolved_user_id,
            limit=limit,
        )
    except Exception as error:
        print(f"[ERROR] Long-term facts load error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load long-term facts: {error}",
        )

    return LongTermFactsResponse(user_id=resolved_user_id, facts=facts)


# --- Long-term memory management (delete / update / forget) ----------------


@app.delete(
    "/long-term/facts",
    response_model=DeleteFactsResponse,
    tags=["Memory Management"],
    summary="Delete one or more long-term facts by ID",
)
@app.delete(
    "/api/long-term/facts",
    response_model=DeleteFactsResponse,
    include_in_schema=False,
)
async def delete_long_term_facts(request: DeleteFactsRequest):
    """Removes specific long-term memories identified by their server IDs.

    Args:
        request: The ``DeleteFactsRequest`` payload containing the
            list of memory IDs to remove.

    Returns:
        A ``DeleteFactsResponse`` confirming how many memories were
        deleted and which IDs were targeted.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            or 500 if the deletion fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    try:
        deleted_count = await _memory_service.delete_long_term_memories(
            memory_ids=request.memory_ids,
        )
    except Exception as error:
        print(f"[ERROR] Long-term fact delete error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete long-term facts: {error}",
        )

    return DeleteFactsResponse(
        deleted_count=deleted_count,
        memory_ids=request.memory_ids,
    )


@app.patch(
    "/long-term/facts/{memory_id}",
    response_model=UpdateFactResponse,
    tags=["Memory Management"],
    summary="Update a single long-term fact",
)
@app.patch(
    "/api/long-term/facts/{memory_id}",
    response_model=UpdateFactResponse,
    include_in_schema=False,
)
async def update_long_term_fact(
    memory_id: str,
    request: UpdateFactRequest,
):
    """Patches one long-term memory with the supplied field updates.

    Only the fields present in the request body are modified on the
    server; omitted fields remain unchanged.

    Args:
        memory_id: Server-assigned identifier of the memory to edit.
        request: The ``UpdateFactRequest`` payload with updated field
            values.

    Returns:
        An ``UpdateFactResponse`` containing the updated fact record.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            400 if no fields are provided, or 500 if the update fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    updates = request.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=400,
            detail="At least one field must be provided for update.",
        )

    try:
        updated_fact = await _memory_service.update_long_term_memory(
            memory_id=memory_id,
            updates=updates,
        )
    except Exception as error:
        print(f"[ERROR] Long-term fact update error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update long-term fact: {error}",
        )

    return UpdateFactResponse(fact=updated_fact)


@app.post(
    "/long-term/facts/forget",
    response_model=ForgetFactsResponse,
    tags=["Memory Management"],
    summary="Run a policy-driven forgetting pass",
)
@app.post(
    "/api/long-term/facts/forget",
    response_model=ForgetFactsResponse,
    include_in_schema=False,
)
async def forget_long_term_facts(
    request: ForgetFactsRequest,
    user_id: str | None = Query(
        default=None,
        description=(
            "Stable user identifier whose memories should be "
            "evaluated for forgetting."
        ),
    ),
):
    """Runs a policy-driven cleanup over a user's long-term memories.

    Memories that exceed the configured age or inactivity thresholds
    are removed. Use ``dry_run=true`` to preview what would be
    deleted without actually removing anything.

    Args:
        request: The ``ForgetFactsRequest`` payload containing
            forgetting policy thresholds.
        user_id: Stable user identifier. Falls back to the configured
            default when not provided.

    Returns:
        A ``ForgetFactsResponse`` summarizing how many memories were
        scanned, deleted, and their IDs.

    Raises:
        HTTPException: 503 if the memory service is not initialized,
            400 if no policy thresholds are provided, or 500 if the
            forget operation fails.
    """
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    if request.max_age_days is None and request.max_inactive_days is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one policy threshold (max_age_days or "
                "max_inactive_days) must be provided."
            ),
        )

    resolved_user_id = _resolve_long_term_user_id(user_id)

    try:
        result = await _memory_service.forget_long_term_memories(
            user_id=resolved_user_id,
            max_age_days=request.max_age_days,
            max_inactive_days=request.max_inactive_days,
            dry_run=request.dry_run,
        )
    except Exception as error:
        print(f"[ERROR] Long-term fact forget error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to run forgetting pass: {error}",
        )

    return ForgetFactsResponse(
        scanned=result["scanned"],
        deleted=result["deleted"],
        deleted_ids=result["deleted_ids"],
        dry_run=result["dry_run"],
    )


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    summary="Send a message to Claude and get a response",
)
@app.post(
    "/api/chat",
    response_model=ChatResponse,
    include_in_schema=False,
)
async def chat(request: ChatRequest):
    """Processes a chat message through Claude with optional memory.

    Memory behavior by mode:

    - ``none``: Stateless — no memory is loaded or stored.
    - ``short-term``: Loads and stores the session transcript in
      working memory.
    - ``long-term``: Hydrates the prompt from working memory and
      native long-term memory, then stores the turn back so AMS
      can extract durable facts in the background.

    Args:
        request: The incoming ``ChatRequest`` payload.

    Returns:
        A ``ChatResponse`` containing Claude's reply, token usage,
        and memory context metadata.

    Raises:
        HTTPException: 503 if required services are not initialized,
            or 500 if any step in the pipeline fails.
    """
    if _anthropic_service is None:
        raise HTTPException(
            status_code=503,
            detail="Anthropic service is not initialized. Check server logs.",
        )

    resolved_user_id = (
        _resolve_long_term_user_id(request.user_id)
        if request.memory_mode == "long-term"
        else None
    )
    uses_working_memory = request.memory_mode in {"short-term", "long-term"}
    settings = get_settings()

    regex_extraction_active = (
        request.memory_mode == "long-term"
        and request.extraction_mode in {"regex", "both"}
    )
    ams_extraction_active = (
        request.memory_mode == "long-term"
        and request.extraction_mode in {"ams", "both"}
        and settings.enable_discrete_memory_extraction
    )

    if uses_working_memory and _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    conversation_history: list[dict] | None = None
    prepared_messages: list[dict] | None = None
    system_prompt_override: str | None = None
    messages_loaded: int = 0
    long_term_memories_retrieved: int = 0

    if request.memory_mode == "long-term":
        try:
            hydrated_prompt = (
                await _memory_service.build_hydrated_long_term_prompt(
                    session_id=request.session_id,
                    user_id=resolved_user_id,
                    query=request.message,
                )
            )
        except Exception as error:
            print(f"[ERROR] Long-term prompt hydration error: {error}")
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to hydrate long-term memory prompt: {error}"
                ),
            )

        prepared_messages = hydrated_prompt["messages"]
        system_prompt_override = hydrated_prompt["system_prompt"]
        messages_loaded = len(prepared_messages)
        long_term_memories_retrieved = len(
            hydrated_prompt.get("long_term_memories", [])
        )
    elif request.memory_mode == "short-term":
        try:
            conversation_history = (
                await _memory_service.load_conversation_history(
                    session_id=request.session_id,
                    user_id=resolved_user_id,
                )
            )
        except Exception as error:
            print(f"[ERROR] Memory load error: {error}")
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to load chat history from memory service: "
                    f"{error}"
                ),
            )

        messages_loaded = len(conversation_history)

    try:
        result = _anthropic_service.chat(
            user_message=request.message,
            conversation_history=conversation_history,
            prepared_messages=prepared_messages,
            system_prompt_override=system_prompt_override,
        )
    except Exception as error:
        print(f"[ERROR] Anthropic API error: {error}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get response from Claude: {error}",
        )

    if uses_working_memory:
        try:
            await _memory_service.store_conversation_turn(
                session_id=request.session_id,
                user_message=request.message,
                assistant_message=result["response"],
                user_id=resolved_user_id,
                long_term_memory_strategy=(
                    _memory_service.build_default_long_term_memory_strategy()
                    if ams_extraction_active
                    else None
                ),
            )
        except Exception as error:
            print(f"[ERROR] Memory store error: {error}")
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to store chat history in memory service: "
                    f"{error}"
                ),
            )

        # Run deterministic regex-based fact extraction alongside AMS
        # discrete extraction. AMS infers additional memories from the
        # transcript in the background; this path synchronously persists
        # a few high-signal patterns from the current user message.
        if regex_extraction_active:
            try:
                await _memory_service.store_long_term_facts(
                    session_id=request.session_id,
                    user_id=resolved_user_id,
                    user_message=request.message,
                )
            except Exception as error:
                # Non-fatal: the conversation already succeeded and the
                # turn is stored. Log and continue so the user still
                # gets a response.
                print(
                    "[WARN] Explicit long-term fact extraction error: "
                    f"{error}"
                )

    return ChatResponse(
        response=result["response"],
        session_id=request.session_id,
        model=result["model"],
        usage=result["usage"],
        user_id=resolved_user_id,
        memory_context=MemoryContext(
            memory_mode=request.memory_mode,
            messages_loaded=messages_loaded,
            long_term_memories_retrieved=long_term_memories_retrieved,
            extraction_mode=(
                request.extraction_mode
                if request.memory_mode == "long-term"
                else None
            ),
        ),
    )
