import Link from "next/link";
import { asPanel, BatchPanels } from "@/components/BatchPanels";

/**
 * The batch drawer, arrived at COLD — a pasted link, a bookmark, a citation in
 * somebody's message. Renders the full page with the breadcrumb back to the
 * list. The soft-navigation path is the intercepted overlay in
 * app/@drawer/(.)operations/…, which renders the same body.
 */
export default async function BatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ batchId: string }>;
  searchParams: Promise<{ panel?: string; drop?: string; outcome?: string; headline?: string }>;
}) {
  const { batchId } = await params;
  const { panel, drop, outcome, headline } = await searchParams;

  return (
    <>
      <p className="note">
        <Link href="/operations/control">Control Operations</Link> / batch {batchId}
      </p>
      <BatchPanels
        batchId={batchId}
        panel={asPanel(panel)}
        drop={drop}
        outcome={outcome}
        headline={headline}
      />
    </>
  );
}
