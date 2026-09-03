"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChevronRight,
  PipelineIcon,
  SettingsIcon,
  ShieldIcon,
  SitemapIcon,
} from "@/components/icons";
import { GROUP_STAGES, groupStageHref, type GroupStageIcon } from "@/lib/navigation";

const STAGE_ICONS: Record<GroupStageIcon, typeof SettingsIcon> = {
  config: SettingsIcon,
  domain: SitemapIcon,
  schedule: ShieldIcon,
  publish: PipelineIcon,
};

/** The group's stages as a stepper. A stage with no surface on this build
 *  carries its reason rather than linking nowhere. */
export default function GroupStageTabs({ group }: { group: string }) {
  const pathname = usePathname();

  return (
    <nav className="stage-tabs" aria-label="Ingest group stages">
      {GROUP_STAGES.map((stage, index) => {
        const Icon = STAGE_ICONS[stage.icon];
        const href = groupStageHref(stage, group);
        const active = href ? pathname === href : false;

        return (
          <span key={stage.id} className="stage-tab-group">
            {index > 0 ? <ChevronRight size={14} className="stage-sep" /> : null}
            {href ? (
              <Link
                href={href}
                className={`stage-tab${active ? " active" : ""}`}
                aria-current={active ? "step" : undefined}
              >
                <Icon size={16} />
                {stage.label}
              </Link>
            ) : (
              <span className="stage-tab disabled" aria-disabled="true" title={stage.reason}>
                <Icon size={16} />
                {stage.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
