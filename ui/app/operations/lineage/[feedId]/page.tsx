import Link from "next/link";
import { ImpactPacketCard } from "@/components/ImpactPacket";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { ImpactPacket } from "@/lib/types";

/**
 * One feed's lineage — `/api/objects/feed/{id}/packet`, rooted at the FEED
 * rather than at any one of its governed pieces, so "what does this feed
 * reach" answers with the contract, the mapping, the rules and everything
 * beyond them in one traversal, not one screen per object type.
 */
export default async function FeedLineagePage({
  params,
}: {
  params: Promise<{ feedId: string }>;
}) {
  const { feedId } = await params;
  const packet = await attempt<ImpactPacket>(
    `/api/objects/feed/${encodeURIComponent(feedId)}/packet`,
  );

  return (
    <>
      <p className="note">
        <Link href="/operations/lineage">Data Lineage</Link> / {feedId}
      </p>
      <h1>{feedId} — lineage</h1>

      {isRefused(packet) ? (
        <RefusalNotice refusal={packet} />
      ) : (
        <ImpactPacketCard packet={packet} />
      )}
    </>
  );
}
