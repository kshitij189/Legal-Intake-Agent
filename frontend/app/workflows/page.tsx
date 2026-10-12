"use client";

import { useEffect, useState } from "react";

import { WorkflowTable } from "@/components/WorkflowTable";
import { api, type Workflow } from "@/lib/api";

export default function WorkflowsPage() {
  const [items, setItems] = useState<Workflow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .workflows()
      .then(setItems)
      .catch(() => setError("Could not load workflows."));
  }, []);

  return (
    <>
      <h1>Durable workflows</h1>
      <p className="muted">
        Recorded state of each Temporal workflow (outbox row). REQUESTED means the worker has not started it yet.
      </p>
      {error && <p className="error-text">{error}</p>}
      {!items ? <p className="muted">Loading…</p> : <WorkflowTable workflows={items} />}
    </>
  );
}
