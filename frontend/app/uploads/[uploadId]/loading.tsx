import { Skeleton, SkeletonScreen } from "@/components/ui/Skeleton";

/** This route only resolves which run step an upload belongs on and redirects.
 *  It still needs a frame: the lookup is a network call, and without this the
 *  user watches an empty page during it with no sign anything is happening. */
export default function Loading() {
  return (
    <SkeletonScreen label="Opening this ingestion">
      <Skeleton width={240} height={22} style={{ margin: "16px 0 8px" }} />
      <Skeleton width={340} height={13} />
    </SkeletonScreen>
  );
}
