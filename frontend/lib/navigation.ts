import type { RailIconName } from "@/components/icons";
import { RUN_STEPS } from "@/lib/runStep";

/** The sidebar is data, not markup. An item without an `href` is a surface this
 *  build does not ship yet: it renders as unavailable with a reason rather than
 *  as a link into a 404. */

export interface NavItem {
  label: string;
  /** Omitted while the surface is not implemented. */
  href?: string;
  /** Shown on hover/focus when there is no href. */
  reason?: string;
}

export interface NavSectionSpec {
  id: string;
  label: string;
  /** Glyph the collapsed rail shows for this section. */
  icon: RailIconName;
  items: NavItem[];
  defaultOpen?: boolean;
}

const NOT_BUILT = "Not part of this build yet";

export const HOME_ITEM: NavItem = { label: "Home", href: "/" };

export const NAV_SECTIONS: NavSectionSpec[] = [
  {
    id: "data-gov",
    label: "Data Gov",
    icon: "analytics",
    defaultOpen: true,
    items: [
      { label: "Data Catalog", reason: NOT_BUILT },
      { label: "Schema Drift", reason: NOT_BUILT },
      { label: "Domain", reason: NOT_BUILT },
      { label: "Glossary", reason: NOT_BUILT },
      { label: "Agent Studio", reason: NOT_BUILT },
      { label: "Catalog Audit", reason: NOT_BUILT },
      { label: "Lineage", reason: "Lineage opens from a batch — no cross-feed view yet" },
    ],
  },
  {
    id: "pipeline",
    label: "Pipeline",
    icon: "pipeline",
    defaultOpen: true,
    items: [
      { label: "Data Pipeline", reason: NOT_BUILT },
      { label: "Data Flow", reason: NOT_BUILT },
      { label: "Ingestion", href: "/data/intake" },
      { label: "Knowledge Base", reason: NOT_BUILT },
      { label: "Orchestration Workflows", reason: NOT_BUILT },
    ],
  },
  {
    id: "dataops",
    label: "DataOps",
    icon: "grid",
    defaultOpen: true,
    items: [
      { label: "Ops Explore", reason: NOT_BUILT },
      { label: "Ops Hub", reason: NOT_BUILT },
      { label: "Control Operations", reason: NOT_BUILT },
      { label: "Recovery Library", reason: NOT_BUILT },
      { label: "Ops Incidents", reason: NOT_BUILT },
      { label: "LLM Observability", reason: NOT_BUILT },
      { label: "Cost Optimization", reason: NOT_BUILT },
    ],
  },
];

export const FOOTER_ITEMS: NavItem[] = [{ label: "All Chats", reason: NOT_BUILT }];

/** Product areas in the top bar. None of these ship on this build, so each one
 *  renders with its reason rather than as a link into a 404. */
export type TopNavIcon = "catalog" | "design" | "opshub" | "explore" | "observability";

export const TOP_NAV: (NavItem & { icon: TopNavIcon })[] = [
  { label: "Catalog", icon: "catalog", reason: NOT_BUILT },
  { label: "Design", icon: "design", reason: NOT_BUILT },
  { label: "OpsHub", icon: "opshub", reason: NOT_BUILT },
  { label: "Explore", icon: "explore", reason: NOT_BUILT },
  { label: "LLM Observability", icon: "observability", reason: NOT_BUILT },
];

/** The stages of an ingest group, shown as the stepper on its detail page.
 *  `href` is a template: `:group` is replaced with the encoded group name. */
export type GroupStageIcon = "config" | "domain" | "schedule" | "publish";

export interface GroupStage {
  id: string;
  label: string;
  icon: GroupStageIcon;
  hrefTemplate?: string;
  reason?: string;
}

export const GROUP_STAGES: GroupStage[] = [
  {
    id: "configuration",
    label: "Configuration",
    icon: "config",
    hrefTemplate: "/data/intake/:group",
  },
  {
    id: "map-to-domain",
    label: "Map to domain",
    icon: "domain",
    hrefTemplate: "/mapping/:group",
  },
  {
    id: "schedule",
    label: "Schedule & Monitoring",
    icon: "schedule",
    reason: "No scheduler on this build — work is queued per upload",
  },
  {
    id: "publish",
    label: "Publish",
    icon: "publish",
    reason: "Promotion to Silver happens at the G2 gate on Map to domain",
  },
];

export function groupStageHref(stage: GroupStage, group: string): string | undefined {
  return stage.hrefTemplate?.replace(":group", encodeURIComponent(group));
}

/** Bottom-of-rail utilities. Only the theme one does anything on this build. */
export const RAIL_UTILITIES = {
  settings: { label: "Settings", reason: NOT_BUILT },
  logout: { label: "Sign out", reason: "No auth on this build" },
} as const;

// ------------------------------------------------------------------ breadcrumbs

export interface Crumb {
  label: string;
  href?: string;
}

const INGESTION: Crumb = { label: "Ingestion", href: "/data/intake" };
const PIPELINE: Crumb = { label: "Pipeline" };

/** Everything downstream of an upload lives under Pipeline › Ingestion, which is
 *  where the analyst actually came from. Dynamic ids are shortened, not hidden. */
export function breadcrumbsFor(pathname: string): Crumb[] {
  if (pathname === "/") return [];

  if (pathname === "/data/intake") return [PIPELINE, INGESTION];
  if (pathname === "/data/intake/new") {
    return [PIPELINE, INGESTION, { label: "Add Ingestion" }];
  }

  // An ingest group: /data/intake/<group>. Checked after the static /new route.
  const group = pathname.match(/^\/data\/intake\/([^/]+)/);
  if (group) {
    return [PIPELINE, INGESTION, { label: decodeURIComponent(group[1]) }];
  }

  const upload = pathname.match(/^\/uploads\/([^/]+)/);
  if (upload) {
    return [PIPELINE, INGESTION, { label: `Upload ${upload[1].slice(0, 8)}…` }];
  }

  // A run: /runs/<uploadId>/<step>. The step crumb names the screen; the run
  // shell itself (app/runs/[uploadId]/layout.tsx) renders the filename/feed
  // detail this pure, pathname-only function has no data to fetch for.
  const run = pathname.match(/^\/runs\/([^/]+)(?:\/([^/]+))?/);
  if (run) {
    const [, uploadId, step] = run;
    const stepDef = RUN_STEPS.find((s) => s.key === step);
    return [
      PIPELINE,
      INGESTION,
      { label: `Run ${uploadId.slice(0, 8)}…`, href: `/uploads/${uploadId}` },
      ...(stepDef ? [{ label: stepDef.label }] : []),
    ];
  }

  const batch = pathname.match(/^\/batches\/([^/]+)/);
  if (batch) {
    return [PIPELINE, INGESTION, { label: `Batch ${batch[1].slice(0, 8)}…` }];
  }

  const mapping = pathname.match(/^\/mapping\/([^/]+)/);
  if (mapping) {
    return [PIPELINE, INGESTION, { label: `Mapping ${decodeURIComponent(mapping[1])}` }];
  }

  return [];
}

/** The rail marks a section active when the current route belongs to it. */
export function activeSectionId(pathname: string): string | null {
  for (const section of NAV_SECTIONS) {
    for (const item of section.items) {
      if (item.href && (pathname === item.href || pathname.startsWith(`${item.href}/`))) {
        return section.id;
      }
    }
  }
  // Upload, run, batch and mapping detail routes all hang off Ingestion.
  if (/^\/(uploads|runs|batches|mapping)\//.test(pathname)) return "pipeline";
  return null;
}
