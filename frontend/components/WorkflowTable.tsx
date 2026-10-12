import { Badge } from "@/components/Badges";
import { formatTime, type Workflow } from "@/lib/api";

export function WorkflowTable({ workflows }: { workflows: Workflow[] }) {
  if (workflows.length === 0) return <p className="muted">No workflows.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Type</th>
          <th>Status</th>
          <th>Result</th>
          <th>Workflow id</th>
          <th>Requested</th>
          <th>Completed</th>
        </tr>
      </thead>
      <tbody>
        {workflows.map((w) => (
          <tr key={w.workflow_id}>
            <td>{w.workflow_type}</td>
            <td>
              <Badge value={w.status} />
            </td>
            <td>
              {w.result ?? "-"}
              {w.last_error && <div className="error-text">{w.last_error}</div>}
            </td>
            <td className="mono">{w.workflow_id}</td>
            <td>{formatTime(w.created_at)}</td>
            <td>{formatTime(w.completed_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
