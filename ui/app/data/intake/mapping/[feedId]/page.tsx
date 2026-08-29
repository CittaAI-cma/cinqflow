import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Mapping, MappingFinding, MappingLine } from "@/lib/types";

/**
 * The mapping, target field by target field. CF-V1-E6-03.
 *
 * KEYED BY THE TARGET, and the screen is arranged that way on purpose. A
 * source-first listing answers "where does this column go?", which nobody asks;
 * a target-first listing answers "what populates this field?", which is the
 * question a reviewer, a lineage graph and a row-loss investigation all ask —
 * and it is the only arrangement in which an UNMAPPED field is visible at all,
 * because an unmapped target has no source row to appear in.
 *
 * UNMAPPED FIELDS ARE LISTED FIRST. They are the shortest read on the page and
 * the one a steward is signing for: the client's own mapping workbooks keep
 * them on a separate sheet with a Reason column, and this is that sheet.
 */

function statusWord(line: MappingLine): string {
  switch (line.status) {
    case "unmapped":
      return "not mapped";
    case "platform_supplied":
      return "written by the pipeline";
    case "constant":
      return "constant";
    default:
      return line.transform.kind === "direct" ? "direct" : line.transform.kind;
  }
}

function Findings({ findings }: { findings: MappingFinding[] }) {
  const blocking = findings.filter((f) => f.blocks);
  const advisory = findings.filter((f) => !f.blocks);

  return (
    <>
      {blocking.length > 0 ? (
        <div className="card">
          <strong>
            {blocking.length} thing{blocking.length === 1 ? "" : "s"} must be fixed before this
            mapping can be reviewed
          </strong>
          <dl>
            {blocking.map((finding) => (
              <div key={`${finding.key}:${finding.address}`}>
                <dt className="mono">{finding.address}</dt>
                <dd>
                  {finding.what}
                  <p className="note">{finding.why_it_matters}</p>
                  <p className="note">{finding.how_to_fix}</p>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      {advisory.length > 0 ? (
        <div className="card note">
          <strong>Worth knowing</strong>
          <ul>
            {advisory.map((finding) => (
              <li key={`${finding.key}:${finding.address}`}>
                <span className="mono">{finding.address}</span> — {finding.what}.{" "}
                {finding.why_it_matters}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

export default async function MappingPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ version?: string }>;
}) {
  const { feedId } = await params;
  const { version } = await searchParams;
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  const found = await attempt<Mapping>(
    `/api/feeds/${encodeURIComponent(feedId)}/mapping${query}`,
  );

  const crumbs = (
    <p className="note">
      <Link href="/data/intake">Data Intake</Link> /{" "}
      <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / mapping
    </p>
  );

  if (isRefused(found)) {
    return (
      <>
        {crumbs}
        <h1>{feedId} column mapping</h1>
        <RefusalNotice refusal={found} />
        <div className="card note">
          Start from the{" "}
          <Link className="cited" href="/data/canonical">
            canonical model browser
          </Link>
          : you cannot map to a model you cannot see.
        </div>
      </>
    );
  }

  const unmapped = found.lines.filter((line) => line.status === "unmapped");
  const populated = found.lines.filter((line) => line.status !== "unmapped");

  return (
    <>
      {crumbs}
      <h1>{found.feed_id} column mapping</h1>
      <p className="lede">
        <CitationChip citationId={found.citation_id} /> · v{found.version} · {found.lifecycle_state}{" "}
        · {found.mapped_count} of {found.total_count} target fields populated
        {found.contract_version ? <> · against contract v{found.contract_version}</> : null}
      </p>

      <Findings findings={found.findings} />

      {unmapped.length > 0 ? (
        <div className="card">
          <strong>
            {unmapped.length} target field{unmapped.length === 1 ? "" : "s"} deliberately not mapped
          </strong>
          <p className="note">
            Each of these is a decision somebody made, with the reason they gave. A field with no
            source and no reason cannot be saved — the difference between &ldquo;we looked and
            there is nothing&rdquo; and &ldquo;nobody got to it&rdquo; is the whole value of this
            list.
          </p>
          <dl>
            {unmapped.map((line) => (
              <div key={`${line.target_entity}.${line.target_field}`}>
                <dt className="mono">
                  {line.target_entity}.{line.target_field}
                </dt>
                <dd>{line.unmapped_reason}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Target field</th>
              <th>Reads from</th>
              <th>How</th>
              <th>When empty</th>
              <th>Business term</th>
            </tr>
          </thead>
          <tbody>
            {populated.map((line) => (
              <tr className="row" key={`${line.target_entity}.${line.target_field}`}>
                <td className="mono">
                  <Link
                    href={`/data/canonical/${encodeURIComponent(line.target_entity)}`}
                  >
                    {line.target_entity}
                  </Link>
                  .{line.target_field}
                </td>
                <td className="mono">
                  {line.source_columns.length > 0 ? (
                    line.source_columns.join(", ")
                  ) : (
                    <span className="note">—</span>
                  )}
                </td>
                <td>
                  {statusWord(line)}
                  <p className="note">{line.transform.describe}</p>
                </td>
                <td className="note">
                  {line.null_policy === "reject_row"
                    ? "the row is quarantined"
                    : line.null_policy === "coalesce"
                      ? "falls back to the next column"
                      : line.null_policy === "substitute"
                        ? `becomes ${line.default_value ?? ""}`
                        : "stays empty"}
                </td>
                <td>
                  {line.glossary_id ? (
                    <Link href={`/data/intake/glossary/${line.glossary_id}`}>
                      {line.glossary_id}
                    </Link>
                  ) : (
                    <span className="note">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="note">
        Every transform on this page is configuration, not code — a closed set of kinds with
        scalar parameters. There is no expression field anywhere in a mapping, which is what makes
        approving one a matter of reading it.
      </p>
    </>
  );
}
