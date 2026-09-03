import { Skeleton, SkeletonScreen, SkeletonTable } from "@/components/ui/Skeleton";

/** The register behind the modal, so the background does not flash empty
 *  while the picker options are fetched from existing uploads. */
export default function Loading() {
  return (
    <SkeletonScreen label="Loading the ingestion form">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Skeleton width={220} height={30} />
        <Skeleton width={140} height={30} />
      </div>
      <SkeletonTable columns={6} rows={6} />
    </SkeletonScreen>
  );
}
