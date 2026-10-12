"""Request dependencies: a database session and the signed-in staff member (tenant + actor)."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.domain.actors import Actor
from app.domain.firm_config_service import get_active_firm
from app.domain.staff_auth_service import AuthenticationFailed, authenticate
from app.models import Firm, StaffMember
from app.models.enums import StaffRole

SESSION_COOKIE = "li_session"
REVIEWER_ROLES = {StaffRole.LAWYER, StaffRole.INTAKE_COORDINATOR}


def get_db(request: Request) -> Iterator[Session]:
    """One session per request. Write routes commit explicitly; anything else is rolled back."""
    with request.app.state.sessions() as session:
        yield session


Db = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class StaffContext:
    staff: StaffMember
    firm: Firm
    token: str

    @property
    def actor(self) -> Actor:
        return Actor.staff(self.staff.id)

    @property
    def firm_id(self) -> uuid.UUID:
        return self.firm.id

    @property
    def can_review(self) -> bool:
        return self.staff.role in REVIEWER_ROLES


def session_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


def current_staff(request: Request, db: Db) -> StaffContext:
    token = session_token(request)
    if not token:
        raise AuthenticationFailed("not signed in")
    staff = authenticate(db, token)
    return StaffContext(staff=staff, firm=get_active_firm(db, staff.firm_id), token=token)


Staff = Annotated[StaffContext, Depends(current_staff)]
