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

from fastapi import FastAPI, HTTPException  # web framework + error handling
from fastapi.middleware.cors import CORSMiddleware  # cross-origin requests

from backend.app.config import get_settings  # our cached settings factory
from backend.app.models import (             # request/response schemas
    ChatRequest,
    ChatResponse,
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

    # Memory is now part of the request flow, so we guard it explicitly too.
    if _memory_service is None:
        raise HTTPException(
            status_code=503,
            detail="Memory service is not initialized. Check server logs.",
        )

    # --- Load short-term memory from Redis via Agent Memory Server ----------
    # Before we call Claude, we load all prior messages for this session.
    #
    # This is the key idea behind short-term memory:
    # Claude itself is stateless, so we reconstruct the conversation by
    # fetching the session history and sending it back on every request.
    try:
        conversation_history = await _memory_service.load_conversation_history(
            session_id=request.session_id,
        )
    except Exception as e:
        print(f"[Error] Memory load error: {e}")
        raise HTTPException(
            status_code = 500,
            detail=f"Failed to load chat history from memory service: {str(e)}",
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
    try:
        await _memory_service.store_conversation_turn(
            session_id=request.session_id,
            user_message=request.message,
            assistant_message=result["response"],
        )
    except Exception as e:
        print(f"[ERROR] Memory store error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store chat history in memory service: {str(e)}",
        )

    # --- Build and return the response ----------------------------------------
    # Map the service result dict to our Pydantic response model.
    # FastAPI automatically serializes this to JSON.
    return ChatResponse(
        response=result["response"], #Claude's actual text reply
        session_id=request.session_id,  # echo back for client convenience
        model=result["model"], #Tells cleitn which claude model handled it
        usage=result["usage"], #Returns token counts
    )
