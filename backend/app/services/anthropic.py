# =============================================================================
# services/anthropic.py - Wrapper around the Anthropic Python SDK
# =============================================================================
# WHY THIS FILE EXISTS:
# Rather than calling the Anthropic SDK directly from our FastAPI endpoints,
# we wrap it in a service class. This gives us:
#
#   1. Encapsulation - endpoint code doesn't need to know SDK specifics
#   2. Testability - we can mock AnthropicService in tests without touching
#      the real API
#   3. Single responsibility - if the SDK changes (new params, new version),
#      we only update this one file
#   4. Extension point - the endpoint can hand us either normal transcript
#      history or a fully hydrated AMS memory_prompt payload
# =============================================================================

from anthropic import Anthropic

from backend.app.config import Settings


class AnthropicService:
    """
    Manages communication with the Anthropic Claude API.

    This service is instantiated once at startup and shared across all
    requests. It holds the Anthropic client and settings, and exposes a
    simple `chat()` method that endpoints call.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialize the Anthropic service with application settings.

        Args:
            settings: The application Settings object containing the API key,
                model name, max tokens, and system prompt.
        """
        self._settings = settings
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    def chat(
        self,
        user_message: str | None = None,
        conversation_history: list[dict] | None = None,
        memory_context: str | None = None,
        prepared_messages: list[dict] | None = None,
        system_prompt_override: str | None = None,
    ) -> dict:
        """
        Send a message to Claude and return the response.

        There are two supported prompt-building paths:
        1. Standard chat path: build messages from transcript history.
        2. Hydrated prompt path: use memory_prompt() output directly.
        """
        if prepared_messages is not None:
            messages = list(prepared_messages)
        else:
            messages = list(conversation_history or [])
            messages.append(
                {
                    "role": "user",
                    "content": user_message,
                }
            )

        system_prompt = system_prompt_override or self._settings.system_prompt
        if system_prompt_override is None and memory_context:
            system_prompt = (
                f"{system_prompt}\n\n"
                "Remembered long-term context:\n"
                "These facts were retrieved from Redis-backed memory via "
                "Agent Memory Server. They are available context for this "
                "conversation even if the current session is new. If the user "
                "asks what you remember, answer from these facts directly and "
                "do not claim you have no memory.\n"
                f"{memory_context}"
            )

        api_response = self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.max_tokens,
            system=system_prompt,
            messages=messages,
        )

        response_text = api_response.content[0].text

        return {
            "response": response_text,
            "model": api_response.model,
            "usage": {
                "input_tokens": api_response.usage.input_tokens,
                "output_tokens": api_response.usage.output_tokens,
            },
        }
