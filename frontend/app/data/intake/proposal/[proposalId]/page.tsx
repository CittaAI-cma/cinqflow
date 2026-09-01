import { redirect } from "next/navigation";

/**
 * W1-31 (CF-V1-E6-03) — superseded by the write-capable screen at
 * `/data/intake/proposals/[proposalId]` (plural, matching the queue it is
 * reached from). Kept as a redirect rather than deleted so a bookmarked or
 * previously-shared link to this singular path still arrives somewhere real:
 * this route drew the proposal read-only, and the plural one draws
 * everything this one did plus the accept/reject/lifecycle actions that were
 * always the point of a REVIEW screen. One proposal detail view, not two
 * that could drift.
 */
export default async function LegacyProposalPage({
  params,
}: {
  params: Promise<{ proposalId: string }>;
}) {
  const { proposalId } = await params;
  redirect(`/data/intake/proposals/${encodeURIComponent(proposalId)}`);
}
