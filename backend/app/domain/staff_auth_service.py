"""Dashboard authentication for staff (local demo): scrypt password hashes and opaque session
tokens. Only a SHA-256 of each token is stored, so a database leak does not leak live sessions."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.actors import Actor
from app.domain.audit import record_audit
from app.domain.clock import resolve_now
from app.domain.errors import PermissionDenied, ValidationFailed
from app.models import Firm, StaffMember, StaffSession

SESSION_TTL = timedelta(hours=8)
MIN_PASSWORD_LENGTH = 10
# scrypt cost (RFC 7914 interactive-login parameters); ~16 MB of memory per hash.
_N, _R, _P = 2**14, 8, 1
# Verified against when the email is unknown, so both failure paths cost the same time.
_DUMMY_HASH = "scrypt$16384$8$1$00000000000000000000000000000000$" + "0" * 64


class AuthenticationFailed(PermissionDenied):
    code = "AUTHENTICATION_FAILED"


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_password(
    session: Session, *, firm_id: uuid.UUID, staff_id: uuid.UUID, password: str
) -> None:
    staff = session.scalar(
        select(StaffMember).where(StaffMember.id == staff_id, StaffMember.firm_id == firm_id)
    )
    if staff is None:
        raise ValidationFailed("staff member not found")
    staff.password_hash = hash_password(password)
    session.flush()


def login(
    session: Session,
    *,
    firm_slug: str,
    email: str,
    password: str,
    now: datetime | None = None,
) -> tuple[str, StaffMember]:
    """Returns (token, staff). Every failure raises the same error, whatever the cause."""
    now = resolve_now(now)
    staff = session.scalar(
        select(StaffMember)
        .join(Firm, Firm.id == StaffMember.firm_id)
        .where(
            Firm.slug == firm_slug,
            Firm.is_active.is_(True),
            StaffMember.email == email.strip().lower(),
        )
    )
    valid = verify_password(
        password, staff.password_hash if staff and staff.password_hash else _DUMMY_HASH
    )
    if staff is None or not staff.is_active or staff.password_hash is None or not valid:
        raise AuthenticationFailed("invalid email or password")
    token = secrets.token_urlsafe(32)
    login_session = StaffSession(
        firm_id=staff.firm_id,
        staff_id=staff.id,
        token_hash=_token_hash(token),
        expires_at=now + SESSION_TTL,
    )
    session.add(login_session)
    session.flush()
    record_audit(
        session,
        firm_id=staff.firm_id,
        actor=Actor.staff(staff.id),
        action="staff.login",
        entity_type="staff_member",
        entity_id=staff.id,
        details={"session_id": str(login_session.id)},
    )
    return token, staff


def authenticate(session: Session, token: str, now: datetime | None = None) -> StaffMember:
    """The active staff member behind a live session token, else AuthenticationFailed."""
    now = resolve_now(now)
    row = session.execute(
        select(StaffSession, StaffMember)
        .join(
            StaffMember,
            (StaffMember.id == StaffSession.staff_id)
            & (StaffMember.firm_id == StaffSession.firm_id),
        )
        .where(StaffSession.token_hash == _token_hash(token))
    ).one_or_none()
    if row is None:
        raise AuthenticationFailed("not signed in")
    login_session: StaffSession = row[0]
    staff: StaffMember = row[1]
    if login_session.revoked_at is not None or login_session.expires_at <= now:
        raise AuthenticationFailed("session expired")
    if not staff.is_active:
        raise AuthenticationFailed("staff member is not active")
    return staff


def logout(session: Session, token: str, now: datetime | None = None) -> None:
    login_session = session.scalar(
        select(StaffSession).where(StaffSession.token_hash == _token_hash(token))
    )
    if login_session is not None and login_session.revoked_at is None:
        login_session.revoked_at = resolve_now(now)
        session.flush()
