import { postBatchAction } from "@/app/operations/control/actions";
import { ActionBar, type ActionSpec } from "@/components/ActionBar";
import type { ActionPreview, ActionSurface } from "@/lib/types";

/**
 * CF-V2-E12-03's console, at last — and CF-V2-E8-04's four recovery members
 * riding the same surface, exactly as `core.operations.actions.OpsAction`
 * says they must: "a second action type would need a second allowed-state
 * matrix, a second refusal path and a second audit shape — and the day they
 * disagreed, the recovery surface would be the one without the guardrails."
 *
 * `surface.offered` IS the only gate this panel applies. It draws a button
 * for every action the server already said it would permit and NO OTHERS —
 * "a console that draws a button and then refuses it teaches people that
 * refusals are noise" is the surface's own docstring, and the discipline
 * belongs here as much as on the wire. The preview sentence
 * (`what_will_happen`) renders ABOVE the button it belongs to, unconditionally
 * — Archetype E's recipe opens with preview-before-execute for a reason, and
 * a hidden preview is not a preview.
 */

const LABELS: Record<string, string> = {
  acknowledge: "Acknowledge",
  assign: "Assign",
  note: "Add note",
  pause: "Pause feed",
  resume: "Resume feed",
  retry: "Retry",
  restart_from_stage: "Restart from stage",
  reprocess_batch: "Reprocess batch",
  reprocess_failed_only: "Reprocess failed rows only",
  backdate: "Backdate",
};

const NEEDS_REASON = new Set(Object.keys(LABELS).filter((action) => action !== "acknowledge"));
const RESUMABLE = new Set(["retry", "restart_from_stage"]);
const STAGES = ["landing", "bronze", "silver_raw", "identity", "silver_ods", "gold"] as const;

export function ActionSurfacePanel({
  batchId,
  panel,
  surface,
}: {
  batchId: string;
  panel: string;
  surface: ActionSurface;
}) {
  if (surface.offered.length === 0) {
    return (
      <div className="card">
        <strong>No action is offered here</strong>
        <p className="note">
          Either the batch&rsquo;s current state permits none of the ten, or your role does not
          hold the permission for the ones that would apply — the same matrix either way, so a
          button never appears only to bounce.
        </p>
      </div>
    );
  }

  const previewFor = (action: string): ActionPreview | undefined =>
    surface.previews.find((p) => p.action === action);

  const actions: ActionSpec[] = surface.offered.map((action) => {
    const preview = previewFor(action);
    return {
      key: action,
      label: LABELS[action] ?? action,
      action: postBatchAction,
      fields: (
        <ActionFields
          action={action}
          panel={panel}
          requiresApprovalIdentifier={preview?.requires_approval_identifier ?? false}
        />
      ),
    };
  });

  return (
    <div className="card">
      <strong>Act on this batch</strong>
      <p className="note">
        Environment: <span className="mono">{surface.environment}</span>
        {surface.environment === "production"
          ? " — every action here except acknowledge, assign and note needs an approval identifier."
          : ""}
      </p>
      <div className="stack">
        {surface.offered.map((action) => {
          const preview = previewFor(action);
          return preview ? (
            <p className="note" key={`preview-${action}`}>
              <strong>{LABELS[action] ?? action}:</strong> {preview.what_will_happen}
            </p>
          ) : null;
        })}
      </div>
      <ActionBar subjectField="batch_id" subjectId={batchId} actions={actions} />
    </div>
  );
}

/** The fields ONE action's form carries, beyond the batch id every form
 *  already has. `ops_action` and `panel` travel as hidden fields so
 *  `postBatchAction` — the single door every one of the ten posts through —
 *  knows which verb was pressed and where to send the operator back. */
function ActionFields({
  action,
  panel,
  requiresApprovalIdentifier,
}: {
  action: string;
  panel: string;
  requiresApprovalIdentifier: boolean;
}) {
  return (
    <>
      <input type="hidden" name="ops_action" value={action} />
      <input type="hidden" name="panel" value={panel} />
      {NEEDS_REASON.has(action) ? (
        <input
          name="reason"
          placeholder={action === "note" ? "The note" : "Why"}
          aria-label={action === "note" ? "Note" : "Reason"}
          required
        />
      ) : null}
      {action === "assign" ? (
        <input name="assignee" placeholder="Assign to" aria-label="Assign to" required />
      ) : null}
      {RESUMABLE.has(action) ? (
        <select name="resume_from" aria-label="Resume from stage" defaultValue="">
          <option value="">last completed stage</option>
          {STAGES.map((stage) => (
            <option key={stage} value={stage}>
              {stage.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      ) : null}
      {action === "backdate" ? (
        <>
          <input type="date" name="business_date" aria-label="Business date" required />
          <label className="note inline">
            <input type="checkbox" name="supersede_acknowledged" value="true" required />
            I have seen the batches this supersedes
          </label>
        </>
      ) : null}
      {requiresApprovalIdentifier ? (
        <input
          name="approval_identifier"
          placeholder="Change record / approval identifier"
          aria-label="Approval identifier"
          required
        />
      ) : null}
    </>
  );
}
