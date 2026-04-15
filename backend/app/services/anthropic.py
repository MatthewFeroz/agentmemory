"""Service wrapper for the Anthropic Claude API."""

from anthropic import Anthropic

from backend.app.config import Settings


class AnthropicService:
    """Manages communication with the Anthropic Claude API.

    This service is instantiated once at startup and shared across all
    requests. It holds the Anthropic client and exposes a ``chat()``
    method that endpoints call.

    Attributes:
        _settings: Application configuration containing API credentials
            and model parameters.
        _client: The Anthropic SDK client instance.
    """

    def __init__(self, settings: Settings) -> None:
        """Initializes the Anthropic service.

        Args:
            settings: Application configuration containing the API key,
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
        """Sends a message to Claude and returns the response.

        Supports two prompt-building paths:

        1. **Standard chat**: Builds messages from transcript history
           and appends the new user message.
        2. **Hydrated prompt**: Uses a pre-built message list from
           ``memory_prompt()`` output directly.

        Args:
            user_message: The latest user message text. Required when
                not using ``prepared_messages``.
            conversation_history: Prior conversation turns as a list
                of ``{"role": ..., "content": ...}`` dicts.
            memory_context: Optional long-term memory context string
                to append to the system prompt.
            prepared_messages: Pre-built Anthropic message list. When
                provided, ``conversation_history`` and ``user_message``
                are ignored.
            system_prompt_override: Replaces the default system prompt
                entirely when provided.

        Returns:
            A dict containing:
                - ``response``: Claude's text reply.
                - ``model``: The model identifier that generated the
                  response.
                - ``usage``: A dict with ``input_tokens`` and
                  ``output_tokens`` counts.
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
