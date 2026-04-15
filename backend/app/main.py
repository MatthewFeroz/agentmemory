# =============================================================================
# main.py - FastAPI application entry point
# =============================================================================

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.models import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    LongTermChatResponse,
    LongTermChatsResponse,
    LongTermFactsResponse,
    MemoryContext,
)
from backend.app.services.anthropic import AnthropicService
from backend.app.services.memory import MemoryService


_anthropic_service: AnthropicService | None = None
_memory_service: MemoryService | None = None


def _resolve_long_term_user_id(request_user_id: str | None) -> str:
    """
    Resolve the stable identity used for long-term memory.

    `session_id` identifies one chat thread.
    `user_id` identifies the same person across many threads.
    """
    settings = get_settings()
    return request_user_id or settings.default_long_term_user_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.
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
    allow_credentials=True,
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
    """
    Return basic backend health information.
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
    """
    Return archived chat summaries for one stable user identity.
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
    """
    Return the stored transcript for one archived long-term chat.
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
    """
    Return the current long-term fact set for one stable user identity.
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
    """
    Process a chat message through Claude.

    Memory flow:
    - `none`: no memory is loaded or stored
    - `short-term`: load and store transcript working memory
    - `long-term`: hydrate the prompt from working memory + native long-term
      memory, then store the turn back into working memory so AMS can extract
      durable facts in the background
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

    if uses_working_memory and _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    conversation_history: list[dict] | None = None
    prepared_messages: list[dict] | None = None
    system_prompt_override: str | None = None

    # Track how much memory context was loaded for this request.
    # These counters feed the frontend's inline memory status display.
    messages_loaded: int = 0
    long_term_memories_retrieved: int = 0

    if request.memory_mode == "long-term":
        try:
            hydrated_prompt = await _memory_service.build_hydrated_long_term_prompt(
                session_id=request.session_id,
                user_id=resolved_user_id,
                query=request.message,
            )
        except Exception as error:
            print(f"[ERROR] Long-term prompt hydration error: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to hydrate long-term memory prompt: {error}",
            )

        prepared_messages = hydrated_prompt["messages"]
        system_prompt_override = hydrated_prompt["system_prompt"]

        # Count the session messages that were loaded into the hydrated prompt.
        # These are the prior conversation turns from this session's working memory.
        messages_loaded = len(prepared_messages)

        # Count the durable facts that AMS retrieved from long-term memory.
        # These are cross-session memories tied to the user_id, not the session.
        long_term_memories_retrieved = len(
            hydrated_prompt.get("long_term_memories", [])
        )
    elif request.memory_mode == "short-term":
        try:
            conversation_history = await _memory_service.load_conversation_history(
                session_id=request.session_id,
                user_id=resolved_user_id,
            )
        except Exception as error:
            print(f"[ERROR] Memory load error: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load chat history from memory service: {error}",
            )

        # Count how many prior messages were reloaded from this session's
        # working memory transcript. This number grows with each exchange.
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
                    if request.memory_mode == "long-term"
                    else None
                ),
            )
        except Exception as error:
            print(f"[ERROR] Memory store error: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to store chat history in memory service: {error}",
            )

        # In long-term mode, also run deterministic regex-based fact extraction.
        # AMS's background "discrete" strategy is non-deterministic (LLM-driven)
        # and may not extract facts reliably or immediately. For a live demo we
        # need "say X, see X appear" — so we also run our own pattern matching
        # which writes facts to AMS native long-term memory synchronously.
        if request.memory_mode == "long-term":
            try:
                await _memory_service.store_long_term_facts(
                    session_id=request.session_id,
                    user_id=resolved_user_id,
                    user_message=request.message,
                )
            except Exception as error:
                # Non-fatal: the conversation already succeeded and the turn
                # is stored. Log and continue so the user still gets a response.
                print(f"[WARN] Explicit long-term fact extraction error: {error}")

    return ChatResponse(
        response=result["response"],
        session_id=request.session_id,
        model=result["model"],
        usage=result["usage"],
        user_id=resolved_user_id,
        # Attach memory context so the frontend can show what memory
        # operations informed this specific response.
        memory_context=MemoryContext(
            memory_mode=request.memory_mode,
            messages_loaded=messages_loaded,
            long_term_memories_retrieved=long_term_memories_retrieved,
        ),
    )
