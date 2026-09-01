import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Principal } from "@/lib/types";
import { AgentActions } from "./AgentActions";

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
// CF-V3-E5-05 — nested NDJSON/FHIR/HL7-derived JSON and fixed-width files.
type StructurePathStat = {
  path: string;
  documents_with_path: number;
  documents_total: number;
  fill_rate: number;
  is_array: boolean;
  array_length_min: number | null;
  array_length_max: number | null;
  array_length_avg: number | null;
};
type FlattenProposal = {
  source_path: string;
  proposed_entity: string;
  element_count_min: number;
  element_count_max: number;
  description: string;
};
type FixedWidthColumn = { start: number; end: number; name: string | null; confidence: number };
type FixedWidthLayout = { source: string; columns: FixedWidthColumn[] };
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
  structure_paths: StructurePathStat[];
  flatten_proposals: FlattenProposal[];
  fixed_width_layout: FixedWidthLayout | null;
};

/**
 * The flat, dotted `StructurePath` list (`item`, `item.adjudication`,
 * `item.adjudication.category`) as an explorable tree — the shape CF-V3-
 * E5-05's own acceptance criterion asks for: "counts at every path."
 */
type TreeNode = {
  label: string;
  path: string;
  stat: StructurePathStat | null;
  children: TreeNode[];
};

function buildStructureTree(paths: StructurePathStat[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const index = new Map<string, TreeNode>();

  for (const stat of [...paths].sort((a, b) => a.path.localeCompare(b.path))) {
    const segments = stat.path.split(".");
    let siblings = roots;
    let prefix = "";
    for (let depth = 0; depth < segments.length; depth++) {
      prefix = prefix ? `${prefix}.${segments[depth]}` : segments[depth];
      let node = index.get(prefix);
      if (!node) {
        node = { label: segments[depth], path: prefix, stat: null, children: [] };
        index.set(prefix, node);
        siblings.push(node);
      }
      if (depth === segments.length - 1) {
        node.stat = stat;
      }
      siblings = node.children;
    }
  }
  return roots;
}

function StructureTreeItem({ node }: { node: TreeNode }) {
  const stat = node.stat;
  const fillPct = stat ? Math.round(stat.fill_rate * 100) : null;
  const summary = stat
    ? `${node.label} — ${stat.documents_with_path}/${stat.documents_total} document(s) (${fillPct}%)` +
      (stat.is_array
        ? ` · repeats ${stat.array_length_min}-${stat.array_length_max} per parent`
        : "")
    : node.label;

  if (node.children.length === 0) {
    return <li>{summary}</li>;
  }
  return (
    <li>
      <details open>
        <summary>{summary}</summary>
        <ul>
          {node.children.map((child) => (
            <StructureTreeItem key={child.path} node={child} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export default async function ProfilePage({
  params,
  searchParams,
}: {
  params: Promise<{ profileId: string }>;
  searchParams: Promise<{ column?: string; refused?: string }>;
}) {
  const { profileId } = await params;
  const { column, refused } = await searchParams;
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
  // CF-V1-E5-02/03 · E6-02. Who may ask an agent to interpret this profile —
  // read from the caller's own permitted actions rather than from their role,
  // so the button and the route agree by construction.
  const me = await attempt<Principal>("/api/me");
  const mayEdit = !isRefused(me) && me.permitted_actions.includes("edit_feed");

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

      {/* CF-V1-E5-02 · E5-03 · E6-02. The four AI capabilities were routed,
          fitted and tested, and no page in this application called any of
          them — the intelligence plane was reachable only by `curl`. This is
          the door, placed where the evidence is: an agent that interprets a
          profile belongs beside the profile it interprets. */}
      <AgentActions
        feedId={result.feed_id}
        profileId={result.profile_id}
        mayEdit={mayEdit}
        refused={refused}
      />

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

          {result.structure_paths.length > 0 ? (
            <>
              <h2>Structure tree</h2>
              <p className="note">
                Every path the sample carries, with how many documents populate it — click a
                branch to collapse it.
              </p>
              <ul className="tree">
                {buildStructureTree(result.structure_paths).map((node) => (
                  <StructureTreeItem key={node.path} node={node} />
                ))}
              </ul>
            </>
          ) : null}

          {result.flatten_proposals.length > 0 ? (
            <>
              <h2>Proposed flattening</h2>
              <p className="note">
                Nothing here has been applied — review and hand these to the mapping studio.
              </p>
              <ul>
                {result.flatten_proposals.map((p) => (
                  <li key={p.source_path}>{p.description}</li>
                ))}
              </ul>
            </>
          ) : null}

          {result.fixed_width_layout ? (
            <>
              <h2>Fixed-width layout ({result.fixed_width_layout.source})</h2>
              <table>
                <thead>
                  <tr>
                    <th>Start</th>
                    <th>End</th>
                    <th>Width</th>
                    <th>Name</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {result.fixed_width_layout.columns.map((c) => (
                    <tr key={`${c.start}-${c.end}`}>
                      <td>{c.start}</td>
                      <td>{c.end}</td>
                      <td>{c.end - c.start + 1}</td>
                      <td>{c.name ?? <span className="note">unnamed</span>}</td>
                      <td>{Math.round(c.confidence * 100)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}

          {result.columns.length > 0 ? (
            <>
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
            </>
          ) : null}

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

          {result.key_candidates.length > 0 ? (
            <>
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
            </>
          ) : null}
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
