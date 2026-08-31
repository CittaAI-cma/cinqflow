import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Tag } from "@/components/Tag";
import { attempt, isRefused } from "@/lib/api";
import type { ReviewQueue, RulePreview, RulePreviewPack, TechnicalReview } from "@/lib/types";

/**
 * What this feed's rules actually catch. CF-V1-E7-02.
 *
 * THE NUMBERS COME FIRST AND THE PROSE COMES SECOND, which is the inversion the
 * story asks for. "Member first name must be populated" is agreeable on any
 * screen; that it fails 3 of 200 rows is the thing a person can act on.
 * Approving rules from their descriptions is how a Critical rule that
 * quarantines 40% of a roster gets signed on a Tuesday.
 *
 * A rule that could NOT be previewed is shown as loudly as one that failed.
 * Reporting no failures for a check that never ran is the most misleading
 * green a preview can show, and this page refuses to show it.
 */

function Row({ preview }: { preview: RulePreview }) {
  if (!preview.not_previewable) return null;
  return (
    <li key={preview.rule_id}>
      <span className="mono">{preview.rule_id}</span> — {preview.not_previewable}
    </li>
  );
}

export default async function RulePreviewPage({
  params,
}: {
  params: Promise<{ feedId: string }>;
}) {
  const { feedId } = await params;
  const [pack, reviews] = await Promise.all([
    attempt<RulePreviewPack>(`/api/feeds/${encodeURIComponent(feedId)}/preview-rules`, {
      method: "POST",
      body: JSON.stringify({ stated: [] }),
    }),
    attempt<ReviewQueue>(`/api/feeds/${encodeURIComponent(feedId)}/rule-reviews`),
  ]);

  const crumbs = (
    <p className="note">
      <Link href="/data/intake">Data Intake</Link> /{" "}
      <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / rules
    </p>
  );

  const technicalReview = !isRefused(reviews) && reviews.reviews.length > 0 && (
    <TechnicalReviewSection queue={reviews} />
  );

  if (isRefused(pack)) {
    return (
      <>
        {crumbs}
        <h1>{feedId} data-quality rules</h1>
        {technicalReview}
        <RefusalNotice refusal={pack} />
      </>
    );
  }

  const ran = pack.previews.filter((preview) => !preview.not_previewable);
  const notRun = pack.previews.filter((preview) => preview.not_previewable);

  return (
    <>
      {crumbs}
      <h1>{pack.feed_id} data-quality rules</h1>
      {technicalReview}
      <p className="lede">
        {pack.rules_previewed} rule{pack.rules_previewed === 1 ? "" : "s"} run over{" "}
        {pack.sample_rows} sampled row{pack.sample_rows === 1 ? "" : "s"} ·{" "}
        {pack.total_failures} failure{pack.total_failures === 1 ? "" : "s"} found
      </p>

      {notRun.length > 0 ? (
        <div className="card">
          <strong>
            {notRun.length} rule{notRun.length === 1 ? "" : "s"} could not be run against this
            sample
          </strong>
          <p className="note">
            Shown rather than counted as passing. A check that never ran and a check that found
            nothing look identical in a total, and only one of them is evidence.
          </p>
          <ul>
            {notRun.map((preview) => (
              <Row key={preview.rule_id} preview={preview} />
            ))}
          </ul>
        </div>
      ) : null}

      {ran.map((preview) => (
        <div className="card" key={preview.rule_id}>
          <strong>
            {preview.rule_id} — {preview.failed} of {preview.tested} rows failed
          </strong>
          <p>{preview.stated}</p>
          <p className="note">
            What the platform checks: {preview.explanation}
            {preview.skipped > 0 ? (
              <>
                {" "}
                {preview.skipped} row{preview.skipped === 1 ? " was" : "s were"} not tested —
                the column was empty, which is a different rule&apos;s question.
              </>
            ) : null}
          </p>

          {preview.failing_rows.length > 0 ? (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Row</th>
                    {Object.keys(preview.failing_rows[0].values).map((column) => (
                      <th key={column}>
                        {column}
                        {preview.masked_columns.includes(column) ? (
                          <span className="note"> · masked</span>
                        ) : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.failing_rows.map((row) => (
                    <tr className="row" key={row.row_number}>
                      <td className="mono">{row.row_number}</td>
                      {Object.entries(row.values).map(([column, value]) => (
                        <td className="mono" key={column}>
                          {value || "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="note">No row in the sample broke this rule.</p>
          )}
        </div>
      ))}

      <p className="note">
        Protected columns are shown as ••• — the masking happens where the row is read, so an
        unmasked value never reaches this page, a log, or the stored evidence.
      </p>
    </>
  );
}

/**
 * CF-V1-E7-04 — every rule the authoring agent could not draft with
 * confidence, routed here rather than published silently wrong. The BA's own
 * sentence renders FIRST and the machine's reading second, deliberately: a
 * reviewer shown the machine's reading first anchors on it and then checks
 * whether the sentence agrees, which reliably produces agreement.
 *
 * READ-ONLY BY DESIGN, FOR NOW. `core.rules.review.correct/escalate/withdraw`
 * exist and are fully specified, but nothing in this codebase — no route, no
 * worker, no test — calls any of them yet, because a `TechnicalReview` is
 * COMPUTED fresh from the rule-authoring proposal on every read rather than
 * a stored object with its own lifecycle. Wiring a resolution action here
 * honestly needs that persistence question answered first — where does
 * "withdrawn" or "escalated" get remembered between one read and the next —
 * and answering it silently, inside a page component, is the wrong place to
 * make that call. This section makes the queue visible, which it was not at
 * all before; resolving from here is the next, separate piece of work.
 */
function TechnicalReviewSection({ queue }: { queue: ReviewQueue }) {
  return (
    <div className="card">
      <strong>
        {queue.open_count} rule{queue.open_count === 1 ? "" : "s"} needs a person
      </strong>
      <p className="note">
        The authoring agent could not draft these with enough confidence to publish, so they
        never became rules — shown here rather than silently dropped.
      </p>
      {queue.unrouted.length > 0 ? (
        <p className="note">
          <Tag tone="bad">Unrouted</Tag> {queue.unrouted.length} candidate
          {queue.unrouted.length === 1 ? "" : "s"} below the confidence floor did not reach this
          queue — the measurable this screen exists to keep at zero.
        </p>
      ) : null}
      <ul>
        {queue.reviews.map((review) => (
          <TechnicalReviewRow key={review.review_id} review={review} />
        ))}
      </ul>
    </div>
  );
}

function TechnicalReviewRow({ review }: { review: TechnicalReview }) {
  return (
    <li style={{ marginBottom: "var(--s-3)" }}>
      <Tag tone={review.state === "open" ? "bad" : "neutral"}>{review.state}</Tag>{" "}
      <strong>{review.stated}</strong>
      <p className="note">Machine reading: {review.machine_reading}</p>
      <p className="note">{review.explained_to_author}</p>
      {review.confidence > 0 ? (
        <p className="note">Confidence: {Math.round(review.confidence * 100)}%</p>
      ) : null}
    </li>
  );
}
