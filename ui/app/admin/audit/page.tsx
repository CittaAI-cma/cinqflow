import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";

/**
 * Audit Trail — who changed what, and who was refused.
 *
 * Append-only. There is no delete button here because there is no delete verb
 * anywhere in the platform — not on the port, not in the schema, not for
 * administrators. A control that cannot be expressed is stronger than a
 * permission that could be misconfigured.
 */
export default async function Audit() {
  const entries = await attempt<AuditEntry[]>("/api/audit?limit=200");
  if (isRefused(entries)) return <RefusalNotice refusal={entries} />;

  return (
    <>
      <h1>Audit Trail</h1>
      <p className="lede">
        Who changed what, and who was refused. Append-only, for everyone, including
        administrators.
      </p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Type</th>
              <th>Action</th>
              <th>Object</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, index) => (
              <tr className="row" key={index} data-action={entry.action}>
                <td className="mono">{entry.occurred_ts.slice(0, 19).replace("T", " ")}</td>
                <td className="mono">{entry.actor_subject}</td>
                <td>{entry.actor_type}</td>
                <td className={entry.action.startsWith("denied:") ? "uncited" : "mono"}>
                  {entry.action}
                </td>
                <td className="mono">
                  {entry.object_type}:{entry.object_id}
                  {entry.version ? `@v${entry.version}` : ""}
                </td>
                <td className="note">{entry.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card note">
        Actor type is recorded, never inferred — human, system or ai. An AI action that reads
        as human defeats the entire trail.
      </div>
    </>
  );
}
