"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useStaff } from "@/components/AuthProvider";
import { Badge, FieldStatusBadge } from "@/components/Badges";
import { EscalationCard } from "@/components/EscalationCard";
import { WorkflowTable } from "@/components/WorkflowTable";
import {
  ApiError,
  FIELD_STATUS_LABEL,
  api,
  formatTime,
  type Field,
  type FieldStatus,
  type IntakeDetail,
} from "@/lib/api";

const EDITABLE: FieldStatus[] = [
  "REPORTED_BY_CALLER",
  "CONFIRMED_BY_CALLER",
  "DECLINED_BY_CALLER",
  "PENDING_HUMAN_REVIEW",
  "VERIFIED_BY_SYSTEM",
];

function FieldEditor({
  field,
  canVerify,
  onSave,
  onCancel,
}: {
  field: Field;
  canVerify: boolean;
  onSave: (body: { status: FieldStatus; value: string | null; note: string | null }) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(field.value ?? "");
  const [status, setStatus] = useState<FieldStatus>(field.status === "MISSING" ? "REPORTED_BY_CALLER" : field.status);
  const [note, setNote] = useState(field.note ?? "");
  const [busy, setBusy] = useState(false);
  const declined = status === "DECLINED_BY_CALLER";

  return (
    <form
      className="editor"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        await onSave({ status, value: declined || !value.trim() ? null : value.trim(), note: note.trim() || null });
        setBusy(false);
      }}
    >
      <input aria-label={`New value for ${field.key}`} value={value} disabled={declined} onChange={(e) => setValue(e.target.value)} />
      <select aria-label={`Status for ${field.key}`} value={status} onChange={(e) => setStatus(e.target.value as FieldStatus)}>
        {EDITABLE.filter((s) => canVerify || s !== "VERIFIED_BY_SYSTEM").map((s) => (
          <option key={s} value={s}>
            {FIELD_STATUS_LABEL[s]}
          </option>
        ))}
      </select>
      <input aria-label={`Note for ${field.key}`} placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <button type="submit" disabled={busy}>
        Save
      </button>
      <button type="button" className="link" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

export default function IntakeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const staff = useStaff();
  const [intake, setIntake] = useState<IntakeDetail | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .intake(id)
      .then(setIntake)
      .catch((err) => setError(err instanceof ApiError && err.status === 404 ? "Intake not found." : "Could not load the intake."));
  }, [id]);

  useEffect(load, [load]);

  async function act(action: () => Promise<IntakeDetail>) {
    setActionError(null);
    try {
      setIntake(await action());
      setEditing(null);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "The action failed.");
    }
  }

  if (error) return <p className="error-text">{error}</p>;
  if (!intake) return <p className="muted">Loading…</p>;
  const open = intake.escalations.filter((e) => e.status !== "RESOLVED").length;

  return (
    <>
      <div className="row">
        <h1 data-testid="intake-reference">{intake.reference}</h1>
        <Badge value={intake.status} />
        {staff.can_review && intake.status === "PENDING_REVIEW" && (
          <button
            type="button"
            className="secondary"
            onClick={() => act(() => api.closeIntake(intake.id))}
            title={open ? "Resolve the open escalations first" : undefined}
          >
            Close intake
          </button>
        )}
      </div>
      <p className="muted">
        Draft intake collected by the automated assistant. Caller statements are unverified unless marked
        &quot;Verified by staff&quot;. Created {formatTime(intake.created_at)}.
      </p>
      {actionError && (
        <p className="error-text" role="alert">
          {actionError}
        </p>
      )}

      <section>
        <h2>Summary</h2>
        <p>{intake.summary ?? <span className="muted">No summary (intake not finalized).</span>}</p>
        {intake.outstanding && (
          <dl className="facts">
            <dt>Missing</dt>
            <dd>{intake.outstanding.missing?.join(", ") || "none"}</dd>
            <dt>Declined</dt>
            <dd>{intake.outstanding.declined?.join(", ") || "none"}</dd>
            <dt>Needs review</dt>
            <dd>{intake.outstanding.needs_review?.join(", ") || "none"}</dd>
          </dl>
        )}
      </section>

      <section>
        <h2>Fields and provenance</h2>
        <table>
          <thead>
            <tr>
              <th>Question</th>
              <th>Value</th>
              <th>Status</th>
              <th>Note</th>
              <th>Last change</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {intake.fields.map((f) => (
              <tr key={f.key} data-testid={`field-${f.key}`}>
                <td>
                  {f.prompt}
                  {f.required && <span className="muted"> (required)</span>}
                </td>
                <td>
                  {editing === f.key ? (
                    <FieldEditor
                      field={f}
                      canVerify={staff.can_review}
                      onCancel={() => setEditing(null)}
                      onSave={(body) => act(() => api.updateField(intake.id, f.key, body))}
                    />
                  ) : (
                    <>
                      {f.value ?? <span className="muted">-</span>}
                      {f.history.length > 0 && (
                        <div className="muted small">
                          earlier: {f.history.map((h) => `${h.value ?? "-"} (${h.status.toLowerCase()})`).join("; ")}
                        </div>
                      )}
                    </>
                  )}
                </td>
                <td>
                  <FieldStatusBadge status={f.status} />
                </td>
                <td>{f.note}</td>
                <td className="small">
                  {f.source ? `${f.source.toLowerCase()}, ${formatTime(f.updated_at)}` : "-"}
                </td>
                <td>
                  {intake.status !== "CLOSED" && editing !== f.key && (
                    <span className="nowrap">
                      {staff.can_review && f.value && f.status !== "VERIFIED_BY_SYSTEM" && (
                        <button
                          type="button"
                          className="link"
                          onClick={() =>
                            act(() =>
                              api.updateField(intake.id, f.key, { status: "VERIFIED_BY_SYSTEM", value: f.value, note: f.note }),
                            )
                          }
                        >
                          Verify
                        </button>
                      )}{" "}
                      <button type="button" className="link" onClick={() => setEditing(f.key)}>
                        Edit
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Escalations</h2>
        {intake.escalations.length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          intake.escalations.map((e) => (
            <EscalationCard key={e.id} escalation={e} showIntakeLink={false} onChange={() => load()} />
          ))
        )}
      </section>

      <section>
        <h2>Consultation</h2>
        {intake.appointment ? (
          <p data-testid="appointment">
            <Badge value={intake.appointment.status} /> {formatTime(intake.appointment.starts_at)} with{" "}
            {intake.appointment.lawyer}
          </p>
        ) : (
          <p className="muted">No consultation booked.</p>
        )}
      </section>

      <section>
        <h2>Conversations</h2>
        {intake.calls.length === 0 && <p className="muted">No call sessions.</p>}
        {intake.calls.map((c) => (
          <details key={c.id}>
            <summary>
              {c.channel.toLowerCase()} session, {formatTime(c.started_at)} <Badge value={c.status} />
              {c.end_reason && <span className="muted"> ({c.end_reason})</span>}
            </summary>
            {c.turns.length === 0 ? (
              <p className="muted">No transcript retained.</p>
            ) : (
              <ol className="transcript">
                {c.turns.map((t) => (
                  <li key={t.seq} className={t.speaker.toLowerCase()}>
                    <strong>{t.speaker === "CALLER" ? "Caller" : "Assistant"}:</strong> {t.text}
                    {t.interrupted && <span className="muted"> [interrupted]</span>}
                  </li>
                ))}
              </ol>
            )}
          </details>
        ))}
      </section>

      <section>
        <h2>Workflows</h2>
        <WorkflowTable workflows={intake.workflows} />
      </section>

      <section>
        <h2>Action history</h2>
        <table data-testid="timeline">
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Outcome</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {intake.timeline.map((e) => (
              <tr key={e.seq}>
                <td>{e.seq}</td>
                <td>{formatTime(e.occurred_at)}</td>
                <td>
                  {e.actor_type.toLowerCase()}{" "}
                  <span className="muted small">{e.actor_name ?? e.actor_id.slice(0, 13)}</span>
                </td>
                <td>{e.action}</td>
                <td>
                  <Badge value={e.outcome} />
                </td>
                <td className="mono small">{JSON.stringify(e.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
