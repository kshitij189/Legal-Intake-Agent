import re

from app.domain.errors import ValidationFailed

_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{8,128}$")


def validate_idempotency_key(key: str) -> str:
    if not _KEY.match(key):
        raise ValidationFailed("idempotency key must be 8-128 characters of [A-Za-z0-9_.:-]")
    return key
