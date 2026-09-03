/** The four entry points on the home page. Each one either routes to a surface
 *  this build ships, or states plainly that it does not exist yet — the panel
 *  never pretends to a capability the platform has not got. */

export type ActionIcon = "pipeline" | "platform" | "upload" | "sparkles";

export interface PlatformAction {
  id: string;
  label: string;
  icon: ActionIcon;
  /** One line under the heading in the panel. */
  blurb: string;
  links: { label: string; href: string; note: string }[];
  /** Present when nothing behind this action is available yet. */
  unavailable?: string;
}

export const PLATFORM_ACTIONS: PlatformAction[] = [
  {
    id: "build-data-pipeline",
    label: "Build data pipeline",
    icon: "pipeline",
    blurb: "Shape how a source feed lands, maps and promotes through the medallion layers.",
    links: [
      {
        label: "Ingestion",
        href: "/data/intake",
        note: "A pipeline starts from a file. Upload one, approve G1, then shape its mapping.",
      },
    ],
  },
  {
    id: "manage-platform",
    label: "Manage platform",
    icon: "platform",
    blurb: "Connections, warehouses, credentials and platform-level settings.",
    links: [],
    unavailable:
      "Platform administration is not part of this build. The control plane runs from the API and CLI for now.",
  },
  {
    id: "onboard-data",
    label: "Onboard data",
    icon: "upload",
    blurb: "Bring a CSV or XLSX in, profile it deterministically, and interpret it before anything is written.",
    links: [
      {
        label: "Ingestion",
        href: "/data/intake",
        note: "Upload a source file. The original is preserved and profiling runs in a worker.",
      },
    ],
  },
  {
    id: "build-domains-glossary",
    label: "Build domains & glossary",
    icon: "sparkles",
    blurb: "Author the canonical model, business domains and governed vocabulary.",
    links: [],
    unavailable:
      "The canonical model and glossary are read-only knowledge files on this build — there is no authoring surface yet.",
  },
];
