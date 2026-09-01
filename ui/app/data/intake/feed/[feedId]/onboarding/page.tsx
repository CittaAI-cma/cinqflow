import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { Tag } from "@/components/Tag";
import { attempt, isRefused } from "@/lib/api";
import type { EvidencePack, Narrative, Obstacle, Wizard, WizardStep } from "@/lib/types";
import { RunSampleTest, SubmitOnboarding } from "./SubmitOnboarding";

/**
 * CF-V1-E4-01/02/03 — the guided journey, and the MVP's headline promise.
 *
 *     "1. Upload sample, 2. Approve schema, 3. Map fields, 4. Define and test
 *      rules, 5. Publish and schedule — with save-and-resume, mandatory checks,
 *      and a readiness checklist throughout"
 *     — CF-V1-E4-01
 *
 * THE BACKEND FOR THIS SCREEN HAS BEEN COMPLETE SINCE THE WIZARD SHIPPED, and
 * no page called any of it: `GET /onboarding`, `GET /evidence`,
 * `GET /narrative` and `POST /onboarding/submit` had no front door at all, so
 * "a trained analyst takes a new feed from a sample file to a published
 * configuration without engineering tickets" was true of the API and false of
 * the product.
 *
 * NOTHING IS COMPUTED HERE. Every step's state, every obstacle and the
 * publishability verdict come from `GET /api/feeds/{id}/onboarding`, which
 * derives them from the governed objects on each request. A client that
 * decided for itself whether step 3 was done would be a second opinion about
 * the lifecycle — and the first thing a BA would find is a green checklist
 * beside a 403.
 *
 * SAVE-AND-RESUME NEEDS NO STATE HERE EITHER. `resume_at` is the first step
 * that is not complete, recomputed every time; there is no `current_step`
 * column anywhere, so "each return resumes exactly where she left off" cannot
 * drift from what is actually approved. Three sessions or three months, the
 * answer is derived the same way.
 */
export default async function OnboardingWizard({
  params,
}: {
  params: Promise<{ feedId: string }>;
}) {
  const { feedId } = await params;
  const id = encodeURIComponent(feedId);

  const [wizard, pack, narrative] = await Promise.all([
    attempt<Wizard>(`/api/feeds/${id}/onboarding`),
    // 404 when no end-to-end test has run — which is the common case at the
    // start and is NOT an error. `attempt` hands back the refusal to render
    // rather than throwing, and this screen reads it as "step 4 has not
    // produced evidence yet".
    attempt<EvidencePack>(`/api/feeds/${id}/evidence`),
    attempt<Narrative>(`/api/feeds/${id}/narrative`),
  ]);
  if (isRefused(wizard)) return <RefusalNotice refusal={wizard} />;

  const evidence = isRefused(pack) ? null : pack;
  const story = isRefused(narrative) ? null : narrative;
  const blocking = wizard.outstanding.filter((obstacle) => obstacle.blocking);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${id}`}>{wizard.feed_id}</Link> / onboarding
      </p>
      <h1>Onboard {wizard.feed_id}</h1>
      <p className="lede">
        {wizard.is_publishable ? (
          <Tag tone="good">Ready to submit</Tag>
        ) : (
          <Tag tone="pending">Resume at step {stepNumber(wizard, wizard.resume_at)}</Tag>
        )}
      </p>
      <p className="note">{wizard.explanation}</p>

      <ol className="tree-list">
        {wizard.steps.map((step) => (
          <StepCard key={step.step} step={step} feedId={id} resumeAt={wizard.resume_at} />
        ))}
      </ol>

      {/* CF-V1-E4-01's exception path, rendered as the story states it: "the
          two fields named and one-click navigation back to them". Named, and
          the link is the platform's own citation route — not a guess this
          client assembled from a step name. */}
      {blocking.length > 0 ? (
        <div className="card">
          <strong>
            {blocking.length} thing{blocking.length === 1 ? "" : "s"} stand
            {blocking.length === 1 ? "s" : ""} between you and done
          </strong>
          <ul className="tree-list">
            {blocking.map((obstacle) => (
              <ObstacleLine key={obstacle.key} obstacle={obstacle} />
            ))}
          </ul>
        </div>
      ) : null}

      {wizard.operations_outstanding.length > 0 ? (
        <div className="card">
          <strong>The registry still needs</strong>
          <p className="note">
            CF-V1-E3-02&apos;s checklist, shown here rather than only on the feed page — one list,
            so the wizard and the registry cannot disagree about whether this feed is ready.
          </p>
          <ul>
            {wizard.operations_outstanding.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {wizard.gaps.length > 0 ? (
        <div className="card">
          <strong>Known gaps — carried honestly into the pack</strong>
          <p className="note">
            These do not block publication. They are listed because the evidence pack is evidence,
            not marketing.
          </p>
          <ul className="tree-list">
            {wizard.gaps.map((gap) => (
              <li key={gap.key}>
                {gap.what}
                <p className="note">{gap.why_it_matters}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <RunSampleTest feedId={wizard.feed_id} hasPack={evidence !== null} />

      <EvidenceCard evidence={evidence} refusal={isRefused(pack) ? pack.detail : null} />

      <SubmitOnboarding
        feedId={wizard.feed_id}
        publishable={wizard.is_publishable}
        blocking={blocking.length}
      />

      {story && story.chapters.length > 0 ? (
        <div className="card">
          <strong>How this feed got here</strong>
          <p className="note">
            CF-V1-E4-03 — who drafted, tested, approved and published, read from the audit ledger.
            An act that produced no audit entry does not appear here, which is correct.
          </p>
          <ul className="tree-list">
            {story.chapters.map((chapter) => (
              <li key={`${chapter.occurred_ts}-${chapter.what}`}>
                <span className="mono">{chapter.occurred_ts.slice(0, 19).replace("T", " ")}</span>{" "}
                — {chapter.who} {chapter.what}
                {chapter.detail ? <span className="note"> · {chapter.detail}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

/** Which of the five `resume_at` names. Read off the server's own ordinals —
 *  never a hard-coded list, which would be a second copy of `core.onboarding
 *  .Step` living in a browser. */
function stepNumber(wizard: Wizard, step: string): number {
  return wizard.steps.find((candidate) => candidate.step === step)?.ordinal ?? 1;
}

function StepCard({
  step,
  feedId,
  resumeAt,
}: {
  step: WizardStep;
  feedId: string;
  resumeAt: string;
}) {
  const here = step.step === resumeAt;
  return (
    <li className={here ? "row" : undefined}>
      <strong>
        {step.ordinal}. {step.label}
      </strong>{" "}
      <Status word={step.status} />
      {step.version !== null ? <span className="note"> · v{step.version}</span> : null}
      {step.citation ? (
        <>
          {" "}
          <CitationChip citationId={step.citation} />
        </>
      ) : null}
      {/* `state` beside `status` on purpose. AWAITING_APPROVAL is deliberately
          NOT complete, and a screen that showed only the status word would
          render "somebody else's move" and "genuinely done" identically —
          which is the exact conflation `core.onboarding.StepState` exists to
          make impossible. */}
      <span className="note"> · {step.state.replace(/_/g, " ")}</span>
      {here && !step.is_complete ? <span className="note"> · you are here</span> : null}
      {step.obstacles.length > 0 ? (
        <ul className="tree-list">
          {step.obstacles.map((obstacle) => (
            <ObstacleLine key={obstacle.key} obstacle={obstacle} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function ObstacleLine({ obstacle }: { obstacle: Obstacle }) {
  return (
    <li>
      {obstacle.route ? (
        <Link href={obstacle.route}>{obstacle.what}</Link>
      ) : (
        <span>{obstacle.what}</span>
      )}
      {obstacle.blocking ? null : <span className="note"> · not blocking</span>}
      <p className="note">{obstacle.why_it_matters}</p>
      <p className="note">To fix: {obstacle.how_to_fix}</p>
    </li>
  );
}

function EvidenceCard({
  evidence,
  refusal,
}: {
  evidence: EvidencePack | null;
  refusal: string | null;
}) {
  if (!evidence) {
    return (
      <div className="card">
        <strong>No evidence pack yet</strong>
        <p className="note">
          {refusal ??
            "Run the end-to-end test on your sample. The pack is generated from that run — it is never assembled by hand."}
        </p>
      </div>
    );
  }
  return (
    <div className="card">
      <strong>Evidence pack</strong>{" "}
      {evidence.partial ? (
        <Tag tone="pending">Partial — the run did not finish</Tag>
      ) : evidence.balanced ? (
        <Tag tone="good">Balanced</Tag>
      ) : (
        <Tag tone="bad">Does not balance</Tag>
      )}
      <p className="note">
        {evidence.rows_in.toLocaleString()} in / {evidence.rows_loaded.toLocaleString()} loaded /{" "}
        {evidence.rows_quarantined.toLocaleString()} quarantined
        {evidence.sample_filename ? ` · ${evidence.sample_filename}` : ""} · produced{" "}
        {evidence.produced_ts.slice(0, 19).replace("T", " ")}
      </p>
      {/* The fingerprint IS the staleness mechanism — CF-V1-E4-03's exception
          path is enforced server-side by comparing it to the configuration at
          submit time, and showing it here is what makes that refusal legible
          rather than mysterious when it fires. */}
      <p className="note">
        Configuration fingerprint <span className="mono">{evidence.fingerprint.slice(0, 16)}</span>{" "}
        — edit a mapping after this run and submission is blocked until you test again.
      </p>
      {evidence.failure ? (
        <p className="note">
          Failed at {evidence.failure.step}: {evidence.failure.explanation}
          {evidence.failure.route ? (
            <>
              {" "}
              <Link href={evidence.failure.route}>Go to the line at fault →</Link>
            </>
          ) : null}
        </p>
      ) : null}
      {evidence.rules.length > 0 ? (
        <div className="scroll">
          <table>
            <caption className="sr-only">Rule hit rates on the sample</caption>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Tested</th>
                <th scope="col">Flagged</th>
                <th scope="col">Hit rate</th>
              </tr>
            </thead>
            <tbody>
              {evidence.rules.map((rule) => (
                <tr className="row" key={rule.rule_id}>
                  <td>
                    <span className="mono">{rule.rule_id}</span> {rule.name}
                    {rule.quarantined ? <span className="note"> · quarantines</span> : null}
                  </td>
                  <td>{rule.tested.toLocaleString()}</td>
                  <td>{rule.flagged.toLocaleString()}</td>
                  <td>{(rule.hit_rate * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {evidence.drops.length > 0 ? (
        <ul className="tree-list">
          {evidence.drops.map((drop) => (
            <li key={`${drop.rule_id}-${drop.reason}`}>
              <span className="mono">{drop.rule_id}</span> — {drop.reason} (
              {drop.record_count.toLocaleString()} records
              {drop.columns.length > 0 ? `, ${drop.columns.join(", ")}` : ""})
            </li>
          ))}
        </ul>
      ) : null}
      <p className="note">
        {evidence.accounts_for_every_row
          ? "Every row is accounted for: loaded plus quarantined equals rows in."
          : "Some rows are unexplained — this pack does not account for every row, and that is stated rather than rounded away."}
      </p>
    </div>
  );
}
