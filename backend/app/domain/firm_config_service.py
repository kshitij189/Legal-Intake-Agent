import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import ConfigurationError, NotFound
from app.models import Firm
from app.schemas.firm_config import FirmConfig, IntakeQuestion


def get_active_firm(session: Session, firm_id: uuid.UUID) -> Firm:
    firm = session.scalar(select(Firm).where(Firm.id == firm_id, Firm.is_active.is_(True)))
    if firm is None:
        raise NotFound("firm not found", firm_id=str(firm_id))
    return firm


def load_config(firm: Firm) -> FirmConfig:
    try:
        return FirmConfig.model_validate(firm.config)
    except ValidationError as exc:
        raise ConfigurationError("firm configuration is invalid", firm_id=str(firm.id)) from exc


def get_intake_questions(session: Session, firm_id: uuid.UUID) -> tuple[IntakeQuestion, ...]:
    """Questionnaire of the active firm only."""
    return load_config(get_active_firm(session, firm_id)).intake_questions


def firm_today(firm: Firm, now: datetime) -> date:
    """Calendar date in the firm's own timezone (deadline rules are firm-local)."""
    return now.astimezone(ZoneInfo(firm.timezone)).date()
