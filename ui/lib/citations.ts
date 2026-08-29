// The citation address space, mirrored for the browser.
//
// One resolver serves the agent's citations, the deep links, the breadcrumbs
// and the drawer — which is why "clicking a citation opens that registry row"
// needs no agent-specific plumbing. The server-side parser in
// core/citations/__init__.py is authoritative; this is the same grammar so a
// link can be built without a round trip.

const PATTERN = /^([a-z]+):([A-Za-z0-9][\w.-]*)(?:@v(\d+))?(?:#([A-Za-z0-9][\w.-]*))?$/;

export interface Citation {
  kind: string;
  subject: string;
  version?: number;
  fragment?: string;
  raw: string;
}

export function parse(raw: string): Citation | null {
  const match = PATTERN.exec(raw.trim());
  if (!match) return null;
  return {
    kind: match[1],
    subject: match[2],
    version: match[3] ? Number(match[3]) : undefined,
    fragment: match[4],
    raw: raw.trim(),
  };
}

/** A citation is a ROUTE. Depth is a drawer, so a fragment becomes a panel. */
export function route(raw: string): string | null {
  const citation = parse(raw);
  if (!citation) return null;
  const { kind, subject, version, fragment } = citation;
  const v = version ? `?version=${version}` : "";
  switch (kind) {
    case "feed":
    case "contract":
    case "mapping":
      return `/data/intake/${kind}/${subject}${v}`;
    case "plan":
      return `/data/intake/feed/${subject}/plan${v}`;
    case "batch":
      return `/operations/control/batch/${subject}${fragment ? `?panel=${fragment}` : ""}`;
    case "recon":
      return `/operations/control/batch/${subject}?panel=recon${
        fragment ? `&drop=${fragment}` : ""
      }`;
    case "error":
      return `/operations/control/error/${subject}`;
    case "file":
      return `/data/explorer/landing/${subject}`;
    case "rule":
      return `/data/intake/rule/${subject}`;
    case "term":
      return `/data/intake/glossary/${subject}`;
    default:
      // A citation kind the UI does not know is NOT rendered as a dead link.
      // A malformed citation is worse than no citation: it reads as evidence
      // and resolves to nothing.
      return null;
  }
}
