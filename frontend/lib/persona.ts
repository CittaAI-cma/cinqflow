/** Persona defaults - the frontend half of `backend/src/cinqflow/auth/persona.py`.
 *
 *  The mapping from roles to persona and capabilities lives in exactly one
 *  place, the backend; `CurrentUser` arrives with both already decided. This
 *  module only turns a persona into *defaults* - which reading mode a review
 *  opens in, which proposal filter, whether technical columns start collapsed,
 *  whether the workflow's unreached steps are shown - and holds the two
 *  sentences the UI says when a control isn't the caller's.
 *
 *  Persona is emphasis; capability is authority. Nothing here hides a fact or
 *  grants anything: the API enforces `capabilities`, the UI mirrors them
 *  (plan §14.2, §18.2). The analyst's own reading-mode override
 *  (`localStorage`, see `ReadingMode`) still wins over the default. */

import type { ReadingModeKey } from "@/components/run/ReadingMode";
import type { Persona } from "@/lib/auth";

export const PERSONA_LABEL: Record<Persona, string> = {
  data_analyst: "Data Analyst",
  data_platform: "Data Platform",
};

export interface PersonaDefaults {
  readingMode: ReadingModeKey;
  /** S4: start on the fields that need a decision, or on all of them. */
  proposalFilter: "decisions" | "all";
  /** Forensic tables: technical/system columns start collapsed (lands with PR-7). */
  technicalCollapsed: boolean;
  /** `WorkflowSteps`: show every declared step, including the unreached ones,
   *  from the start (Data Platform) - or only what has happened (Data Analyst). */
  workflowStepsExpanded: boolean;
}

export function personaDefaults(persona: Persona): PersonaDefaults {
  switch (persona) {
    case "data_platform":
      return {
        readingMode: "forensic",
        proposalFilter: "all",
        technicalCollapsed: false,
        workflowStepsExpanded: true,
      };
    case "data_analyst":
    default:
      return {
        readingMode: "evidence",
        proposalFilter: "decisions",
        technicalCollapsed: true,
        workflowStepsExpanded: false,
      };
  }
}

/** Shown in place of a gate's controls when `can_decide_gates` is false. */
export const GATE_LOCKED_REASON =
  "Your role can review this run but not decide it — an approver or business analyst signs the gate.";

/** Shown in place of a retry/re-run control when `can_rerun_steps` is false. */
export const RERUN_LOCKED_REASON =
  "Retrying and re-running are Data Platform actions — a data engineer or operations role.";
