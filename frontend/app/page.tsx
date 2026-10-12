"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/Badges";
import { api, formatTime, type Escalation, type Notification } from "@/lib/api";

export default function OverviewPage() {
  const [escalations, setEscalations] = useState<Escalation[] | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.escalations(), api.notifications()])
      .then(([e, n]) => {
        setEscalations(e);
        setNotifications(n);
      })
      .catch(() => setError("Could not load the dashboard."));
  }, []);

  async function markRead(id: string) {
    const updated = await api.markRead(id);
    setNotifications((all) => all.map((n) => (n.id === id ? updated : n)));
  }

  if (error) return <p className="error-text">{error}</p>;
  if (!escalations) return <p className="muted">Loading…</p>;
  const count = (status: Escalation["status"]) => escalations.filter((e) => e.status === status).length;
  const urgentOpen = escalations.filter(
    (e) => e.priority === "URGENT" && (e.status === "PENDING" || e.status === "FAILED"),
  );

  return (
    <>
      <h1>Overview</h1>
      <section className="stats" aria-label="Escalation counts">
        <Link href="/escalations?status=FAILED" className="stat stat-failed">
          <span className="num" data-testid="count-failed">
            {count("FAILED")}
          </span>{" "}
          handoffs failed
        </Link>
        <Link href="/escalations?status=PENDING" className="stat">
          <span className="num" data-testid="count-pending">
            {count("PENDING")}
          </span>{" "}
          awaiting acceptance
        </Link>
        <Link href="/escalations?status=ACCEPTED" className="stat">
          <span className="num" data-testid="count-accepted">
            {count("ACCEPTED")}
          </span>{" "}
          accepted, unresolved
        </Link>
      </section>
      {urgentOpen.length > 0 && (
        <section>
          <h2>Urgent and not yet accepted</h2>
          <ul className="plain">
            {urgentOpen.map((e) => (
              <li key={e.id}>
                <Badge value={e.status} /> <Link href={`/intakes/${e.intake_id}`}>{e.intake_reference}</Link>:{" "}
                {e.reason_text}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <h2>Reviewer inbox</h2>
        {notifications.length === 0 ? (
          <p className="muted">No alerts.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Kind</th>
                <th>To</th>
                <th>Intake</th>
                <th>Message</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {notifications.map((n) => (
                <tr key={n.id} className={n.read_at ? "read" : undefined}>
                  <td>{formatTime(n.created_at)}</td>
                  <td>
                    <Badge value={n.kind} />
                  </td>
                  <td>{n.recipient_role.replace("_", " ").toLowerCase()}</td>
                  <td>{n.intake_reference}</td>
                  <td>{n.message}</td>
                  <td>
                    {n.read_at ? (
                      <span className="muted">read</span>
                    ) : (
                      <button type="button" className="link" onClick={() => markRead(n.id)}>
                        Mark read
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
