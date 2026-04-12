# =============================================================================
# services/anthropic.py — Wrapper around the Anthropic Python SDK
# =============================================================================
# WHY THIS FILE EXISTS:
# Rather than calling the Anthropic SDK directly from our FastAPI endpoints,
# we wrap it in a service class. This gives us:
#
#   1. Encapsulation — endpoint code doesn't need to know SDK specifics
#   2. Testability — we can mock AnthropicService in tests without touching
#      the real API
#   3. Single responsibility — if the SDK changes (new params, new version),
#      we only update this one file
#   4. Extension point — when we add Redis memory (Task 2), we'll modify
#      the `chat()` method to inject conversation history into the messages
#      array, without changing any endpoint code
#
# HOW THE ANTHROPIC MESSAGES API WORKS:
# The Messages API expects:
#   - model: which Claude model to use (e.g., "claude-haiku-4-5")
#   - max_tokens: cap on how many tokens Claude can generate
#   - system: a string that sets Claude's behavior/personality (optional)
#   - messages: a list of {"role": "user"|"assistant", "content": "..."} dicts
#     representing the conversation history
#
# The API returns a Message object with:
#   - content: list of content blocks (usually one TextBlock)
#   - model: the model that was actually used
#   - usage: input_tokens and output_tokens counts
#   - stop_reason: why Claude stopped (e.g., "end_turn", "max_tokens")
# =============================================================================

from anthropic import Anthropic  # The synchronous Anthropic client class

from backend.app.config import Settings


class AnthropicService:
    """
    Manages communication with the Anthropic Claude API.

    This service is instantiated once at startup and shared across all
    requests. It holds the Anthropic client and settings, and exposes
    a simple `chat()` method that endpoints call.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialize the Anthropic service with application settings.

        Args:
            settings: The application Settings object containing the API key,
                      model name, max tokens, and system prompt.

        How the Anthropic client works:
        - `Anthropic(api_key=...)` creates a client that can call Claude.
        - If we omitted api_key, the SDK would auto-read ANTHROPIC_API_KEY
          from the environment. We pass it explicitly for clarity and so
          the dependency on config.py is obvious.
        - The client is thread-safe and reuses HTTP connections internally,
          so it's fine to share one instance across concurrent requests.
        """
        # Store settings so we can reference model, max_tokens, etc. later.
        self._settings = settings

        # Create the Anthropic client. This doesn't make any network calls
        # yet — it just sets up the HTTP client with our API key. The actual
        # API call happens when we call client.messages.create().
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def chat(self, user_message: str, conversation_history: list[dict] | None = None) -> dict:
        """
        Send a message to Claude and return the response.

        This is the main method that endpoints call. Right now it sends a
        single user message with no history. In Task 2 (Redis integration),
        we'll pass `conversation_history` loaded from Redis so Claude can
        "remember" previous messages in the session.

        Args:
            user_message: The text the user typed.
            conversation_history: Optional list of prior messages in
                Anthropic's format: [{"role": "user"|"assistant", "content": "..."}].
                Defaults to None (no history), which means each request is
                independent — Claude has no memory of previous messages.

        Returns:
            A dict with keys:
                - "response": Claude's text reply (str)
                - "model": which model generated the reply (str)
                - "usage": token counts dict with "input_tokens" and "output_tokens"

        How the messages array works:
            The Anthropic API is STATELESS — it doesn't remember previous calls.
            To give Claude context of the conversation, you must send the FULL
            message history every time. This is exactly the problem Redis solves
            in Task 2: we'll store the history in Redis and prepend it here.

            Example with history:
            messages = [
                {"role": "user", "content": "My name is Matthew"},
                {"role": "assistant", "content": "Nice to meet you, Matthew!"},
                {"role": "user", "content": "What's my name?"},  ← current message
            ]
            Without history, Claude would have no idea what the user's name is.
        """
        # --- Build the messages array ----------------------------------------
        # Start with any prior conversation history, or an empty list if this
        # is a fresh/standalone request.
        messages = conversation_history if conversation_history else []

        # Append the current user message to the end of the history.
        # This is always the most recent message — the one Claude needs to
        # respond to right now.
        messages.append({
            "role": "user",           # identifies this as a human message
            "content": user_message,  # the actual text
        })

        # --- Call the Anthropic Messages API ---------------------------------
        # client.messages.create() sends an HTTP POST to Anthropic's servers
        # and blocks until Claude generates a complete response.
        #
        # Parameters:
        #   model — which Claude model to use (from our settings)
        #   max_tokens — maximum tokens Claude can generate (prevents runaway costs)
        #   system — the system prompt that shapes Claude's behavior
        #            This is NOT part of the messages array; it's a separate
        #            parameter that Anthropic handles specially. The system
        #            prompt is always "visible" to Claude but never appears
        #            as a user or assistant message.
        #   messages — the conversation history + current message
        api_response = self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.max_tokens,
            system=self._settings.system_prompt,
            messages=messages,
        )

        # --- Extract the response text ---------------------------------------
        # api_response.content is a LIST of content blocks. In a simple text
        # response, there's usually just one TextBlock. We grab the first one.
        #
        # Why a list? Because Claude can return multiple content blocks in
        # advanced use cases (e.g., tool use, mixed text + images). For our
        # chat demo, it will always be a single TextBlock.
        response_text = api_response.content[0].text

        # --- Build and return the result dict --------------------------------
        # We return a plain dict (not a Pydantic model) because this service
        # shouldn't depend on our API models. The endpoint layer (main.py)
        # will map this dict into the ChatResponse Pydantic model.
        return {
            # The actual text Claude generated
            "response": response_text,

            # Which model processed the request. We read this from the API
            # response (not from settings) because Anthropic might resolve
            # an alias like "claude-haiku-4-5" to a specific version like
            # "claude-haiku-4-5-20251001".
            "model": api_response.model,

            # Token usage stats — useful for monitoring cost and for demos
            # showing how conversation history affects token consumption.
            # api_response.usage is a Usage object with input_tokens and
            # output_tokens attributes; we convert to a dict for JSON safety.
            "usage": {
                "input_tokens": api_response.usage.input_tokens,
                "output_tokens": api_response.usage.output_tokens,
            },
        }
