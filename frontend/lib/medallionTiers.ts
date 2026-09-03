/** The medallion lifecycle for an ingest group.
 *
 *  Two of these tiers are real on this build: `landing_bronze` is the
 *  `batch.land_bronze` run minted at G1, and `bronze_silverraw` is the
 *  `mapping.promote` run written at G2. Anything beyond them is `planned` —
 *  the tier list posts with the form, but the control plane does not consume
 *  it yet, so a planned tier configures nothing today. */

export type PipelineMode = "auto" | "static";

export type TierBacking = "live" | "planned";

export interface MedallionTier {
  /** Stable identifier used as the React key and in the posted payload. */
  id: string;
  label: string;
  /** The stage key the control plane would use. */
  key: string;
  enabled: boolean;
  pipelineMode: PipelineMode;
  /** Set when pipelineMode is "static". */
  pipelineTemplateId: string | null;
  backing: TierBacking;
  /** Custom tiers are analyst-added and removable. */
  custom: boolean;
}

export const PIPELINE_MODES: { value: PipelineMode; label: string }[] = [
  { value: "auto", label: "Auto (generate later)" },
  { value: "static", label: "Static pipeline (bind now)" },
];

export function defaultTiers(): MedallionTier[] {
  return [
    {
      id: "landing_bronze",
      label: "Landing → Bronze",
      key: "landing_bronze",
      enabled: true,
      pipelineMode: "auto",
      pipelineTemplateId: null,
      backing: "live",
      custom: false,
    },
    {
      id: "bronze_silverraw",
      label: "Bronze → Silver Raw",
      key: "bronze_silverraw",
      enabled: true,
      pipelineMode: "auto",
      pipelineTemplateId: null,
      backing: "live",
      custom: false,
    },
    {
      id: "silverraw_silverods",
      label: "Silver Raw → Silver ODS",
      key: "silverraw_silverods",
      enabled: true,
      pipelineMode: "auto",
      pipelineTemplateId: null,
      backing: "planned",
      custom: false,
    },
  ];
}

/** Derives a stage key from a free-text tier label: "Silver ODS → Gold" →
 *  "silverods_gold". Falls back to a counter when the label yields nothing. */
export function tierKeyFrom(label: string, fallbackIndex: number): string {
  const parts = label
    .split(/→|->|,|\//)
    .map((part) => part.trim().toLowerCase().replace(/[^a-z0-9]+/g, ""))
    .filter(Boolean);
  if (parts.length >= 2) return `${parts[0]}_${parts[1]}`;
  if (parts.length === 1) return parts[0];
  return `custom_tier_${fallbackIndex}`;
}
