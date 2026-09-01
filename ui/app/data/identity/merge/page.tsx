import { redirect } from "next/navigation";
import { RefusalNotice } from "@/components/Refusal";
import { api, attempt, isRefused, Refused } from "@/lib/api";
import type { EvidenceCard, MergeExecuteResult, MergePlan } from "@/lib/types";

/**
 * Merge & Split Review — R4, human-always. CF-V3-E9-03.
 *
 * THREE THINGS THIS SCREEN MAKES STRUCTURALLY TRUE, not just visually true:
 *
 *   1. There is no button anywhere on this page that executes a merge
 *      without a typed approval id. `POST /execute` refuses without one —
 *      this form cannot even ATTEMPT the request that would bypass it,
 *      because the input is required before submit.
 *   2. The preview and the narrative are two different things, rendered so
 *      they cannot be confused: the plan (who repoints, who collapses) comes
 *      from `core.identity.merge.plan_merge`, deterministic and unconditional;
 *      the paragraph beneath it is the model's, and is missing outright —
 *      never a placeholder — the moment no endpoint answered.
 *   3. Declining does not call `/execute` at all. Nothing is repointed,
 *      nothing is marked, and the page says so rather than implying a
 *      decision was recorded somewhere it was not — there is no case store
 *      yet (see the note below the decision form).
 *
 * NO QUEUE YET. A real deployment learns about a candidate merge from
 * Verato's own response; that worker (CF-V3-E9-01's identity stage) is not
 * built. Until it is, this screen reviews the one worked example the
 * platform's ground truth already specifies (L200 -> L100, memory/
 * 05-ground-truth/01-canonical-model.md) end to end, for real, against the
 * real API — which is what proves the flow rather than a screenshot of it.
 */

// The ground-truth scenario, byte-for-byte: two extra addresses on the
// merged-away member, one of which duplicates a survivor address exactly —
// "2 addresses repoint, 1 duplicate collapses".
const WORKED_EXAMPLE = {
  merged_away_member_id: "C2",
  survivor_member_id: "C1",
  merged_away_rows: [
    { entity: "Members_Addresses", record_id: "A1", owner_member_id: "C2", content_key: "123 Main St|Albany|NY" },
    { entity: "Members_Addresses", record_id: "A2", owner_member_id: "C2", content_key: "456 Oak Ave|Albany|NY" },
    { entity: "Members_Addresses", record_id: "A3", owner_member_id: "C2", content_key: "789 Elm St|Albany|NY" },
  ],
  survivor_rows: [
    { entity: "Members_Addresses", record_id: "A9", owner_member_id: "C1", content_key: "789 Elm St|Albany|NY" },
  ],
  demographic_comparison: {
    first_name: "match",
    last_name: "match",
    date_of_birth: "match",
    address: "similar",
  },
};

async function loadWorkedExample(): Promise<void> {
  "use server";
  redirect("/data/identity/merge?loaded=1");
}

async function decide(formData: FormData): Promise<void> {
  "use server";
  const decision = String(formData.get("decision") ?? "");
  const planJson = String(formData.get("plan") ?? "");

  if (decision === "decline") {
    redirect("/data/identity/merge?outcome=DECLINED");
  }

  const approvalId = String(formData.get("steward_approval_id") ?? "").trim();
  const plan = JSON.parse(planJson) as MergePlan;
  try {
    const result = await api<MergeExecuteResult>("/api/identity/merge-preview/execute", {
      method: "POST",
      body: JSON.stringify({ plan, steward_approval_id: approvalId }),
    });
    redirect(
      `/data/identity/merge?outcome=EXECUTED&approval=${encodeURIComponent(result.steward_approval_id)}`,
    );
  } catch (error) {
    // A refusal here is R4 doing exactly its job — rendered, not thrown.
    // `redirect()` throws too, so this must not catch its own call above.
    if (!(error instanceof Refused)) throw error;
    redirect(`/data/identity/merge?loaded=1&outcome=REFUSED&detail=${encodeURIComponent(error.detail)}`);
  }
}

function toneOf(comparison: string): "good" | "bad" | "pending" {
  if (comparison === "match") return "good";
  if (comparison === "differs") return "bad";
  return "pending";
}

export default async function MergeEvidencePage({
  searchParams,
}: {
  searchParams: Promise<{ loaded?: string; outcome?: string; approval?: string; detail?: string }>;
}) {
  const { loaded, outcome, approval, detail } = await searchParams;

  let card: EvidenceCard | null = null;
  let refusal = null;
  if (loaded === "1") {
    const result = await attempt<EvidenceCard>("/api/identity/merge-preview", {
      method: "POST",
      body: JSON.stringify(WORKED_EXAMPLE),
    });
    if (isRefused(result)) refusal = result;
    else card = result;
  }

  return (
    <>
      <h1>Merge &amp; Split Review</h1>
      <p className="lede">
        For a proposed merge, the evidence side by side and every record it would touch. The
        decision is always the steward&rsquo;s — this screen cannot execute one without a typed
        approval id, at any confidence.
      </p>

      {outcome === "EXECUTED" && (
        <div className="outcome card" data-outcome="ACCEPTED">
          <span className="outcome-word">Approved</span>
          <p>
            Recorded under approval <span className="mono">{approval}</span>. The actual
            repointing is a worker&rsquo;s job (I/O) and is not run from this screen; once it has
            run, <span className="mono">verify_post_change</span> confirms the plane matches this
            preview exactly.
          </p>
        </div>
      )}
      {outcome === "DECLINED" && (
        <div className="outcome card" data-outcome="REJECTED">
          <span className="outcome-word">Declined</span>
          <p>Nothing was executed. No repoint, no collapse, no mark.</p>
        </div>
      )}
      {outcome === "REFUSED" && (
        <div className="refusal">
          <strong>Refused</strong>
          <div className="note">{detail}</div>
        </div>
      )}

      {!loaded && (
        <div className="card">
          <p style={{ marginBottom: "var(--s-3)" }}>
            No queue is wired yet — a real deployment learns of a candidate from Verato&rsquo;s
            own response (CF-V3-E9-01, not yet built). Review the platform&rsquo;s own worked
            example instead: two payers report the same address twice; Verato reports L200 merged
            into L100.
          </p>
          <form action={loadWorkedExample}>
            <button className="primary" type="submit">
              Review the L200 → L100 scenario
            </button>
          </form>
        </div>
      )}

      {refusal && <RefusalNotice refusal={refusal} />}

      {card && (
        <>
          <div className="card">
            <h2>Demographics, side by side</h2>
            <dl className="kv">
              {Object.entries(card.demographic_comparison).map(([field, result]) => (
                <div key={field} style={{ display: "contents" }}>
                  <dt>{field.replace(/_/g, " ")}</dt>
                  <dd>
                    <span className="tag" data-tone={toneOf(result)}>
                      {result}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="card">
            <h2>Preview</h2>
            <p className="note" style={{ marginBottom: "var(--s-3)" }}>
              Merging <span className="mono">{card.plan.merged_away_member_id}</span> into{" "}
              <span className="mono">{card.plan.survivor_member_id}</span> would repoint{" "}
              <strong>{card.plan.repoints.length}</strong> record(s) and collapse{" "}
              <strong>{card.plan.collapses.length}</strong> duplicate(s).{" "}
              <span className="mono">{card.plan.marked_merged}</span> would be marked
              merged-to-<span className="mono">{card.plan.survivor_member_id}</span>. Nothing
              affected is hidden from this list.
            </p>
            {card.plan.repoints.length + card.plan.collapses.length === 0 ? (
              <p className="empty">This candidate touches no satellite records.</p>
            ) : (
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Record</th>
                      <th>Entity</th>
                      <th>Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {card.plan.repoints.map((r) => (
                      <tr className="row" key={r.record_id}>
                        <td className="mono">{r.record_id}</td>
                        <td>{r.entity}</td>
                        <td>
                          repoints {r.from_member_id} → {r.to_member_id}
                        </td>
                      </tr>
                    ))}
                    {card.plan.collapses.map((c) => (
                      <tr className="row" key={c.collapsed_record_id}>
                        <td className="mono">{c.collapsed_record_id}</td>
                        <td>{c.entity}</td>
                        <td>collapses into {c.kept_record_id} (duplicate)</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <h2>Evidence card</h2>
            {card.narrative ? (
              <>
                <p>{card.narrative}</p>
                <p className="note">
                  Grounded in: {card.grounded_fields.join(", ") || "nothing named"}
                </p>
              </>
            ) : (
              <p className="empty">
                {card.model_called
                  ? "The model returned nothing usable — the preview above is unaffected."
                  : "No LLM pin is fitted on this deployment. The preview above is complete either way; only this paragraph is missing."}
              </p>
            )}
          </div>

          <div className="card">
            <h2>Decision</h2>
            <form action={decide}>
              <input type="hidden" name="plan" value={JSON.stringify(card.plan)} />
              <div className="field">
                <label htmlFor="steward_approval_id">Approval id</label>
                <input
                  id="steward_approval_id"
                  name="steward_approval_id"
                  placeholder="e.g. APPROVAL-4471"
                  required
                />
              </div>
              <div className="inline action-row">
                <button className="primary" type="submit" name="decision" value="approve">
                  Approve merge
                </button>
                <button type="submit" name="decision" value="decline">
                  Decline
                </button>
              </div>
            </form>
            <p className="note">
              Declining is recorded only on this screen for now — there is no case store yet
              (CF-V3-E9-02&rsquo;s exception queue holds unresolved identities, not declined
              merge decisions). Approving calls the real, governed <span className="mono">
                /execute
              </span>{" "}
              route: refused without an approval id, refused for any role but the data steward,
              and the fingerprint below is checked against exactly this preview.
            </p>
          </div>
        </>
      )}
    </>
  );
}
