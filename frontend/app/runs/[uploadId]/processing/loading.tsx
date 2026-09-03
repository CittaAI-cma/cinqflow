import { Skeleton } from "@/components/ui/Skeleton";

export default function Loading() {
  return (
    <div aria-busy="true">
      <Skeleton width={200} height={16} style={{ margin: "32px 0 10px" }} />
      <div className="kpi">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i}>
            <Skeleton height={18} width={i === 3 ? "80%" : 34} style={{ marginBottom: 6 }} />
            <Skeleton height={9} width="60%" />
          </div>
        ))}
      </div>

      <div className="card run-timeline" style={{ marginTop: 14 }}>
        <Skeleton height={9} width={80} style={{ marginBottom: 12 }} />
        <div className="skeleton-lines">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={13} width={`${70 - i * 12}%`} />
          ))}
        </div>
      </div>
    </div>
  );
}
