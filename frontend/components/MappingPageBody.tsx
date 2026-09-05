import Link from "next/link";
import ApproveMapping from "@/components/ApproveMapping";
import MappingStudio from "@/components/MappingStudio";
import ProposalTable from "@/components/ProposalTable";
import StartDraft from "@/components/StartDraft";
import PreviewPanel from "@/components/PreviewPanel";
import StatusWord from "@/components/StatusWord";
import {
  getFeedVersionProgress,
  getMappingDiff,
  getMappingVersion,
  getPreview,
  getProposalById,
  listMappingVersions,
} from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import { mappingStatusWord } from "@/lib/statusWords";

const PREVIEW_LIMITS = [10, 25, 50] as const;

/** The mapping studio's content, feed-parameterized so it renders identically
 *  wherever an analyst reaches it: the durable `/mapping/[feed]` surface and
 *  S5 (`/runs/[uploadId]/mapping`, which resolves `feed` from the upload and
 *  delegates here). One body, so the two moments can never drift apart -
 *  same reasoning as `ProposalTable` being shared between the batch page and
 *  this one's own empty state.
 *
 *  `baseHref` is the one thing each caller supplies its own value for - the
 *  durable page keeps `/mapping/{feed}`, the run flow keeps
 *  `/runs/{uploadId}/mapping`. The version chips built here derive from it and
 *  from the version this render actually resolved, never from the raw,
 *  possibly-absent `v` search param a caller was given. `PreviewPanel`'s
 *  row-limit chips get the same `baseHref` as a plain string, not a callback
 *  - it is itself a Client Component, and a Server Component (this one)
 *  cannot pass a function across that boundary. */
export default async function MappingPageBody({
  feed,
  v,
  proposal,
  limit: limitParam,
  baseHref,
}: {
  feed: string;
  v?: string;
  proposal?: string;
  limit?: string;
  baseHref: string;
}) {
  // Whether the gate below renders as a form or as a stated reason - the API
  // enforces `can_decide_gates` regardless (`require_capability`).
  const user = await getCurrentUser();
  const canDecide = user?.capabilities.can_decide_gates ?? false;
  const { versions } = await listMappingVersions(feed).catch(() => ({ versions: [] }));
  const selected = v ? Number(v) : versions[0]?.version;
  const previewLimit = PREVIEW_LIMITS.includes(Number(limitParam) as (typeof PREVIEW_LIMITS)[number])
    ? Number(limitParam)
    : 25;
  const mapping = selected ? await getMappingVersion(feed, selected) : null;
  const diff = selected ? await getMappingDiff(feed, selected) : null;
  const preview = selected ? await getPreview(feed, selected, previewLimit) : null;
  // The ledger's view of this version, for `PreviewPanel`'s `WorkflowSteps`.
  const progress = selected ? await getFeedVersionProgress(feed, selected).catch(() => null) : null;
  const analystEdited = new Set(diff?.diff.analyst_edited ?? []);
  // Only fetched for the empty state: once a draft exists it owns the fields,
  // and the proposal is history that `mapping.ai_context` already carries in.
  const seedProposal = !mapping && proposal ? await getProposalById(proposal) : null;

  return (
    <>
      {versions.length ? (
        <div className="chip-row">
          {versions.map((version) => (
            <Link
              key={version.version}
              href={`${baseHref}?v=${version.version}`}
              className={`chip${version.version === selected ? " on" : ""}`}
            >
              v{version.version} · {version.status}
            </Link>
          ))}
        </div>
      ) : null}

      {!mapping ? (
        <>
          <p className="empty">
            <StatusWord word="Expected" /> No mapping version for this feed yet. A draft starts
            from an AI proposal — nothing in it is authoritative until it is approved.
          </p>
          {seedProposal && seedProposal.feed !== feed ? (
            <p className="alert error">
              Proposal <span className="mono">{proposal}</span> belongs to feed{" "}
              <span className="mono">{seedProposal.feed}</span>, not <span className="mono">{feed}</span>.
              It cannot seed a draft here —{" "}
              <Link href={`/mapping/${encodeURIComponent(seedProposal.feed)}?proposal=${proposal}`}>
                open the correct feed
              </Link>
              .
            </p>
          ) : seedProposal ? (
            <>
              <h2>What "Start draft" will seed</h2>
              <ProposalTable proposal={seedProposal} />
            </>
          ) : proposal ? (
            <p className="alert error">
              Proposal <span className="mono">{proposal}</span> could not be loaded — it may
              belong to a different feed, or no longer exist. Paste a proposal id below, or open
              it from its batch page.
            </p>
          ) : null}
          <StartDraft feed={feed} proposalId={proposal} />
        </>
      ) : (
        <>
          <div className="card grid">
            <div className="row">
              <div>
                <label>Version</label>
                <span className="mono">v{mapping.version}</span>
              </div>
              <div>
                <label>Status</label>
                <StatusWord word={mappingStatusWord(mapping.status)} raw={mapping.status} />
              </div>
              <div>
                <label>Origin</label>
                <span className="mono">{mapping.origin}</span>
              </div>
              <div>
                <label>Derived from</label>
                <span className="mono">
                  {mapping.derived_from ? `v${mapping.derived_from}` : "—"}
                </span>
              </div>
              <div>
                <label>Fields</label>
                <span className="mono">{mapping.spec.fields.length}</span>
              </div>
              <div>
                <label>Editable</label>
                <span className="mono">{String(mapping.editable)}</span>
              </div>
            </div>
          </div>

          {diff && diff.against !== null ? (
            <div className="card" style={{ marginTop: 14 }}>
              <label>
                Compared with v{diff.against} ({diff.against_status})
              </label>
              <div className="row">
                <span className="meta">
                  added <span className="mono">{diff.diff.added.length}</span>
                </span>
                <span className="meta">
                  removed <span className="mono">{diff.diff.removed.length}</span>
                </span>
                <span className="meta">
                  changed <span className="mono">{diff.diff.changed.length}</span>
                </span>
                <span className="meta">
                  unchanged <span className="mono">{diff.diff.unchanged}</span>
                </span>
              </div>
              {diff.diff.changed.length ? (
                <ul className="plain" style={{ marginTop: 8 }}>
                  {diff.diff.changed.map((change) => (
                    <li key={change.source} className="mono small">
                      {change.source}:{" "}
                      {Object.entries(change.attributes)
                        .map(
                          ([attribute, move]) =>
                            `${attribute} ${JSON.stringify(move.from)} → ${JSON.stringify(move.to)}`,
                        )
                        .join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <div className="card" style={{ marginTop: 14 }}>
            <label>Ownership</label>
            <span className="meta">
              <span className="tag edited">analyst-edited</span>{" "}
              <span className="mono">{diff?.diff.analyst_edited.length ?? 0}</span> ·{" "}
              <span className="tag proposal">from proposal</span>{" "}
              <span className="mono">{diff?.diff.from_proposal.length ?? 0}</span>
              {diff?.diff.analyst_edited.length ? (
                <>
                  {" "}
                  — <span className="mono">{diff.diff.analyst_edited.join(", ")}</span>
                </>
              ) : null}
            </span>
          </div>

          <h2>{mapping.editable ? "Edit the draft" : "Frozen version"}</h2>

          {mapping.editable ? (
            <MappingStudio mapping={mapping} />
          ) : (
            <>
              <p className="empty">
                v{mapping.version} is {mapping.status} and cannot be edited. Start v
                {mapping.version + 1} from it to continue.
              </p>
              <StartDraft feed={feed} deriveFrom={mapping.version} />
              <div className="card scroll" style={{ padding: 0, marginTop: 14 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Target</th>
                      <th>Cast</th>
                      <th>Transform</th>
                      <th>Origin</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mapping.spec.fields.map((field) => (
                      <tr key={field.source}>
                        <td className="mono">{field.source}</td>
                        <td className="mono">{field.target}</td>
                        <td className="mono">{field.cast}</td>
                        <td className="mono">{field.transform?.op ?? "—"}</td>
                        <td>
                          <span className={`tag ${analystEdited.has(field.source) ? "edited" : "proposal"}`}>
                            {analystEdited.has(field.source) ? "analyst-edited" : "proposal"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <PreviewPanel
            feed={feed}
            version={mapping.version}
            preview={preview}
            limit={previewLimit}
            baseHref={baseHref}
            initialSteps={progress?.steps ?? []}
          />

          <ApproveMapping
            feed={feed}
            version={mapping.version}
            status={mapping.status}
            preview={preview}
            editedCount={diff?.diff.analyst_edited.length ?? 0}
            canDecide={canDecide}
          />
        </>
      )}
    </>
  );
}
