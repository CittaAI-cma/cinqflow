/** Inline icon set — no icon dependency. Every glyph inherits `currentColor`
 *  and sizes from the `size` prop so it can sit in text or in a chip. */

interface IconProps {
  size?: number;
  className?: string;
}

function svgProps({ size = 16, className }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    className,
  };
}

export function BrandMark({ size = 22, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={className}
    >
      <rect x="1.5" y="1.5" width="21" height="21" rx="6" fill="var(--brand-mark-bg)" />
      <path d="M12 4.5a7.5 7.5 0 0 0 0 15Z" fill="var(--brand-mark-a)" />
      <path d="M12 4.5a7.5 7.5 0 0 1 0 15Z" fill="var(--brand-mark-b)" />
      <circle cx="12" cy="12" r="2.4" fill="var(--brand-mark-bg)" />
    </svg>
  );
}

export function ChevronDown(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function ChevronLeft(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

export function PipelineIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="12" r="2.5" />
      <path d="M6 8.5v7M8.5 6h4a3 3 0 0 1 3 3v.8M8.5 18h4a3 3 0 0 0 3-3v-.8" />
    </svg>
  );
}

export function DatabaseIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <ellipse cx="12" cy="6" rx="7.5" ry="3" />
      <path d="M4.5 6v12c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V6" />
      <path d="M4.5 12c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3" />
    </svg>
  );
}

export function UploadIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M12 15.5V4m0 0L7.5 8.5M12 4l4.5 4.5" />
      <path d="M4 16v2.5A2.5 2.5 0 0 0 6.5 21h11A2.5 2.5 0 0 0 20 18.5V16" />
    </svg>
  );
}

export function SparklesIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M9 3.5l1.4 3.6L14 8.5l-3.6 1.4L9 13.5l-1.4-3.6L4 8.5l3.6-1.4z" />
      <path d="M17.5 13.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" />
    </svg>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.5 1.5m11.2 11.2 1.5 1.5M19.1 4.9l-1.5 1.5M6.4 17.6l-1.5 1.5" />
    </svg>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5Z" />
    </svg>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function ChatIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M20 12a7 7 0 0 1-7 7H8.5L4 21.5V12a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7Z" />
    </svg>
  );
}

export function HomeIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19Z" />
    </svg>
  );
}

export function AnalyticsIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 3.5v8.5h8.5" />
    </svg>
  );
}

export function GridIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </svg>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 3 15H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 10 4V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 21 10h.1a2 2 0 1 1 0 4H21Z" />
    </svg>
  );
}

export function PaletteIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M12 21a9 9 0 1 1 9-9c0 2.2-1.8 3.5-4 3.5h-1.2a1.8 1.8 0 0 0-1.3 3.05A1.8 1.8 0 0 1 12 21Z" />
      <circle cx="8" cy="10" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="7.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="16" cy="10" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function LogoutIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M15 4.5h2.5A1.5 1.5 0 0 1 19 6v12a1.5 1.5 0 0 1-1.5 1.5H15" />
      <path d="M11 8.5 7.5 12l3.5 3.5M7.5 12H16" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.5 15.5 4 4" />
    </svg>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M19 12a7 7 0 1 1-2.1-5" />
      <path d="M19.5 4.5V9H15" />
    </svg>
  );
}

export function DownloadIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M12 4v11.5m0 0L7.5 11M12 15.5 16.5 11" />
      <path d="M4.5 18.5V19A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-.5" />
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function EditIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M9.5 4.5H6A1.5 1.5 0 0 0 4.5 6v12A1.5 1.5 0 0 0 6 19.5h12a1.5 1.5 0 0 0 1.5-1.5v-3.5" />
      <path d="M18 3.5 20.5 6 13 13.5l-3 .5.5-3Z" />
    </svg>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M4.5 7h15M9.5 7V5.5A1 1 0 0 1 10.5 4.5h3a1 1 0 0 1 1 1V7" />
      <path d="M6.5 7l.7 12a1.5 1.5 0 0 0 1.5 1.4h6.6a1.5 1.5 0 0 0 1.5-1.4L17.5 7" />
      <path d="M10.5 11v6M13.5 11v6" />
    </svg>
  );
}

export function ChevronUp(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m6 15 6-6 6 6" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)} strokeWidth={2.6}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </svg>
  );
}

export function GlobeIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.2 2.4 3.3 5.3 3.3 8.5S14.2 18.1 12 20.5c-2.2-2.4-3.3-5.3-3.3-8.5S9.8 5.9 12 3.5Z" />
    </svg>
  );
}

export function ChevronRight(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

export function ArrowRight(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M4.5 12h15m0 0-5-5m5 5-5 5" />
    </svg>
  );
}

export function PackageIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M20 8.5v7l-8 4.5-8-4.5v-7L12 4Z" />
      <path d="m4 8.5 8 4.5 8-4.5M12 13v7" />
    </svg>
  );
}

export function ActivityIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M3 12h3.5l2.5-6 3.5 12 2.5-6H21" />
    </svg>
  );
}

export function CompassIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15 9-2 4.5-4 2 2-4.5Z" />
    </svg>
  );
}

export function ChartIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M4 19V5m0 14h16" />
      <path d="m7.5 15 3.5-4 3 2.5 4.5-6" />
    </svg>
  );
}

export function DocumentIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8Z" />
      <path d="M14 3.5V8h4.5" />
    </svg>
  );
}

export function LayersIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m12 3.5 8.5 4.5L12 12.5 3.5 8Z" />
      <path d="m4 12 8 4.5 8-4.5M4 16l8 4.5 8-4.5" />
    </svg>
  );
}

export function SitemapIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <circle cx="12" cy="5" r="2" />
      <circle cx="6" cy="19" r="2" />
      <circle cx="18" cy="19" r="2" />
      <path d="M12 7v4m0 0H6v6m6-6h6v6" />
    </svg>
  );
}

export function DotsVerticalIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)} strokeWidth={2.2}>
      <circle cx="12" cy="5.5" r="0.6" fill="currentColor" />
      <circle cx="12" cy="12" r="0.6" fill="currentColor" />
      <circle cx="12" cy="18.5" r="0.6" fill="currentColor" />
    </svg>
  );
}

export function ShieldIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M12 3.5l7 2.5v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6Z" />
      <path d="m9 12 2.2 2.2L15.5 10" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** The double chevron the reference uses on picker-style controls. */
export function ChevronUpDown(props: IconProps) {
  return (
    <svg {...svgProps(props)}>
      <path d="m8 10 4-4 4 4M8 14l4 4 4-4" />
    </svg>
  );
}

/** Two-direction sort affordance; the active leg is filled by CSS. */
export function SortArrows({ size = 12, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={className}
      fill="currentColor"
    >
      <path className="sort-up" d="M6 1.5 8.6 5H3.4Z" />
      <path className="sort-down" d="M6 10.5 3.4 7h5.2Z" />
    </svg>
  );
}

export const ACTION_ICONS = {
  pipeline: PipelineIcon,
  platform: DatabaseIcon,
  upload: UploadIcon,
  sparkles: SparklesIcon,
} as const;

/** Glyphs the collapsed rail uses for each nav section. */
export const RAIL_ICONS = {
  home: HomeIcon,
  analytics: AnalyticsIcon,
  pipeline: PipelineIcon,
  grid: GridIcon,
  chat: ChatIcon,
} as const;

export type RailIconName = keyof typeof RAIL_ICONS;
