"""Seeds long-term memories into Agent Memory Server from a JSON file.

Reads structured memory records from a seed file and writes them to
AMS as long-term memories for the configured default user identity.
Records are deduplicated on write so re-running the script is safe.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from agent_memory_client import MemoryAPIClient, MemoryClientConfig
from agent_memory_client.models import ClientMemoryRecord, MemoryTypeEnum

from backend.app.config import get_settings


SEED_FILE = Path("backend/seeds/devrel_long_term_memories.json")
SEED_ORIGIN_TOPIC = "demo-seed"


def load_seed_rows(path: Path) -> list[dict]:
    """Loads and validates memory records from a JSON seed file.

    Args:
        path: Filesystem path to a JSON file containing an array
            of memory record objects.

    Returns:
        A list of dicts, each representing one memory record.

    Raises:
        ValueError: If the file does not contain a JSON array.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Seed file must contain a JSON array of memory records.")
    return rows


def parse_memory_type(value: str | None) -> MemoryTypeEnum:
    """Converts a string memory type to the corresponding enum value.

    Args:
        value: The memory type string, either ``"semantic"``,
            ``"episodic"``, or ``None`` (defaults to semantic).

    Returns:
        The matching ``MemoryTypeEnum`` member.

    Raises:
        ValueError: If the value is not a recognized memory type.
    """
    if value is None or value == "semantic":
        return MemoryTypeEnum.SEMANTIC
    if value == "episodic":
        return MemoryTypeEnum.EPISODIC
    raise ValueError(f"Unsupported memory_type: {value}")


def build_memory_record(
    row: dict,
    *,
    user_id: str,
) -> ClientMemoryRecord:
    """Builds an AMS ``ClientMemoryRecord`` from a seed data row.

    Args:
        row: A dict containing at minimum a ``text`` key. Optional
            keys include ``topics``, ``entities``, ``memory_type``,
            and ``event_date``.
        user_id: The stable user identity to associate with the record.

    Returns:
        A ``ClientMemoryRecord`` ready for submission to AMS.

    Raises:
        ValueError: If the row is missing a non-empty ``text`` field.
    """
    if not row.get("text"):
        raise ValueError("Each seed record must include a non-empty 'text' field.")

    topics = list(row.get("topics") or [])
    if SEED_ORIGIN_TOPIC not in topics:
        topics.append(SEED_ORIGIN_TOPIC)

    kwargs = {
        "text": row["text"],
        "user_id": user_id,
        "topics": topics,
        "entities": list(row.get("entities") or []),
        "memory_type": parse_memory_type(row.get("memory_type")),
    }
    if row.get("event_date"):
        kwargs["event_date"] = datetime.fromisoformat(row["event_date"])

    return ClientMemoryRecord(**kwargs)


async def main() -> None:
    """Loads seed data and writes long-term memories to AMS."""
    settings = get_settings()
    rows = load_seed_rows(SEED_FILE)

    memories = [
        build_memory_record(
            row,
            user_id=settings.default_long_term_user_id,
        )
        for row in rows
    ]

    client = MemoryAPIClient(
        MemoryClientConfig(
            base_url=settings.memory_api_url,
        )
    )

    try:
        result = await client.create_long_term_memory(memories, deduplicate=True)
    finally:
        await client.close()

    print(
        "Seeded "
        f"{len(memories)} long-term memories for "
        f"'{settings.default_long_term_user_id}'."
    )
    print(f"AMS status: {getattr(result, 'status', 'ok')}")
    for index, memory in enumerate(memories, start=1):
        print(f"{index}. {memory.text}")


if __name__ == "__main__":
    asyncio.run(main())
