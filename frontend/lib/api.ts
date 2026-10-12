// Typed client for the FastAPI staff API (backend/app/schemas/api.py). Same-origin: /api is
// proxied by next.config.ts, and the session lives in an HttpOnly cookie the page cannot read.

export type Staff = {
  id: string;
  full_name: string;
  email: string;
  role: "LAWYER" | "INTAKE_COORDINATOR" | "ADMINISTRATOR";
  firm_name: string;
  firm_slug: string;
  firm_timezone: string;
  can_review: boolean;
};

export type FieldStatus =
  | "MISSING"
  | "REPORTED_BY_CALLER"
  | "CONFIRMED_BY_CALLER"
  | "DECLINED_BY_CALLER"
  | "PENDING_HUMAN_REVIEW"
  | "VERIFIED_BY_SYSTEM";

export type Field = {
  key: string;
  prompt: string;
  required: boolean;
  value: string | null;
  status: FieldStatus;
  note: string | null;
  source: string | null;
  updated_at: string | null;
  history: { value: string | null; status: FieldStatus; updated_at: string | null }[];
};

export type Workflow = {
  workflow_id: string;
  workflow_type: "HANDOFF" | "BOOKING" | "POST_CALL";
  entity_type: string;
  entity_id: string;
  status: "REQUESTED" | "STARTED" | "COMPLETED" | "FAILED";
  dispatch_attempts: number;
  result: string | null;
  last_error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type Notification = {
  id: string;
  escalation_id: string;
  intake_reference: string | null;
  recipient_role: string;
  kind: "NEW_ESCALATION" | "REMINDER" | "HANDOFF_FAILED_FALLBACK" | "UNACKNOWLEDGED_FALLBACK";
  message: string;
  created_at: string;
  read_at: string | null;
};

export type EscalationStatus = "PENDING" | "ACCEPTED" | "FAILED" | "RESOLVED";

export type Escalation = {
  id: string;
  intake_id: string;
  intake_reference: string;
  reason_code: "CONFIGURED_RULE" | "UNABLE_TO_COMPLETE" | "INCOMPLETE_INTAKE";
  rule_id: string | null;
  reason_text: string;
  priority: "URGENT" | "HIGH" | "NORMAL";
  status: EscalationStatus;
  assigned_to: string | null;
  resolution_note: string | null;
  last_error: string | null;
  created_at: string;
  accepted_at: string | null;
  resolved_at: string | null;
  notifications: Notification[];
  handoff_workflow: Workflow | null;
};

export type IntakeStatus = "IN_PROGRESS" | "INCOMPLETE" | "PENDING_REVIEW" | "CLOSED";

export type IntakeListItem = {
  id: string;
  reference: string;
  status: IntakeStatus;
  caller_name: string | null;
  matter_category: string | null;
  open_escalations: number;
  appointment_starts_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Appointment = {
  id: string;
  intake_id: string;
  intake_reference: string;
  lawyer: string;
  starts_at: string;
  ends_at: string;
  status: "BOOKED";
};

export type Turn = {
  seq: number;
  speaker: "CALLER" | "AGENT";
  text: string;
  interrupted: boolean;
  created_at: string;
};

export type Call = {
  id: string;
  channel: "TEXT" | "VOICE";
  status: string;
  end_reason: string | null;
  started_at: string;
  ended_at: string | null;
  consent: Record<string, unknown>;
  turns: Turn[];
};

export type AuditEvent = {
  seq: number;
  occurred_at: string;
  actor_type: "AGENT" | "STAFF" | "SYSTEM";
  actor_id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  outcome: "SUCCESS" | "FAILURE" | "REJECTED";
  details: Record<string, unknown>;
};

export type IntakeDetail = {
  id: string;
  reference: string;
  status: IntakeStatus;
  summary: string | null;
  outstanding: { missing?: string[]; declined?: string[]; needs_review?: string[] } | null;
  created_at: string;
  updated_at: string;
  fields: Field[];
  escalations: Escalation[];
  appointment: Appointment | null;
  calls: Call[];
  workflows: Workflow[];
  timeline: AuditEvent[];
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    const detail = Array.isArray(data?.detail) ? data.detail[0]?.msg : undefined;
    throw new ApiError(
      response.status,
      error?.code ?? "HTTP_" + response.status,
      error?.message ?? detail ?? `Request failed (${response.status})`,
    );
  }
  return data as T;
}

export const api = {
  login: (firm: string, email: string, password: string) =>
    request<Staff>("POST", "/auth/login", { firm, email, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Staff>("GET", "/auth/me"),
  intakes: (status?: IntakeStatus) =>
    request<IntakeListItem[]>("GET", "/intakes" + (status ? `?status=${status}` : "")),
  intake: (id: string) => request<IntakeDetail>("GET", `/intakes/${id}`),
  updateField: (id: string, key: string, body: { status: FieldStatus; value?: string | null; note?: string | null }) =>
    request<IntakeDetail>("PATCH", `/intakes/${id}/fields/${key}`, body),
  closeIntake: (id: string) => request<IntakeDetail>("POST", `/intakes/${id}/close`),
  escalations: (status?: EscalationStatus) =>
    request<Escalation[]>("GET", "/escalations" + (status ? `?status=${status}` : "")),
  accept: (id: string) => request<Escalation>("POST", `/escalations/${id}/accept`),
  resolve: (id: string, note: string) =>
    request<Escalation>("POST", `/escalations/${id}/resolve`, { note }),
  appointments: (upcoming = false) =>
    request<Appointment[]>("GET", "/appointments" + (upcoming ? "?upcoming=true" : "")),
  workflows: () => request<Workflow[]>("GET", "/workflows"),
  notifications: () => request<Notification[]>("GET", "/notifications"),
  markRead: (id: string) => request<Notification>("POST", `/notifications/${id}/read`),
};

let displayTimeZone: string | undefined;

/** Times are shown in the firm's timezone (set once the staff member is known). */
export function setDisplayTimeZone(zone: string): void {
  displayTimeZone = zone;
}

export function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: displayTimeZone,
  });
}

export const FIELD_STATUS_LABEL: Record<FieldStatus, string> = {
  MISSING: "Missing",
  REPORTED_BY_CALLER: "Reported by caller (unverified)",
  CONFIRMED_BY_CALLER: "Confirmed by caller (unverified)",
  DECLINED_BY_CALLER: "Declined by caller",
  PENDING_HUMAN_REVIEW: "Needs human review",
  VERIFIED_BY_SYSTEM: "Verified by staff",
};
