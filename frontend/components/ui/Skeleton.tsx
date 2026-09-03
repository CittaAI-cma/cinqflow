/** Shimmer block for loading states. Server-safe (no hooks) so `loading.tsx`
 *  files can render it directly while the real server component fetches. */
export function Skeleton({
  width,
  height = 14,
  radius = 4,
  className,
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className={`skeleton${className ? ` ${className}` : ""}`}
      style={{ width, height, borderRadius: radius, ...style }}
      aria-hidden="true"
    />
  );
}

/** A stack of shimmer lines, one per entry in `width` (or `lines` copies of
 *  the same width). Use for paragraph/label-shaped placeholders. */
export function SkeletonText({
  lines = 1,
  width = "100%",
}: {
  lines?: number;
  width?: string | string[];
}) {
  const widths = Array.isArray(width) ? width : Array.from({ length: lines }, () => width);
  return (
    <div className="skeleton-lines">
      {widths.map((w, i) => (
        <Skeleton key={i} height={12} width={w} />
      ))}
    </div>
  );
}

/** A `.card`-shaped placeholder: a label-sized line, then a few body lines. */
export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card">
      <Skeleton height={10} width={90} style={{ marginBottom: 12 }} />
      <SkeletonText lines={lines} width={["100%", "88%", "62%"].slice(0, lines)} />
    </div>
  );
}

/** A `.dt`-shaped placeholder table, so a loading list keeps its column
 *  rhythm instead of collapsing to a spinner. */
export function SkeletonTable({ columns = 6, rows = 8 }: { columns?: number; rows?: number }) {
  return (
    <div className="dt-wrap">
      <table className="dt dt-ruled">
        <thead>
          <tr>
            {Array.from({ length: columns }).map((_, c) => (
              <th key={c}>
                <Skeleton height={9} width="55%" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, r) => (
            <tr key={r}>
              {Array.from({ length: columns }).map((_, c) => (
                <td key={c}>
                  <Skeleton height={12} width={c === 0 ? "82%" : "48%"} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
