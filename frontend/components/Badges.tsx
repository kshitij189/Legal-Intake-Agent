import { FIELD_STATUS_LABEL, type FieldStatus } from "@/lib/api";

/** A status label with a colour class per value. Text always carries the meaning. */
export function Badge({ value, label }: { value: string; label?: string }) {
  return <span className={`badge badge-${value.toLowerCase()}`}>{label ?? value.replaceAll("_", " ")}</span>;
}

export function FieldStatusBadge({ status }: { status: FieldStatus }) {
  return <Badge value={status} label={FIELD_STATUS_LABEL[status]} />;
}
