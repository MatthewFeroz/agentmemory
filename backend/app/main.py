# =============================================================================
# main.py — FastAPI application entry point
# =============================================================================
# This is the heart of the backend. It:
#   1. Creates the FastAPI application instance
#   2. Initializes the Anthropic service on startup
#   3. Defines the HTTP endpoints (/chat, /health)
#
# HOW TO RUN:
#   uvicorn backend.app.main:app --reload --port 8000
#
# The --reload flag watches for file changes and auto-restarts the server,
# which is invaluable during development. The `app` in `main:app` refers
# to the `app` variable defined below.
#
# AFTER STARTING:
#   - Interactive API docs: http://localhost:8000/docs   (Swagger UI)
#   - Alternative docs:     http://localhost:8000/redoc  (ReDoc)
#   - Health check:         http://localhost:8000/health
#
# WHY FastAPI?
#   - Auto-generates interactive OpenAPI docs (great for demos)
#   - Built-in request validation via Pydantic models
#   - Async support out of the box (we use sync here for simplicity)
#   - Widely adopted in the Python AI/ML community
# =============================================================================

from contextlib import asynccontextmanager  # for lifespan management

from fastapi import FastAPI, HTTPException, Query  # web framework + error handling
from fastapi.middleware.cors import CORSMiddleware  # cross-origin requests

from backend.app.config import get_settings  # our cached settings factory
from backend.app.models import (             # request/response schemas
    ChatRequest,
    ChatResponse,
    LongTermFactsResponse,
    LongTermChatResponse,
    LongTermChatsResponse,
    HealthResponse,
)
from backend.app.services.anthropic import AnthropicService  # Claude wrapper
from backend.app.services.memory import MemoryService         # Redis-backed memory wrapper


# =============================================================================
# Application State — Module-level variable for the Anthropic service
# =============================================================================
# We store the AnthropicService instance here so all endpoints can access it.
# It's initialized during the lifespan startup event (see below).
#
# =============================================================================
_anthropic_service: AnthropicService | None = None
_memory_service: MemoryService | None = None


def _resolve_long_term_user_id(request_user_id: str | None) -> str:
    """
    Resolve the stable identity used for long-term memory.

    Why have a separate helper for this?
    The important architectural idea in the demo is:
    - `session_id` = one chat thread
    - `user_id` = the same person across many chat threads

    By centralizing that rule here, every long-term endpoint uses the same
    identity behavior and the reasoning stays easy to explain.
    """
    settings = get_settings()
    return request_user_id or settings.default_long_term_user_id


def _format_long_term_context(remembered_facts: list[str]) -> str | None:
    """
    Convert stored fact strings into prompt-ready context text.

    We inject this into the system prompt for long-term mode so Claude can use
    remembered facts without us pretending those facts were literal chat turns.
    """
    if not remembered_facts:
        return None

    bullet_lines = "\n".join(f"- {fact}" for fact in remembered_facts)
    return (
        "Use these remembered facts when they are relevant and consistent with "
        "the user's current request:\n"
        f"{bullet_lines}"
    )


# =============================================================================
# Lifespan — Startup and shutdown logic
# =============================================================================
# FastAPI's lifespan context manager runs code BEFORE the first request
# (startup) and AFTER the last request (shutdown). We use it to:
#   - Initialize the Anthropic service (startup)
#   - Clean up resources if needed (shutdown — currently a no-op)
#
# WHY NOT @app.on_event("startup")?
# That decorator is deprecated in modern FastAPI. The lifespan pattern is
# the recommended replacement as of FastAPI 0.93+.
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.

    Everything before `yield` runs on startup.
    Everything after `yield` runs on shutdown.
    """
    # --- STARTUP -------------------------------------------------------------
    global _anthropic_service, _memory_service

    # Load and validate settings from environment variables.
    # If ANTHROPIC_API_KEY is missing, this will raise a validation error
    # and the server will refuse to start — which is what we want.
    settings = get_settings()

    # Create the Anthropic service. This instantiates the HTTP client but
    # does NOT make any API calls yet. The first API call happens when
    # someone hits the /chat endpoint.
    _anthropic_service = AnthropicService(settings)

    # Create the memory service that talks to Agent Memory Server.
    # This is the new piece for Task 2 Part 2.
    #
    # Important architecture note:
    # - AnthropicService talks to Claude
    # - MemoryService talks to Agent Memory Server
    # - Agent Memory Server persists working memory into Redis
    _memory_service = MemoryService(settings)

    # Log a startup message so we can confirm the server is configured correctly.
    print(f"[OK] Chat backend started, model= {settings.anthropic_model}")

    # yield hands control to FastAPI to start serving requests.
    # The server is now live and accepting traffic.
    yield

    # --- SHUTDOWN ------------------------------------------------------------
    # Clean up any long-lived network clients on shutdown.
    if _memory_service is not None:
        await _memory_service.close()

    print("[BYE] Chat backend shutting down, thanks!")


# =============================================================================
# FastAPI Application Instance
# =============================================================================
# This is the object that Uvicorn imports and runs. The parameters here
# configure the auto-generated documentation at /docs.
# =============================================================================
app = FastAPI(
    # The title shown at the top of the /docs page.
    title="Redis DevRel Chat API",

    # A longer description shown in the /docs page. Supports Markdown.
    description=(
        "A chat backend powered by Anthropic's Claude API. "
        "This is the foundation for a Redis DevRel demo that will "
        "showcase LLM memory management using Redis for short-term "
        "and long-term conversation persistence."
    ),

    # API version — follows semantic versioning.
    version="0.1.0",

    # Wire up our lifespan handler for startup/shutdown logic.
    lifespan=lifespan,
)


# =============================================================================
# CORS Middleware — Allow cross-origin requests
# =============================================================================
# CORS (Cross-Origin Resource Sharing) controls which websites can call our
# API. Without this middleware, a frontend running on http://localhost:3000
# would be blocked from calling our API at http://localhost:8000 by the
# browser's same-origin policy.
#
# allow_origins=["*"] means "allow requests from any origin." This is fine
# for local development and demos. In production, you'd restrict this to
# your specific frontend domain(s).
#
# =============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # which origins can access the API
    allow_credentials=True,    # allow cookies/auth headers
    allow_methods=["*"],       # allow all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],       # allow all request headers
)


# =============================================================================
# GET /health — Health check endpoint
# =============================================================================
# A simple endpoint that confirms the server is running and reports the
# configured model. Every production API should have a health endpoint.
#
# Use cases:
#   - Load balancers poll this to know if the server is alive
#   - During demos, hit this first to verify the server is up
#   - CI/CD pipelines check this after deployment
# =============================================================================
@app.get(
    "/health",
    response_model=HealthResponse,  # tells FastAPI the shape of the response
    tags=["System"],                # groups this endpoint in the /docs UI
    summary="Check if the server is running",
)
@app.get(
    "/api/health",
    response_model=HealthResponse,
    include_in_schema=False,
)
async def health_check():
    """
    Returns the server status and currently configured Claude model.

    This endpoint does NOT call the Anthropic API — it only confirms
    that the FastAPI server itself is running and properly configured.
    """
    # Read settings to get the configured model name.
    settings = get_settings()

    return HealthResponse(
        status="ok",
        model=settings.anthropic_model,
    )


# =============================================================================
# GET /long-term/chats â€” List archived chats for one long-term identity
# =============================================================================
# This endpoint supports the frontend's "chat archive" UI for long-term mode.
# It answers the question: "for this stable user identity, what conversation
# threads already exist?"
# =============================================================================
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
    Return archived long-term chat summaries for one stable user identity.

    This is the archive/list side of long-term memory:
    - multiple sessions can belong to one user_id
    - the frontend lists those sessions in a dropdown
    - selecting one later loads the full transcript
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
    except Exception as e:
        print(f"[ERROR] Long-term archive list error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list long-term chats: {str(e)}",
        )

    return LongTermChatsResponse(
        user_id=resolved_user_id,
        chats=chats,
    )


# =============================================================================
# GET /long-term/chats/{session_id} â€” Load one archived chat transcript
# =============================================================================
# The list endpoint above gives us chat summaries. This endpoint loads the
# full transcript for whichever archived session the user selects.
# =============================================================================
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

    The `user_id` filter matters for correctness: it ensures we load the chat
    inside the right long-term identity rather than treating session IDs as
    global, user-less identifiers.
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
    except Exception as e:
        print(f"[ERROR] Long-term archive load error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load long-term chat: {str(e)}",
        )

    return LongTermChatResponse(
        user_id=resolved_user_id,
        session_id=chat["session_id"],
        label=chat["label"],
        messages=chat["messages"],
    )


# =============================================================================
# GET /long-term/facts - Load the durable remembered facts for one user
# =============================================================================
# Archived chats answer "what happened in each conversation?"
# This endpoint answers "what profile-level knowledge survived across them?"
#
# That distinction is important for the interview/demo because it makes the
# Redis-backed long-term memory layer visible instead of leaving it hidden
# behind the model's natural-language responses.
# =============================================================================
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

    This endpoint deliberately does not call Claude.
    It reads the memory layer directly so the frontend can show what AMS and
    Redis have persisted independently of the current chat response.
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
    except Exception as e:
        print(f"[ERROR] Long-term facts load error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load long-term facts: {str(e)}",
        )

    return LongTermFactsResponse(
        user_id=resolved_user_id,
        facts=facts,
    )


# =============================================================================
# POST /chat — Send a message to Claude
# =============================================================================
# This is the main endpoint. It accepts a user message, sends it to Claude
# via the Anthropic service, and returns Claude's response.
#
# WHY POST and not GET?
#   - POST is for actions that have side effects (calling an external API)
#   - POST bodies can be large (GET query strings have size limits)
#   - POST bodies are not logged in access logs (important for privacy)
#
# The `response_model=ChatResponse` parameter tells FastAPI to:
#   1. Validate the return value matches the ChatResponse schema
#   2. Serialize it to JSON automatically
#   3. Document the response shape in the /docs page
# =============================================================================
@app.post(
    "/chat",
    response_model=ChatResponse,   # expected response shape for docs & validation
    tags=["Chat"],                 # groups this endpoint in the /docs UI
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

    Accepts a user message and optional session_id. Sends the message to
    the configured Claude model and returns the response along with
    token usage statistics.

    Raises:
        HTTPException 500: If the Anthropic API call fails (e.g., invalid
            API key, rate limit exceeded, model unavailable).
        HTTPException 503: If the Anthropic service hasn't been initialized
            (server startup failed).
    """
    # --- Guard: ensure the service is initialized ----------------------------
    # This should never happen in normal operation (lifespan initializes it),
    # but defensive coding prevents cryptic NoneType errors.
    if _anthropic_service is None:
        raise HTTPException(
            status_code=503,  # 503 = Service Unavailable
            detail="Anthropic service is not initialized. Check server logs.",
        )

    # Long-term memory needs a stable identity that survives across many chat
    # sessions. Short-term memory does not, because it lives entirely inside a
    # single session_id.
    resolved_user_id = (
        _resolve_long_term_user_id(request.user_id)
        if request.memory_mode == "long-term"
        else None
    )

    uses_working_memory = request.memory_mode in {"short-term", "long-term"}

    # Only requests that use memory depend on the memory service.
    if uses_working_memory and _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    conversation_history: list[dict] | None = None

    # --- Load short-term memory from Redis via Agent Memory Server ----------
    # Requests in "none" mode stay stateless by skipping both the load and
    # store steps. Short-term and long-term currently share working memory.
    if uses_working_memory:
        try:
            conversation_history = await _memory_service.load_conversation_history(
                session_id=request.session_id,
                user_id=resolved_user_id,
            )
        except Exception as e:
            print(f"[Error] Memory load error: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load chat history from memory service: {str(e)}",
            )

    # Long-term mode is more than just "load the current session transcript."
    # We also search persisted facts tied to the same user_id so Claude can
    # remember information across brand-new chat sessions.
    memory_context: str | None = None
    if request.memory_mode == "long-term":
        try:
            remembered_facts = await _memory_service.search_long_term_facts(
                user_id=resolved_user_id,
                query=request.message,
            )
            memory_context = _format_long_term_context(remembered_facts)
        except Exception as e:
            print(f"[ERROR] Long-term memory search error: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to search long-term memory: {str(e)}",
            )

    # --- Call Claude via our service wrapper ----------------------------------
    # We wrap this in a try/except because the Anthropic API can fail for
    # several reasons:
    #   - Invalid API key → AuthenticationError
    #   - Rate limit exceeded → RateLimitError
    #   - Model not found → NotFoundError
    #   - Network issues → APIConnectionError
    #
    # Rather than catching each individually (which would clutter the demo),
    # we catch the broad Exception and return a 500 with the error message.
    # In production, you'd want more granular error handling.
    try:
        result = _anthropic_service.chat(
            user_message=request.message,
            # We now pass the session's prior messages so Claude can answer
            # in the context of the full conversation.
            conversation_history=conversation_history,
            memory_context=memory_context,
        )
    except Exception as e:
        # Log the full error for debugging (visible in the terminal running uvicorn).
        print(f"[ERROR] Anthropic API error: {e}")

        # Return a 500 error with a descriptive message.
        # We include the error string so it's visible in the /docs UI,
        # which is helpful during demos. In production, you'd sanitize this
        # to avoid leaking internal details.
        raise HTTPException(
            status_code=500,  # 500 = Internal Server Error
            detail=f"Failed to get response from Claude: {str(e)}",
        )

    # --- Persist the new conversation turn back into short-term memory -------
    # Once Claude has responded successfully, we store BOTH sides of the turn:
    #   1. the user's latest message
    #   2. Claude's reply
    #
    # We store after the model call rather than before so we don't end up with
    # dangling user-only turns if the Anthropic request fails.
    if uses_working_memory:
        try:
            await _memory_service.store_conversation_turn(
                session_id=request.session_id,
                user_message=request.message,
                assistant_message=result["response"],
                user_id=resolved_user_id,
            )
        except Exception as e:
            print(f"[ERROR] Memory store error: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to store chat history in memory service: {str(e)}",
            )

    # Persist explicit facts only for long-term mode.
    # This is what allows "start a new chat and still remember my name" demos.
    if request.memory_mode == "long-term":
        try:
            await _memory_service.store_long_term_facts(
                session_id=request.session_id,
                user_id=resolved_user_id,
                user_message=request.message,
            )
        except Exception as e:
            print(f"[ERROR] Long-term memory store error: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to store long-term memory: {str(e)}",
            )

    # --- Build and return the response ----------------------------------------
    # Map the service result dict to our Pydantic response model.
    # FastAPI automatically serializes this to JSON.
    return ChatResponse(
        response=result["response"], #Claude's actual text reply
        session_id=request.session_id,  # echo back for client convenience
        model=result["model"], #Tells cleitn which claude model handled it
        usage=result["usage"], #Returns token counts
        user_id=resolved_user_id,
    )
