import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * One file profile. The destination a `profile:<fingerprint>` citation opens,
 * and step 1 of the onboarding wizard.
 *
 * Everything rendered here is arithmetic over the sample's bytes — no model was
 * called. `?column=` opens one column as a panel rather than a page, which is
 * the one depth level the citation address space allows.
 *
 * The id IS the fingerprint of the facts, so this page is stable: the same
 * sample profiled again resolves to the same address, and a link in a ticket
 * keeps working.
 */
type TypeCandidate = { type: string; matched: number; considered: number };
type ColumnProfile = {
  name: string;
  position: number;
  row_count: number;
  null_count: number;
  null_like_count: number;
  distinct_count: number;
  distinct_is_exact: boolean;
  is_unique: boolean | null;
  narrowest_type: string | null;
  type_candidates: TypeCandidate[];
  date_formats: { label: string; matched: number }[];
  observed_precision: number | null;
  observed_scale: number | null;
  examples: string[];
  values_redacted: boolean;
  citation_id: string;
};
type Finding = {
  quirk: string;
  detail: string;
  occurrences: number;
  first_lines: number[];
  blocks_ingestion: boolean;
};
type KeyCandidate = {
  columns: string[];
  distinct_count: number;
  populated_rows: number;
  null_rows: number;
  duplicate_values: number;
  is_unique: boolean;
};
type FileProfile = {
  profile_id: string;
  feed_id: string;
  source_key: string;
  readable: boolean;
  would_load: boolean;
  refusal: { reason: string; explanation: string; ask_the_payer: string } | null;
  structure: {
    file_format: string;
    encoding: string;
    delimiter: string | null;
    byte_order_mark: string | null;
    column_count: number;
    data_rows: number;
    sampled: boolean;
  };
  columns: ColumnProfile[];
  findings: Finding[];
  blockers: Finding[];
  key_candidates: KeyCandidate[];
  key_search: { pairs_examined: number; pairs_skipped: number; note: string };
  duplicate_rows: number;
  values_redacted: boolean;
  profiled_by: string;
};

export default async function ProfilePage({
  params,
  searchParams,
}: {
  params: Promise<{ profileId: string }>;
  searchParams: Promise<{ column?: string }>;
}) {
  const { profileId } = await params;
  const { column } = await searchParams;
  const result = await attempt<FileProfile>(`/api/profiles/${encodeURIComponent(profileId)}`);

  if (isRefused(result)) {
    return (
      <>
        <p className="note">
          <Link href="/data/intake">Data Intake</Link> / profile
        </p>
        <h1>File profile</h1>
        <RefusalNotice refusal={result} />
      </>
    );
  }

  const selected = column ? result.columns.find((c) => c.name === column) : undefined;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${result.feed_id}`}>{result.feed_id}</Link> / profile
      </p>
      <h1>{result.source_key.split("/").pop()}</h1>
      <p className="lede">
        <CitationChip citationId={`profile:${result.profile_id}`} /> — computed, never inferred.
        Profiled by {result.profiled_by}.
      </p>

      {result.refusal ? (
        <div className="card">
          <strong>This file could not be read</strong>
          <p>{result.refusal.explanation}</p>
          <p className="note">
            <strong>Ask the payer:</strong> {result.refusal.ask_the_payer}
          </p>
        </div>
      ) : null}

      {result.blockers.length > 0 ? (
        <div className="card">
          <strong>The pipeline will refuse this file</strong>
          <ul>
            {result.blockers.map((f) => (
              <li key={f.quirk}>
                {f.detail}
                {f.first_lines.length > 0 ? ` (first at line ${f.first_lines.join(", ")})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.readable ? (
        <>
          <div className="card">
            <strong>Structure</strong>
            <p className="note">
              {result.structure.file_format} · {result.structure.encoding}
              {result.structure.byte_order_mark ? ` (${result.structure.byte_order_mark} BOM)` : ""}
              {result.structure.delimiter
                ? ` · delimiter ${JSON.stringify(result.structure.delimiter)}`
                : ""}{" "}
              · {result.structure.column_count} columns · {result.structure.data_rows} rows
              {result.structure.sampled ? " (sampled — the file is larger)" : ""}
              {result.duplicate_rows > 0
                ? ` · ${result.duplicate_rows} duplicate row(s)`
                : ""}
            </p>
          </div>

          <h2>Columns</h2>
          <table>
            <thead>
              <tr>
                <th>Column</th>
                <th>Type the data determines</th>
                <th>Nulls</th>
                <th>Distinct</th>
                <th>Unique</th>
              </tr>
            </thead>
            <tbody>
              {result.columns.map((c) => (
                <tr key={c.name}>
                  <td>
                    <Link href={`?column=${encodeURIComponent(c.name)}`}>{c.name}</Link>
                  </td>
                  <td>
                    {c.narrowest_type ?? (
                      <span className="note">needs your input — more than one type fits</span>
                    )}
                  </td>
                  <td>{c.null_count}</td>
                  <td>
                    {c.distinct_count}
                    {c.distinct_is_exact ? "" : "+"}
                  </td>
                  <td>{c.is_unique === null ? "unknown" : c.is_unique ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {selected ? (
            <div className="card">
              <strong>{selected.name}</strong>
              <p className="note">
                {selected.type_candidates
                  .map((t) => `${t.type} ${t.matched}/${t.considered}`)
                  .join(" · ")}
              </p>
              {selected.date_formats.length > 0 ? (
                <p className="note">
                  date formats seen:{" "}
                  {selected.date_formats.map((d) => `${d.label} (${d.matched})`).join(", ")}
                </p>
              ) : null}
              {selected.observed_precision !== null ? (
                <p className="note">
                  numeric precision {selected.observed_precision}, scale {selected.observed_scale}
                </p>
              ) : null}
              <p className="note">
                {selected.values_redacted
                  ? "Example values are hidden for your role."
                  : `examples: ${selected.examples.join(" · ")}`}
              </p>
              <CitationChip citationId={selected.citation_id} />
            </div>
          ) : null}

          <h2>Candidate keys</h2>
          <table>
            <thead>
              <tr>
                <th>Columns</th>
                <th>Distinct</th>
                <th>Populated</th>
                <th>Nulls</th>
                <th>Repeats</th>
                <th>Usable as a key</th>
              </tr>
            </thead>
            <tbody>
              {result.key_candidates.map((k) => (
                <tr key={k.columns.join("+")}>
                  <td>{k.columns.join(" + ")}</td>
                  <td>{k.distinct_count}</td>
                  <td>{k.populated_rows}</td>
                  <td>{k.null_rows}</td>
                  <td>{k.duplicate_values}</td>
                  <td>{k.is_unique ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.key_search.note ? <p className="note">{result.key_search.note}</p> : null}

          {result.findings.length > 0 ? (
            <>
              <h2>What the profiler noticed</h2>
              <ul>
                {result.findings.map((f) => (
                  <li key={f.quirk}>{f.detail}</li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </>
  );
}
