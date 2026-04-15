"""Application settings loaded from environment variables.

All configuration is centralized here using ``pydantic-settings``.
Field names are matched case-insensitively to environment variable
names, and missing required values cause a validation error at
startup rather than at request time.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration for the chat backend.

    Every attribute maps to an environment variable of the same name.
    For example, ``anthropic_api_key`` reads from the
    ``ANTHROPIC_API_KEY`` env var.

    Attributes:
        anthropic_api_key: API key for authenticating with the
            Anthropic Claude API.
        memory_api_url: Base URL for the Agent Memory Server REST API.
        default_long_term_user_id: Stable user identity used when the
            client does not send an explicit ``user_id`` for long-term
            memory mode.
        enable_discrete_memory_extraction: Whether to attach the
            ``discrete`` long-term memory strategy when writing
            working memory, allowing AMS to run background extraction.
        prefer_ams_long_term_search: Whether to prefer AMS semantic
            vector search for long-term memory retrieval. Requires a
            working embedding provider on the AMS process.
        anthropic_model: Which Claude model to use for completions.
        max_tokens: Maximum tokens Claude can generate per response.
        system_prompt: System prompt defining Claude's personality
            and behavioral guidelines.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Required — raises a validation error at startup if missing.
    anthropic_api_key: str

    # The backend communicates with Agent Memory Server over HTTP,
    # and AMS persists working memory into Redis.
    memory_api_url: str = "http://localhost:32769"

    # Stable identity used when the client does not explicitly send a
    # user_id for long-term memory mode. This enables the "new chat,
    # same remembered person" workflow without requiring an auth system.
    default_long_term_user_id: str = "default-user"

    # When True, the backend attaches ``long_term_memory_strategy="discrete"``
    # when writing working memory. When False, long-term facts are stored
    # only through the deterministic regex extraction path.
    enable_discrete_memory_extraction: bool = True

    # Requires a working embedding provider configured on the AMS
    # process. Default is True because the standard deployment uses
    # OpenAI embeddings for AMS long-term semantic search.
    prefer_ams_long_term_search: bool = True

    anthropic_model: str = "claude-haiku-4-5"

    # 1024 tokens ≈ ~750 words — sufficient for conversational replies
    # while preventing runaway responses.
    max_tokens: int = 1024

    system_prompt: str = (
        "You are a Developer Relations content strategist for Redis. "
        "You help brainstorm content ideas, plan editorial roadmaps, and "
        "think through what topics would resonate with a developer audience. "
        "You're familiar with the types of content DevRel teams produce: "
        "blog posts, tutorials, conference talks, YouTube videos, livestreams, "
        "sample apps, and documentation. "
        "When the user shares context about past content, audience feedback, "
        "or upcoming priorities, treat that as important information worth "
        "building on in future responses. "
        "Be collaborative and concise — think creative partner, not lecturer."
    )


@lru_cache()
def get_settings() -> Settings:
    """Returns the singleton ``Settings`` instance.

    On first call, reads environment variables and the ``.env`` file.
    Subsequent calls return the cached result immediately.

    Returns:
        The shared application ``Settings`` object.
    """
    return Settings()
