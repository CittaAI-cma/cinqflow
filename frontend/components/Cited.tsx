import Link from "next/link";
import { route } from "@/lib/citations";

/**
 * A figure and where it came from.
 *
 * Every number rendered anywhere passes through here. That is the mechanical
 * form of "uncited claims are a defect class": a value with no citation renders
 * marked, so an uncited figure is visible in review rather than plausible.
 */
export function Cited({
  value,
  citationId,
  title,
}: {
  value: React.ReactNode;
  citationId?: string | null;
  title?: string;
}) {
  const href = citationId ? route(citationId) : null;
  if (!href) {
    return (
      <span className="uncited" title="no resolvable citation">
        {value}
      </span>
    );
  }
  return (
    <Link className="cited" href={href} title={title ?? citationId ?? undefined}>
      {value}
    </Link>
  );
}

/** The citation itself, as a chip you can click and read aloud. */
export function CitationChip({ citationId }: { citationId: string }) {
  const href = route(citationId);
  if (!href) return <span className="chip uncited">{citationId}</span>;
  return (
    <Link className="chip" href={href}>
      {citationId}
    </Link>
  );
}
