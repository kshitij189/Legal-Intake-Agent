"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/Badges";
import { api, formatTime, type IntakeListItem, type IntakeStatus } from "@/lib/api";

const STATUSES: (IntakeStatus | "ALL")[] = ["ALL", "PENDING_REVIEW", "INCOMPLETE", "IN_PROGRESS", "CLOSED"];
type Filter = (typeof STATUSES)[number];

export default function IntakesPage() {
  const [status, setStatus] = useState<Filter>("ALL");
  const [items, setItems] = useState<IntakeListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    api
      .intakes(status === "ALL" ? undefined : status)
      .then(setItems)
      .catch(() => setError("Could not load intakes."));
  }, [status]);

  return (
    <>
      <h1>Intakes</h1>
      <label className="inline">
        Status{" "}
        <select value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replaceAll("_", " ").toLowerCase()}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="error-text">{error}</p>}
      {!items ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No intakes.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Reference</th>
              <th>Status</th>
              <th>Caller (as reported)</th>
              <th>Category</th>
              <th>Open escalations</th>
              <th>Consultation</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id}>
                <td>
                  <Link href={`/intakes/${i.id}`}>{i.reference}</Link>
                </td>
                <td>
                  <Badge value={i.status} />
                </td>
                <td>{i.caller_name ?? <span className="muted">not given</span>}</td>
                <td>{i.matter_category ?? "-"}</td>
                <td>{i.open_escalations || "-"}</td>
                <td>{formatTime(i.appointment_starts_at)}</td>
                <td>{formatTime(i.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
