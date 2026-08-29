// Shapes mirrored from the BFF's OpenAPI document. The API is the contract;
// this file is the only place its shapes are named, so a field rename shows up
// in one diff rather than fifteen.

/** The seven. There is no eighth, and no synonym. */
export const STATUS_WORDS = [
  "Expected",
  "Received",
  "Processing",
  "Completed",
  "Needs Review",
  "Needs Attention",
  "Missing",
] as const;

export type StatusWord = (typeof STATUS_WORDS)[number];

/** One card on the persona home. The RANK is the array order — decided in
 *  core/persona.py, never in the browser. */
export interface HomeSlot {
  key: string;
  answers: string;
}

export interface Principal {
  subject: string;
  display_name: string;
  roles: string[];
  has_access: boolean;
  permitted_actions: string[];
  /** The ordered persona home. Empty for a principal with no access. */
  home_slots: HomeSlot[];
}

export interface Destination {
  key: string;
  label: string;
  route: string;
  group: string;
  answers: string;
  prominent: boolean;
}

export interface Navigation {
  active_wave: number;
  destinations: Destination[];
}

export interface Feed {
  feed_id: string;
  domain: string;
  source_system: string;
  file_format: string;
  landing_path: string;
  file_pattern: string;
  schedule_cron: string;
  version: number;
  lifecycle_state: string;
  status: StatusWord;
  citation_id: string;
  route: string;
}

export interface Batch {
  batch_id: string;
  feed_id: string;
  business_date: string;
  state: string;
  status: StatusWord;
  started_ts: string | null;
  completed_ts: string | null;
  citation_id: string;
  route: string;
}

export interface Rows {
  tool: string;
  rows: Record<string, unknown>[];
  citations: string[];
  row_count: number;
  out_of_scope: boolean;
  marker: string;
  note: string;
}

export interface Claim {
  text: string;
  citation_ids: string[];
  routes: string[];
}

export interface Ask {
  claims: Claim[];
  confidence: string;
  unanswered: string[];
  intent: string;
  tools_called: string[];
  trace: { node: string; duration_ms: number }[];
  cost_usd: string;
  refused: boolean;
  refusal: string;
  run_id: string;
}

export interface AgentAction {
  run_id: string;
  agent: string;
  action: string;
  outcome: string;
  is_refusal: boolean;
  actor_subject: string;
  actor_type: string;
  risk_class: string;
  prompt_ref: string;
  prompt_hash: string;
  model: string;
  model_version: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  latency_ms: number;
  occurred_ts: string;
  detail: string;
}

export interface Budget {
  agent: string;
  spent_today_usd: string;
  daily_cap_usd: string;
  per_run_cap_usd: string;
  runs_today: number;
  refusals_today: number;
  grounded_claims: number;
  uncited_claims_blocked: number;
}

export interface AuditEntry {
  object_type: string;
  object_id: string;
  version: number;
  action: string;
  actor_subject: string;
  actor_type: string;
  occurred_ts: string;
  detail: string;
}

export interface Contract {
  story_id: string;
  reads: string[];
  writes: string[];
  unknowns: { question: string; owner: string; blocks: boolean }[];
}

export interface Tool {
  name: string;
  answers: string;
  reads: string[];
  cites: string[];
  parameters: string[];
  note: string;
}
