from datetime import UTC, datetime


def resolve_now(now: datetime | None) -> datetime:
    """Services accept an injectable `now` for deterministic tests; it must be timezone-aware."""
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware")
    return now
