"""Entrypoint script that seeds long-term memory and starts the backend.

Waits for Agent Memory Server to become reachable, seeds initial
long-term memories, then replaces the current process with the
uvicorn server running the FastAPI application.
"""

from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.request

from backend.app.config import get_settings
from backend.scripts.seed_long_term_direct import main as seed_long_term_direct


async def wait_for_ams(health_url: str, attempts: int = 60) -> None:
    """Blocks until Agent Memory Server responds to a health check.

    Args:
        health_url: The full URL of the AMS health endpoint.
        attempts: Maximum number of connection attempts before
            raising an error.

    Raises:
        RuntimeError: If AMS does not become reachable within the
            allowed number of attempts.
    """
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(health_url, timeout=2):
                print(f"[OK] AMS reachable at {health_url}")
                return
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == attempts:
                raise RuntimeError(
                    f"AMS did not become reachable at {health_url}"
                ) from error
            await asyncio.sleep(1)


async def startup() -> None:
    """Runs pre-flight checks and seeds memory before server start."""
    settings = get_settings()
    health_url = settings.memory_api_url.rstrip("/") + "/v1/health"

    await wait_for_ams(health_url)
    await seed_long_term_direct()


if __name__ == "__main__":
    asyncio.run(startup())
    os.execvp(
        "uv",
        [
            "uv",
            "run",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
    )
