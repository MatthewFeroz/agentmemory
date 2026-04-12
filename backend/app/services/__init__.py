# =============================================================================
# services/__init__.py — Marks this directory as a Python package
# =============================================================================
# This file can be empty. Its presence tells Python that the `services/`
# directory is a package, which allows imports like:
#   from backend.app.services.anthropic import AnthropicService
#
# We keep services in their own sub-package because each service encapsulates
# a specific external integration (Anthropic, Redis, etc.). This separation
# makes it easy to:
#   - Test services in isolation (mock the Anthropic client in tests)
#   - Swap implementations (e.g., switch from Anthropic to OpenAI)
#   - Add new services (Redis) without touching existing code
# =============================================================================
