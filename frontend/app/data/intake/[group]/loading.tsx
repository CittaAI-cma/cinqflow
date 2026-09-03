import { Skeleton, SkeletonScreen, SkeletonTable } from "@/components/ui/Skeleton";

export default function Loading() {
  return (
    <SkeletonScreen label="Loading ingest group">
      <Skeleton width={280} height={26} style={{ margin: "12px 0 6px" }} />
      <Skeleton width={180} height={13} style={{ marginBottom: 18 }} />

      {/* The stage tabs, which anchor the rest of the page. */}
      <div className="row" style={{ gap: 10, marginBottom: 16 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} width={92} height={22} />
        ))}
      </div>

      <SkeletonTable columns={6} rows={6} />
    </SkeletonScreen>
  );
}
