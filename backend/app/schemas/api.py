"""Request/response models of the staff API (Phase 7). Responses mirror persisted state only."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType,
    AppointmentStatus,
    AuditOutcome,
    CallSessionStatus,
    EscalationReason,
    EscalationStatus,
    FieldStatus,
    IntakeStatus,
    NotificationKind,
    Priority,
    SessionChannel,
    Speaker,
    StaffRole,
    WorkflowStatus,
    WorkflowType,
)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- auth -----------------------------------------------------------------------------------


class LoginIn(In):
    firm: str = Field(min_length=1, max_length=64, description="Firm slug")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class StaffOut(Out):
    id: uuid.UUID
    full_name: str
    email: str
    role: StaffRole
    firm_name: str
    firm_slug: str
    firm_timezone: str  # IANA zone the dashboard displays times in
    can_review: bool  # may accept/resolve escalations, verify fields, close intakes


# --- intakes --------------------------------------------------------------------------------


class PriorValueOut(Out):
    value: str | None
    status: FieldStatus
    updated_at: datetime | None


class FieldOut(Out):
    key: str
    prompt: str
    required: bool
    value: str | None
    status: FieldStatus
    note: str | None
    source: ActorType | None
    updated_at: datetime | None
    history: list[PriorValueOut]


class FieldUpdateIn(In):
    status: FieldStatus
    value: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)


class IntakeListOut(Out):
    id: uuid.UUID
    reference: str
    status: IntakeStatus
    caller_name: str | None
    matter_category: str | None
    open_escalations: int
    appointment_starts_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationOut(Out):
    id: uuid.UUID
    escalation_id: uuid.UUID
    intake_reference: str | None = None
    recipient_role: StaffRole
    kind: NotificationKind
    message: str
    created_at: datetime
    read_at: datetime | None


class WorkflowOut(Out):
    workflow_id: str
    workflow_type: WorkflowType
    entity_type: str
    entity_id: uuid.UUID
    status: WorkflowStatus
    dispatch_attempts: int
    result: str | None
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EscalationOut(Out):
    id: uuid.UUID
    intake_id: uuid.UUID
    intake_reference: str
    reason_code: EscalationReason
    rule_id: str | None
    reason_text: str
    priority: Priority
    status: EscalationStatus
    assigned_to: str | None
    resolution_note: str | None
    last_error: str | None
    created_at: datetime
    accepted_at: datetime | None
    resolved_at: datetime | None
    notifications: list[NotificationOut] = []
    handoff_workflow: WorkflowOut | None = None


class ResolveIn(In):
    note: str = Field(min_length=1, max_length=2000)


class AppointmentOut(Out):
    id: uuid.UUID
    intake_id: uuid.UUID
    intake_reference: str
    lawyer: str
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus


class TurnOut(Out):
    seq: int
    speaker: Speaker
    text: str
    interrupted: bool
    created_at: datetime


class CallOut(Out):
    id: uuid.UUID
    channel: SessionChannel
    status: CallSessionStatus
    end_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    consent: dict[str, Any]
    turns: list[TurnOut]


class AuditOut(Out):
    seq: int
    occurred_at: datetime
    actor_type: ActorType
    actor_id: str
    actor_name: str | None = None  # staff member's name
    action: str
    entity_type: str
    entity_id: uuid.UUID
    outcome: AuditOutcome
    details: dict[str, Any]


class IntakeDetailOut(Out):
    id: uuid.UUID
    reference: str
    status: IntakeStatus
    summary: str | None
    outstanding: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    fields: list[FieldOut]
    escalations: list[EscalationOut]
    appointment: AppointmentOut | None
    calls: list[CallOut]
    workflows: list[WorkflowOut]
    timeline: list[AuditOut]
