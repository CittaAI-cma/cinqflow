import { asPanel, BatchPanels } from "@/components/BatchPanels";
import { Drawer } from "@/components/ui/Drawer";

/**
 * The intercepted drawer: clicking a run overlays it, and the list you were
 * reading stays behind it. The URL is unchanged — still the batch's
 * citation_id — so "look at recon:8842#DQ-002" is still a link somebody else
 * can open, which is the daily_status.xlsx habit this platform exists to
 * retire.
 */
export default async function BatchDrawerSlot({
  params,
  searchParams,
}: {
  params: Promise<{ batchId: string }>;
  searchParams: Promise<{ panel?: string; drop?: string; outcome?: string; headline?: string }>;
}) {
  const { batchId } = await params;
  const { panel, drop, outcome, headline } = await searchParams;

  return (
    <Drawer title={`Batch ${batchId}`}>
      <BatchPanels
        batchId={batchId}
        panel={asPanel(panel)}
        drop={drop}
        outcome={outcome}
        headline={headline}
      />
    </Drawer>
  );
}
