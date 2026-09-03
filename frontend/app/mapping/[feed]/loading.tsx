import { Skeleton, SkeletonCard, SkeletonScreen, SkeletonTable } from "@/components/ui/Skeleton";

/** The studio loads the version list, the spec, the diff against the previous
 *  version and the preview together. Its skeleton leads with the version chip
 *  row, because that is what tells an analyst which version they are about to
 *  be editing — the one thing worth reserving space for. */
export default function Loading() {
  return (
    <SkeletonScreen label="Loading mapping studio">
      <Skeleton width={240} height={26} style={{ margin: "12px 0 10px" }} />

      <div className="row" style={{ gap: 8 }}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} width={74} height={26} radius={999} />
        ))}
      </div>

      <div style={{ marginTop: 16 }}>
        <SkeletonCard lines={2} />
      </div>

      <Skeleton width={150} height={18} style={{ margin: "22px 0 10px" }} />
      <SkeletonTable columns={8} rows={7} />
    </SkeletonScreen>
  );
}
