import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { RulePreview, RulePreviewPack } from "@/lib/types";

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
  const pack = await attempt<RulePreviewPack>(
    `/api/feeds/${encodeURIComponent(feedId)}/preview-rules`,
    { method: "POST", body: JSON.stringify({ stated: [] }) },
  );

  const crumbs = (
    <p className="note">
      <Link href="/data/intake">Data Intake</Link> /{" "}
      <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / rules
    </p>
  );

  if (isRefused(pack)) {
    return (
      <>
        {crumbs}
        <h1>{feedId} data-quality rules</h1>
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
