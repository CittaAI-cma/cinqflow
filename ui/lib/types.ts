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

/** CF-V1-E3-02. One thing that must be true before a feed can be operated. */
export interface ChecklistItem {
  key: string;
  question: string;
  satisfied: boolean;
  why_it_matters: string;
  how_to_fix: string;
}

export interface Readiness {
  feed_id: string;
  is_ready: boolean;
  outstanding: number;
  items: ChecklistItem[];
  explanation: string;
}

export interface Owner {
  role: string;
  subject: string;
  display_name: string;
}

/**
 * The operational envelope around a feed's six engine fields. CF-V1-E3-02.
 *
 * `endpoint_ref` is the connection profile's NAME for the endpoint, never a
 * host — so this object is the same in every environment.
 */
export interface FeedOperations {
  source_id: string;
  direction: string;
  delivery_method: string;
  endpoint_ref: string;
  owners: Owner[];
  service_level: {
    expected_by_local_time: string;
    timezone: string;
    calendar: string;
    grace_minutes: number;
    escalate_after_minutes: number;
  } | null;
  volume: {
    minimum_records: number | null;
    maximum_records: number | null;
    typical_records: number | null;
    tolerance_percent: number;
  } | null;
  alert_chain: { after_minutes: number; channel: string; notify: string[] }[];
  documents: { kind: string; label: string; reference: string }[];
  notes: string;
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
  operations: FeedOperations;
  readiness: Readiness | null;
}

/** CF-V1-E3-04. Whether a feed is paused, on the axis that is not the lifecycle. */
export interface FeedSuspension {
  feed_id: string;
  is_paused: boolean;
  reason: string;
  paused_by: string | null;
  paused_ts: string | null;
  resumes_after: string | null;
  may_start_new_work: boolean;
  affects_work_already_running: boolean;
  explanation: string;
}

export interface Source {
  source_id: string;
  name: string;
  kind: string;
  endpoint_ref: string;
  line_of_business: string[];
  states: string[];
  owners: Owner[];
  counterparty_contact: string;
  notes: string;
  version: number;
  lifecycle_state: string;
  status: StatusWord;
  feed_ids: string[];
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

// ── the mapping studio · CF-V1-E6-03 ─────────────────────────────────────────

/**
 * What to do with the source value. Parameters only.
 *
 * Note what this interface does NOT have: an `expression`, `sql` or `formula`
 * field. There is none on the wire because there is none in the object — a
 * mapping is configuration a steward approves by reading it, and the moment a
 * line can carry an expression, approving one means reading a language.
 */
export interface MappingTransform {
  kind: string;
  target_type: string | null;
  date_format: string | null;
  separator: string | null;
  part: number | null;
  lookup: string[][];
  on_unlisted: string;
  cases: { when_in: string[]; then: string }[];
  literal: string | null;
  default_value: string | null;
  describe: string;
}

/** One target field, and where its value comes from. */
export interface MappingLine {
  target_entity: string;
  target_field: string;
  source_columns: string[];
  transform: MappingTransform;
  null_policy: string;
  default_value: string | null;
  platform_supplied: boolean;
  unmapped_reason: string;
  glossary_id: string | null;
  notes: string;
  confidence: number | null;
  citations: string[];
  status: string;
  describe: string;
}

/**
 * One thing wrong, or worth knowing, about a mapping. Three strings, for the
 * same reason a readiness checklist item has three: a finding that only names
 * a field gets a placeholder typed into it.
 */
export interface MappingFinding {
  key: string;
  address: string;
  severity: string;
  blocks: boolean;
  what: string;
  why_it_matters: string;
  how_to_fix: string;
}

export interface Mapping {
  feed_id: string;
  version: number;
  lifecycle_state: string;
  status: string;
  contract_version: number | null;
  citation_id: string;
  route: string;
  mapped_count: number;
  total_count: number;
  unmapped_count: number;
  lines: MappingLine[];
  findings: MappingFinding[];
  blocking_count: number;
}
