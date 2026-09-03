import { Skeleton, SkeletonTable } from "@/components/ui/Skeleton";

export default function Loading() {
  return (
    <div className="grid" aria-busy="true">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Skeleton width={220} height={30} />
        <Skeleton width={140} height={30} />
      </div>
      <SkeletonTable columns={6} rows={8} />
    </div>
  );
}
