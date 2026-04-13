# =============================================================================
# config.py — Application settings loaded from environment variables
# =============================================================================
# WHY THIS FILE EXISTS:
# Instead of scattering os.getenv() calls throughout the codebase, we
# centralize all configuration here using pydantic-settings. This gives us:
#   1. Type validation — crash at startup if a required var is missing
#   2. A single source of truth — every configurable value lives here
#   3. Easy testing — just instantiate Settings(ANTHROPIC_API_KEY="test-key")
#
# HOW IT WORKS:
# pydantic-settings reads environment variables (and .env files) and maps
# them to Python class attributes. Field names are matched case-insensitively
# to env var names. If a required field has no default and no env var is set,
# the app raises a validation error on startup.
# =============================================================================

from functools import lru_cache  # stdlib caching decorator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Typed configuration for the chat backend.

    Every attribute here maps to an environment variable of the same name.
    For example, `anthropic_api_key` reads from the ANTHROPIC_API_KEY env var.
    """

    # ---- model_config -------------------------------------------------------
    # This inner config tells pydantic-settings WHERE to look for values.
    # `env_file = ".env"` means it will also read a .env file in the working
    # directory (useful for local development). `case_sensitive = False` means
    # ANTHROPIC_API_KEY and anthropic_api_key both work.
    model_config = SettingsConfigDict(
        env_file=".env",        # path to the dotenv file to load
        case_sensitive=False,   # env var matching ignores case
    )

    # ---- Required settings --------------------------------------------------

    # The API key for authenticating with Anthropic's Claude API.
    # No default value → pydantic-settings will raise an error at startup if
    # this env var is missing, which is exactly what we want. Better to fail
    # loudly on boot than silently on the first chat request.
    anthropic_api_key: str

    # ---- Redis Agent Memory Server settings ---------------------------------

    # Base URL for the Agent Memory Server REST API.
    # This backend does NOT talk directly to Redis. Instead, it talks to
    # Agent Memory Server, and Agent Memory Server persists the working memory
    # into Redis for us.
    #
    # Why do it this way?
    #   - Less code in our app: we don't manually manage Redis keys/lists
    #   - Better teaching value: working memory becomes an explicit service
    #   - Easier path to long-term memory later: same client, same server
    memory_api_url: str = "http://localhost:32769"

    # Optional namespace used by Agent Memory Server to group memory records.
    # For the absolute simplest implementation, None means "use the server's
    # default namespace behavior." We keep it configurable so the concept is
    # visible without forcing extra complexity into the first version.
    memory_namespace: str | None = None

    # Interview/demo note:
    # Agent Memory Server's FULL long-term semantic search typically needs an
    # embedding-capable provider configured on the memory server itself.
    # Anthropic is used here for chat completions, but Anthropic does not
    # supply embeddings for AMS long-term vector search. In practice that means:
    # - short-term Redis-backed working memory works with the current setup
    # - long-term archive storage works with the current setup
    # - AMS native long-term semantic retrieval may require extra server-side
    #   configuration (for example, an OPENAI_API_KEY on the AMS process)
    #
    # We keep that complexity out of this backend config because those values
    # belong to the memory server process, not to this FastAPI app.

    # Stable demo identity used when the frontend does not explicitly send a
    # user_id for long-term memory mode. This lets us demonstrate "new chat,
    # same remembered person" without requiring an auth system.
    default_long_term_user_id: str = "demo-long-term-user"

    # Enable this only when the Agent Memory Server process is configured with
    # a working embedding provider for long-term semantic search.
    #
    # Default is False because this repo currently ships only the Anthropic
    # chat key for the FastAPI app, not the extra AMS-side embedding config
    # needed for reliable vector search.
    prefer_ams_long_term_search: bool = False

    # ---- Optional settings with sensible defaults ---------------------------

    # Which Claude model to use for chat completions.
    # We default to claude-haiku-4-5 because:
    #   - It's the fastest Claude model (low latency for demos)
    #   - It's the cheapest ($1/M input, $5/M output tokens)
    #   - It's smart enough for conversational chat
    # You can override this by setting ANTHROPIC_MODEL in your .env file
    # to use a more capable model like "claude-sonnet-4-5" if needed.
    anthropic_model: str = "claude-haiku-4-5"

    # Maximum number of tokens Claude can generate in a single response.
    # 1024 tokens ≈ ~750 words, which is plenty for conversational replies.
    # Setting a cap prevents runaway responses that eat your API budget.
    # O 
    max_tokens: int = 1024

    # The system prompt that defines Claude's personality and behavior.
    # This is sent with every API call as the `system` parameter.
    # It's separate from the user's messages and sets the "ground rules"
    # for how Claude should respond.
    #
    # DESIGN CHOICE: We give the assistant a specific role (DevRel content
    # strategist for Redis) rather than making it a generic chatbot. This
    # serves the demo in three ways:
    #   1. It gives the presentation a story: "this is a tool for US"
    #   2. It makes the memory feature meaningful: the assistant remembers
    #      past content ideas, audience preferences, and roadmap decisions
    #   3. It shows the Redis DevRel team that you understand their world
    #
    # The prompt is intentionally short. We are NOT trying to over-constrain
    # Claude's behavior. We just set the role and let it be conversational.
    # When Redis memory is added later, the stored facts (like "we published
    # a blog about vector search last month") will naturally enrich the
    # context without needing a longer system prompt.
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


# =============================================================================
# get_settings() — Cached settings factory
# =============================================================================
# WHY @lru_cache?
# We only want to read the environment / .env file ONCE. After that, every
# call to get_settings() returns the same Settings instance from memory.
# This is important because:
#   1. Performance — no repeated file I/O or env parsing on every request
#   2. Consistency — the entire app uses the same config object
#   3. FastAPI pattern — this function is used as a dependency (Depends(get_settings))
#      so FastAPI calls it on every request; caching makes that free.
# =============================================================================
@lru_cache()
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    On first call, reads environment variables and .env file.
    On subsequent calls, returns the cached result instantly.
    """
    return Settings()
