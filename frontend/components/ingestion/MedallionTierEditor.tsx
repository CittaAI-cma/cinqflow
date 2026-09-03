"use client";

import { useState } from "react";
import Checkbox from "@/components/ui/Checkbox";
import { ChevronDown, ChevronUp, PlusIcon, TrashIcon } from "@/components/icons";
import { PIPELINE_TEMPLATES } from "@/lib/platformCatalog";
import {
  PIPELINE_MODES,
  defaultTiers,
  tierKeyFrom,
  type MedallionTier,
} from "@/lib/medallionTiers";

/** Toggle, order and bind the medallion tiers of an ingest group.
 *
 *  Order is explicit (top runs first) rather than drag-based, so it works from
 *  the keyboard and needs no pointer-event library. The whole list posts as
 *  JSON on a hidden input — see lib/medallionTiers.ts for what the control
 *  plane actually honours today. */
export default function MedallionTierEditor({ name }: { name: string }) {
  const [tiers, setTiers] = useState<MedallionTier[]>(defaultTiers);

  function update(id: string, patch: Partial<MedallionTier>) {
    setTiers((current) =>
      current.map((tier) => (tier.id === id ? { ...tier, ...patch } : tier)),
    );
  }

  function move(index: number, delta: number) {
    setTiers((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function addCustomTier() {
    setTiers((current) => {
      const ordinal = current.filter((tier) => tier.custom).length + 1;
      const label = `Custom tier ${ordinal}`;
      return [
        ...current,
        {
          id: `custom_${Date.now()}`,
          label,
          key: tierKeyFrom(label, ordinal),
          enabled: true,
          pipelineMode: "auto",
          pipelineTemplateId: null,
          backing: "planned",
          custom: true,
        },
      ];
    });
  }

  function removeTier(id: string) {
    setTiers((current) => current.filter((tier) => tier.id !== id));
  }

  const lastBuiltInIndex = tiers.reduce(
    (last, tier, index) => (tier.custom ? last : index),
    -1,
  );

  return (
    <>
      <input type="hidden" name={name} value={JSON.stringify(tiers)} />

      <h4 className="tier-heading">Medallion tiers</h4>
      <p className="tier-intro">
        Configure medallion tiers for this ingest group. Ingestion workflow stages are always
        created. Disable tiers you do not need, bind a static pipeline per tier, or use the arrows
        to set execution order (top runs first).
      </p>

      <ul className="tier-list">
        {tiers.map((tier, index) => (
          <li key={tier.id} className={`tier-card${tier.enabled ? "" : " off"}`}>
            <div className="tier-head">
              <Checkbox
                checked={tier.enabled}
                onChange={(enabled) => update(tier.id, { enabled })}
                label={`Enable ${tier.label}`}
              />
              <div className="tier-title">
                {tier.custom ? (
                  <input
                    className="tier-name-input"
                    value={tier.label}
                    aria-label="Tier name"
                    onChange={(event) =>
                      update(tier.id, {
                        label: event.target.value,
                        key: tierKeyFrom(event.target.value, index),
                      })
                    }
                  />
                ) : (
                  <b>{tier.label}</b>
                )}
                <span className="tier-key">{tier.key}</span>
              </div>

              <div className="tier-actions">
                {index === lastBuiltInIndex ? (
                  <button type="button" className="btn-outline tier-add" onClick={addCustomTier}>
                    <PlusIcon size={14} /> Add custom tier
                  </button>
                ) : null}
                {tier.custom ? (
                  <button
                    type="button"
                    className="icon-action danger"
                    onClick={() => removeTier(tier.id)}
                    title={`Remove ${tier.label}`}
                    aria-label={`Remove ${tier.label}`}
                  >
                    <TrashIcon size={15} />
                  </button>
                ) : null}
                <button
                  type="button"
                  className="tier-move"
                  onClick={() => move(index, -1)}
                  disabled={index === 0}
                  title="Run earlier"
                  aria-label={`Move ${tier.label} earlier`}
                >
                  <ChevronUp size={15} />
                </button>
                <button
                  type="button"
                  className="tier-move"
                  onClick={() => move(index, 1)}
                  disabled={index === tiers.length - 1}
                  title="Run later"
                  aria-label={`Move ${tier.label} later`}
                >
                  <ChevronDown size={15} />
                </button>
              </div>
            </div>

            <div className="tier-body">
              <label className="field-label" htmlFor={`mode-${tier.id}`}>
                Pipeline mode
              </label>
              <select
                id={`mode-${tier.id}`}
                className="native-select tier-mode"
                value={tier.pipelineMode}
                disabled={!tier.enabled}
                onChange={(event) =>
                  update(tier.id, {
                    pipelineMode: event.target.value as MedallionTier["pipelineMode"],
                    pipelineTemplateId:
                      event.target.value === "static" ? PIPELINE_TEMPLATES[0].id : null,
                  })
                }
              >
                {PIPELINE_MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>
                    {mode.label}
                  </option>
                ))}
              </select>

              {tier.pipelineMode === "static" ? (
                <div className="tier-static">
                  <label className="field-label" htmlFor={`pipeline-${tier.id}`}>
                    Bound pipeline
                  </label>
                  <select
                    id={`pipeline-${tier.id}`}
                    className="native-select tier-mode"
                    value={tier.pipelineTemplateId ?? ""}
                    disabled={!tier.enabled}
                    onChange={(event) =>
                      update(tier.id, { pipelineTemplateId: event.target.value })
                    }
                  >
                    {PIPELINE_TEMPLATES.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.label}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              {tier.backing === "planned" ? (
                <p className="field-hint">
                  This tier is not part of the pipeline contract yet — it posts with the form but
                  runs nothing today.
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
