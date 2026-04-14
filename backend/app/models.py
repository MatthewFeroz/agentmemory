# =============================================================================
# models.py — Pydantic schemas for API request and response bodies
# =============================================================================
# WHY THIS FILE EXISTS:
# FastAPI uses Pydantic models to:
#   1. Validate incoming JSON — reject malformed requests automatically
#   2. Serialize outgoing JSON — ensure consistent response shapes
#   3. Generate OpenAPI docs — the /docs page reads these schemas to show
#      developers exactly what fields are expected and what they'll get back
#
# Keeping models in their own file (rather than inline in main.py) means:
#   - main.py stays focused on routing logic
#   - models can be imported by tests, services, etc. without circular deps
#   - the API contract for transcript and long-term memory stays centralized
# =============================================================================

from typing import Literal  # constrains a field to an exact set of string values

from pydantic import BaseModel, Field


# =============================================================================
# ChatRequest — What the client sends to POST /chat
# =============================================================================
class ChatRequest(BaseModel):
    """
    The payload a client sends when they want to chat with Claude.

    Example JSON body:
    {
        "message": "Hi, my name is Matthew!",
        "session_id": "user-123-session-abc",
        "memory_mode": "short-term"
    }
    """

    # The user's message text. This is the only truly required field.
    # `min_length=1` prevents empty strings — there's no point sending
    # a blank message to Claude (it would waste an API call).
    # The Field() call also provides description text that shows up in
    # the auto-generated /docs page.
    message: str = Field(
        ...,                            # ... means "required, no default"
        min_length=1,                   # reject empty strings
        description="The user's message to send to Claude.",
        examples=["Hi, my name is Matthew!"],  # shown in /docs UI
    )

    # A unique identifier for the conversation session.
    # The backend uses this to load and replace the correct working-memory
    # transcript on every short-term or long-term request.
    #
    # Default is "default" so the API works out-of-the-box without
    # requiring clients to generate session IDs for simple testing.
    session_id: str = Field(
        default="default",
        description=(
            "Unique session identifier. Used to group messages into "
            "conversations and load the correct working-memory transcript."
        ),
        examples=["user-123-session-abc"],
    )

    # memory_mode tells the backend which "agent behavior" to use.
    #
    # Why Literal instead of plain str?
    # - Validation: FastAPI rejects unsupported values automatically.
    # - Documentation: the generated /docs page shows the allowed values.
    # - Safety: it prevents typos like "shortterm" from silently falling
    #   through to the wrong behavior.
    memory_mode: Literal["none", "short-term", "long-term"] = Field(
        default="none",
        description=(
            "How the backend should handle memory for this request. "
            "'none' keeps the request stateless, 'short-term' uses session "
            "history, and 'long-term' combines session history with "
            "user-level fact memory across chats."
        ),
        examples=["short-term"],
    )

    # user_id is the stable identity that ties multiple long-term chats
    # together. This is different from session_id:
    # - session_id identifies one conversation thread
    # - user_id identifies the same person across conversation threads
    #
    # That distinction is what allows the demo to prove long-term memory:
    # new chat, same user, remembered facts.
    user_id: str | None = Field(
        default=None,
        description=(
            "Stable user identifier used to link multiple long-term "
            "conversations together. Primarily used when memory_mode is "
            "'long-term'."
        ),
        examples=["demo-long-term-user"],
    )


# =============================================================================
# ChatResponse — What the server sends back from POST /chat
# =============================================================================
class ChatResponse(BaseModel):
    """
    The payload returned after Claude processes a message.

    Example JSON response:
    {
        "response": "Hi Matthew! Nice to meet you. How can I help?",
        "session_id": "user-123-session-abc",
        "model": "claude-haiku-4-5",
        "usage": {
            "input_tokens": 42,
            "output_tokens": 18
        }
    }
    """

    # Claude's text response to the user's message.
    response: str = Field(
        ...,
        description="The text response generated by Claude.",
    )

    # Echo back the session_id so the client can confirm which session
    # this response belongs to. Useful when a client manages multiple
    # concurrent conversations.
    session_id: str = Field(
        ...,
        description="The session ID this response belongs to.",
    )

    # Which Claude model actually generated the response.
    # We include this for transparency — during demos, it's helpful to
    # show exactly which model is being used (e.g., haiku vs. sonnet).
    model: str = Field(
        ...,
        description="The Claude model that generated this response.",
    )

    # Token usage statistics from the Anthropic API.
    # This is useful in the demo because memory hydration changes how much
    # context is sent to Claude on each request.
    usage: dict = Field(
        ...,
        description=(
            "Token usage statistics. Contains 'input_tokens' and "
            "'output_tokens' counts from the Anthropic API."
        ),
    )

    # Echo the resolved user_id when the backend used one. This helps the
    # frontend confirm which long-term identity was active for the request.
    user_id: str | None = Field(
        default=None,
        description=(
            "Resolved stable user identifier for the request, when applicable."
        ),
    )


# =============================================================================
# ChatMessage â€” A single chat message in API responses
# =============================================================================
class ChatMessage(BaseModel):
    """
    A normalized chat message returned by archive endpoints.

    We use the same simple shape the frontend already understands:
    role + content. A timestamp is included when it is available from
    stored memory records.
    """

    role: Literal["user", "assistant"] = Field(
        ...,
        description="Whether the message came from the user or assistant.",
    )
    content: str = Field(
        ...,
        description="The message text.",
    )
    timestamp: str | None = Field(
        default=None,
        description="ISO timestamp for when the message was created, if known.",
    )


# =============================================================================
# LongTermChatSummary â€” Metadata shown in the long-term chat picker
# =============================================================================
class LongTermChatSummary(BaseModel):
    """
    Lightweight metadata about an archived long-term conversation.

    This keeps the list endpoint fast and UI-friendly: the frontend can
    show the available chats without loading every full transcript up front.
    """

    session_id: str = Field(
        ...,
        description="Unique identifier for the archived chat session.",
    )
    label: str = Field(
        ...,
        description="Human-friendly label for the chat.",
    )
    message_count: int = Field(
        ...,
        description="Total number of stored messages in the chat.",
    )
    last_updated: str | None = Field(
        default=None,
        description="ISO timestamp of the most recent activity in the chat.",
    )
    preview: str | None = Field(
        default=None,
        description="Short preview of the most recent user-facing content.",
    )


# =============================================================================
# LongTermChatsResponse â€” List of archived chats for one user
# =============================================================================
class LongTermChatsResponse(BaseModel):
    """
    Response for the long-term chat archive listing endpoint.

    It tells the frontend which stable user identity was used and returns
    the archived chats that belong to that identity.
    """

    user_id: str = Field(
        ...,
        description="Stable user identifier used for the archive lookup.",
    )
    chats: list[LongTermChatSummary] = Field(
        ...,
        description="Archived long-term chats for the user.",
    )


# =============================================================================
# LongTermChatResponse â€” Full transcript for one archived chat
# =============================================================================
class LongTermChatResponse(BaseModel):
    """
    Response for loading one archived long-term conversation.

    The frontend uses this when the user selects a previous chat from the
    archive dropdown and needs the transcript restored.
    """

    user_id: str = Field(
        ...,
        description="Stable user identifier that owns the chat.",
    )
    session_id: str = Field(
        ...,
        description="The archived chat session that was loaded.",
    )
    label: str = Field(
        ...,
        description="Human-friendly label for the archived chat.",
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="Transcript of the archived chat.",
    )


# =============================================================================
# LongTermFact - One durable fact remembered across long-term chats
# =============================================================================
class LongTermFact(BaseModel):
    """
    A normalized long-term fact returned to the frontend.

    This gives the UI a direct view into the "remembered profile" layer so
    the demo can prove that Redis-backed memory is persisting more than just
    one transcript.
    """

    text: str = Field(
        ...,
        description="Human-readable fact text stored in long-term memory.",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="Topic labels attached to the fact for organization.",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Key entities or values associated with the fact.",
    )
    memory_type: Literal["semantic", "episodic", "message"] | None = Field(
        default=None,
        description=(
            "What kind of memory this is. 'semantic' stores general facts, "
            "while 'episodic' stores time-grounded events."
        ),
    )
    event_date: str | None = Field(
        default=None,
        description=(
            "ISO timestamp for the event date when this is an episodic memory."
        ),
    )
    source_session_id: str | None = Field(
        default=None,
        description="Chat session where the fact was first observed, if known.",
    )


# =============================================================================
# LongTermFactsResponse - Current long-term fact set for one user identity
# =============================================================================
class LongTermFactsResponse(BaseModel):
    """
    Response for the remembered-facts panel in long-term mode.

    Archive endpoints answer "what happened in a chat?".
    This endpoint answers "what durable knowledge do we remember about the
    same user across many chats?".
    """

    user_id: str = Field(
        ...,
        description="Stable user identifier whose facts were loaded.",
    )
    facts: list[LongTermFact] = Field(
        default_factory=list,
        description="Currently remembered long-term facts for that user.",
    )


# =============================================================================
# HealthResponse — What the server sends back from GET /health
# =============================================================================
class HealthResponse(BaseModel):
    """
    Simple health-check response. Confirms the server is running and
    reports which model is configured.

    Useful for:
      - Load balancers / uptime monitors
      - Quick "is it working?" checks during demos
      - Verifying the correct model is configured
    """

    # Always "ok" if the server is healthy enough to respond.
    status: str = Field(
        ...,
        description="Server health status.",
        examples=["ok"],
    )

    # Which model the server is configured to use.
    # Helpful during demos to confirm you're hitting the right model.
    model: str = Field(
        ...,
        description="The Claude model currently configured.",
    )
