import { Skeleton, SkeletonCard, SkeletonText } from "@/components/ui/Skeleton";

export default function Loading() {
  return (
    <div className="review-layout" aria-busy="true">
      <div className="card verdict-card">
        <Skeleton height={10} width={70} style={{ marginBottom: 12 }} />
        <Skeleton height={22} width="70%" style={{ marginBottom: 10 }} />
        <SkeletonText lines={3} width={["100%", "92%", "60%"]} />
      </div>

      <div className="review-right">
        <div className="card">
          <Skeleton height={10} width={100} style={{ marginBottom: 14 }} />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="claim">
              <Skeleton height={11} width={120} style={{ marginBottom: 8 }} />
              <Skeleton height={16} width="55%" />
            </div>
          ))}
        </div>
        <SkeletonCard lines={2} />
      </div>
    </div>
  );
}
