"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/Badges";
import { api, formatTime, type Appointment } from "@/lib/api";

export default function AppointmentsPage() {
  const [upcoming, setUpcoming] = useState(true);
  const [items, setItems] = useState<Appointment[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    api
      .appointments(upcoming)
      .then(setItems)
      .catch(() => setError("Could not load bookings."));
  }, [upcoming]);

  return (
    <>
      <h1>Consultation bookings</h1>
      <label className="inline">
        <input type="checkbox" checked={upcoming} onChange={(e) => setUpcoming(e.target.checked)} /> Upcoming only
      </label>
      {error && <p className="error-text">{error}</p>}
      {!items ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No bookings.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Starts</th>
              <th>Ends</th>
              <th>Lawyer</th>
              <th>Intake</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td>{formatTime(a.starts_at)}</td>
                <td>{formatTime(a.ends_at)}</td>
                <td>{a.lawyer}</td>
                <td>
                  <Link href={`/intakes/${a.intake_id}`}>{a.intake_reference}</Link>
                </td>
                <td>
                  <Badge value={a.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
