import { Skeleton, SkeletonCard, SkeletonScreen, SkeletonTable } from "@/components/ui/Skeleton";

/** Batch detail is the widest fetch in the app — lineage, Bronze rows, the
 *  Bronze profile, the proposal and quarantine, all in parallel. The skeleton
 *  keeps the page's real rhythm (counts strip, lineage band, then tables) so
 *  the layout does not jump when the data lands. */
export default function Loading() {
  return (
    <SkeletonScreen label="Loading batch">
      <Skeleton width={260} height={26} style={{ margin: "12px 0 4px" }} />

      <div className="kpi">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i}>
            <Skeleton height={22} width={54} style={{ marginBottom: 8 }} />
            <Skeleton height={9} width={86} />
          </div>
        ))}
      </div>

      <Skeleton width={110} height={18} style={{ margin: "20px 0 10px" }} />
      <SkeletonCard lines={2} />

      <Skeleton width={170} height={18} style={{ margin: "22px 0 10px" }} />
      <SkeletonTable columns={4} rows={5} />
    </SkeletonScreen>
  );
}
