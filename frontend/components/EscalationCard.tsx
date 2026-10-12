"use client";

import Link from "next/link";
import { useState } from "react";

import { useStaff } from "@/components/AuthProvider";
import { Badge } from "@/components/Badges";
import { ApiError, api, formatTime, type Escalation } from "@/lib/api";

const HANDOFF_MEANING: Record<Escalation["status"], string> = {
  PENDING: "Queued for review - nobody has accepted it yet.",
  ACCEPTED: "Accepted by a staff member.",
  FAILED: "Reviewer alerts could not be delivered. Assign it manually.",
  RESOLVED: "Resolved.",
};

/** One escalation with its real handoff state and the actions allowed for its status. */
export function EscalationCard({
  escalation,
  onChange,
  showIntakeLink = true,
}: {
  escalation: Escalation;
  onChange: (updated: Escalation) => void;
  showIntakeLink?: boolean;
}) {
  const staff = useStaff();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const e = escalation;

  async function run(action: () => Promise<Escalation>) {
    setBusy(true);
    setError(null);
    try {
      onChange(await action());
      setNote("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The action failed.");
    } finally {
      setBusy(false);
    }
  }

  const canAccept = staff.can_review && e.status === "PENDING";
  const canResolve = staff.can_review && e.status !== "RESOLVED";
  const delivered = e.notifications.filter((n) => n.kind === "NEW_ESCALATION").length > 0;
  const fallback = e.notifications.filter((n) => n.kind.endsWith("FALLBACK"));

  return (
    <article className={`card escalation priority-${e.priority.toLowerCase()}`} data-testid={`escalation-${e.id}`}>
      <div className="row">
        <Badge value={e.priority} />
        <Badge value={e.status} />
        {showIntakeLink && (
          <Link href={`/intakes/${e.intake_id}`} className="ref">
            {e.intake_reference}
          </Link>
        )}
        <span className="muted">{formatTime(e.created_at)}</span>
      </div>
      <p className="reason">
        <strong>{e.reason_code === "CONFIGURED_RULE" ? `Rule ${e.rule_id}` : e.reason_code.replaceAll("_", " ")}:</strong>{" "}
        {e.reason_text}
      </p>
      <p className="muted" data-testid="handoff-meaning">
        {HANDOFF_MEANING[e.status]}
        {e.assigned_to && ` Assigned to ${e.assigned_to} (${formatTime(e.accepted_at)}).`}
      </p>
      {e.last_error && <p className="error-text">Handoff error: {e.last_error}</p>}
      {e.resolution_note && <p>Resolution: {e.resolution_note}</p>}
      <dl className="facts">
        <dt>Reviewer alert</dt>
        <dd>{delivered ? "Delivered" : "Not delivered"}</dd>
        <dt>Fallback alerts</dt>
        <dd>{fallback.length ? fallback.map((n) => n.kind.replaceAll("_", " ")).join(", ") : "None"}</dd>
        <dt>Handoff workflow</dt>
        <dd>
          {e.handoff_workflow
            ? `${e.handoff_workflow.status}${e.handoff_workflow.result ? ` (${e.handoff_workflow.result})` : ""}`
            : "Not recorded"}
        </dd>
      </dl>
      {(canAccept || canResolve) && (
        <div className="actions">
          {canAccept && (
            <button type="button" disabled={busy} onClick={() => run(() => api.accept(e.id))}>
              Accept
            </button>
          )}
          {canResolve && (
            <>
              <input
                aria-label="Resolution note"
                placeholder="Resolution note (required)"
                value={note}
                maxLength={2000}
                onChange={(event) => setNote(event.target.value)}
              />
              <button
                type="button"
                className="secondary"
                disabled={busy || !note.trim()}
                onClick={() => run(() => api.resolve(e.id, note.trim()))}
              >
                Resolve
              </button>
            </>
          )}
        </div>
      )}
      {!staff.can_review && e.status !== "RESOLVED" && (
        <p className="muted">Only lawyers and intake coordinators can accept or resolve escalations.</p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}
