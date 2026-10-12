"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { EscalationCard } from "@/components/EscalationCard";
import { api, type Escalation, type EscalationStatus } from "@/lib/api";

const FILTERS: (EscalationStatus | "OPEN" | "ALL")[] = ["OPEN", "FAILED", "PENDING", "ACCEPTED", "RESOLVED", "ALL"];
type Filter = (typeof FILTERS)[number];

function Queue() {
  const router = useRouter();
  const requested = useSearchParams().get("status") as Filter | null;
  const filter: Filter = requested && FILTERS.includes(requested) ? requested : "OPEN";
  const [items, setItems] = useState<Escalation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    api
      .escalations(filter === "OPEN" || filter === "ALL" ? undefined : filter)
      .then((all) => setItems(filter === "OPEN" ? all.filter((e) => e.status !== "RESOLVED") : all))
      .catch(() => setError("Could not load the queue."));
  }, [filter]);

  return (
    <>
      <h1>Escalation queue</h1>
      <div className="filters" role="group" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className={f === filter ? "chip active" : "chip"}
            aria-pressed={f === filter}
            onClick={() => router.replace(`/escalations?status=${f}`)}
          >
            {f.toLowerCase()}
          </button>
        ))}
      </div>
      {error && <p className="error-text">{error}</p>}
      {!items ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">Nothing in this view.</p>
      ) : (
        items.map((e) => (
          <EscalationCard
            key={e.id}
            escalation={e}
            onChange={(updated) => setItems((all) => all && all.map((x) => (x.id === updated.id ? updated : x)))}
          />
        ))
      )}
    </>
  );
}

export default function EscalationsPage() {
  return (
    <Suspense>
      <Queue />
    </Suspense>
  );
}
