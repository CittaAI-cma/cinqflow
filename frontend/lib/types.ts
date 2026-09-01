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

// ── CF-V1-E6-04 · version compare, and the loss that does not announce itself ─

/**
 * One target field that differs between two mapping versions.
 *
 * `loses_its_source` is the field a row-loss investigation starts from, and it
 * is computed rather than described: a line that went from populated to empty
 * is how a column silently goes dark after a release, with nothing failing.
 */
export interface MappingDiffLine {
  address: string;
  change: string;
  before: string;
  after: string;
  loses_its_source: boolean;
  explanation: string;
}

export interface MappingDiff {
  feed_id: string;
  from_version: number;
  to_version: number;
  from_published: boolean;
  lines: MappingDiffLine[];
  fields_losing_their_source: string[];
  summary: string;
}

// ── NL rules and their preview · CF-V1-E7-01, CF-V1-E7-02 ────────────────────

/**
 * One proposed rule, as the reviewer reads it.
 *
 * BOTH TEXTS travel. `stated` is the BA's own sentence, verbatim; `explanation`
 * is generated from the check, so it cannot drift from what runs. Where the two
 * disagree the rule is wrong, and a screen showing one of them would hide it.
 *
 * `sql` and `pyspark` are rendered by the PLATFORM from the check — the model
 * never writes either, which is what makes approving a rule a matter of
 * reading configuration rather than reading a dialect.
 */
export interface ProposedRule {
  stated: string;
  unsupported: boolean;
  unsupported_reason: string;
  rule_id: string | null;
  name: string;
  explanation: string;
  check_kind: string | null;
  column: string | null;
  dimension: string | null;
  severity: string | null;
  glossary_id: string | null;
  confidence: number | null;
  settled_by: string;
  rationale: string;
  sql: string;
  pyspark: string;
}

/** One row a rule caught, already masked before it reached the wire. */
export interface FailingRow {
  row_number: number;
  values: Record<string, string>;
}

export interface RulePreview {
  rule_id: string;
  stated: string;
  explanation: string;
  tested: number;
  passed: number;
  failed: number;
  skipped: number;
  failure_rate: number;
  failing_rows: FailingRow[];
  masked_columns: string[];
  not_previewable: string;
  summary: string;
}

export interface RulePreviewPack {
  feed_id: string;
  sample_rows: number;
  rules_previewed: number;
  rules_not_previewable: number;
  total_failures: number;
  previews: RulePreview[];
}

/** CF-V1-E3-05. What happened to a file somebody just sent. */
export interface Delivery {
  outcome: "ACCEPTED" | "REJECTED" | "UNEXPECTED" | "SKIPPED";
  headline: string;
  reason: string | null;
  check_name: string | null;
  feed_id: string;
  filename: string;
  /** Where the connector PUT it — always under `incoming/`. */
  key: string;
  /** Where it IS, after landing moved it. This is the one to show a person. */
  landed_key: string;
  size_bytes: number;
  fingerprint: string;
  business_date: string;
  delivered_by: string;
  source: string;
  citation_id: string;
  route: string;
  profile_id: string | null;
  next_step: string;
}

/** CF-V1-E16-04/E16-06. One page of an uploaded document — a
 *  `document:<id>#p<n>` citation's fragment. */
export interface DocumentPage {
  number: number;
  text: string;
  table_count: number;
}

/** CF-V1-E16-04/E16-06. What `POST /api/feeds/{feedId}/documents` returns,
 *  and `document:<id>`'s citation destination reads back. */
export interface Document {
  document_id: string;
  filename: string;
  media_type: string;
  feed_id: string | null;
  domain: string | null;
  page_count: number;
  pages: DocumentPage[];
  version: number;
  lifecycle_state: string;
  status: string;
}

/** Whether the delivery source can be reached at all. */
export interface DeliverySource {
  reachable: boolean;
  source: string;
  detail: string;
}

/** One profiling run, as the feed page's "Recent deliveries" list shows it —
 *  a summary, not the full column-by-column profile `FileProfileOut` carries. */
export interface FeedProfile {
  profile_id: string;
  source_key: string;
  source_fingerprint: string;
  readable: boolean;
  would_load: boolean;
  refusal: { reason: string; explanation: string; ask_the_payer: string } | null;
  structure: { data_rows: number; column_count: number; file_format: string };
  profiled_ts: string;
  profiled_by: string;
}

// ── incidents · CF-V2-E12-04 ──────────────────────────────────────────────

/** One error the batch's engine logged, root cause or consequence alike. */
export interface BatchError {
  error_id_hash: string;
  stage: string;
  category: string;
  message: string;
  occurred_ts: string;
  rule_id: string | null;
  is_consequence: boolean;
  caused_by: string | null;
  citation: string;
  route: string;
}

/** One earlier incident this signature also produced. */
export interface PriorIncident {
  incident_id: string;
  occurred_ts: string;
  fix_minutes: number | null;
  batch_id: string | null;
  citation: string;
}

/** A matched recovery guide, WITH the evidence that justifies the claim —
 *  a match may not be claimed without showing the fingerprint. Present only
 *  for a KNOWN failure; absent (null) means NOVEL. */
export interface GuideMatch {
  guide_id: string;
  title: string;
  steps: string[];
  signature: string;
  matched_errors: string[];
  occurrences: number;
  mean_fix_minutes: number | null;
  remedy: string | null;
  stale: boolean;
  priors: PriorIncident[];
  citations: string[];
  explanation: string;
}

/** One line in the incident list — the ledger's current state, cheap enough
 *  to serve for every open incident at once. The full evidence bundle stays
 *  on the per-batch route (`Incident` below), which recomputes it. */
export interface IncidentRow {
  incident_id: string;
  batch_id: string;
  feed_id: string;
  state: string;
  signature: string;
  assigned_to: string;
  opened_ts: string;
  resolved_ts: string | null;
}

/** The whole incident: evidence recomputed on every read, decisions read
 *  from the ledger. `match` null is NOVEL; present is KNOWN. */
export interface Incident {
  incident_id: string;
  batch_id: string;
  feed_id: string;
  opened_ts: string;
  kind: string;
  status: StatusWord;
  state: string;
  acknowledged_by: string;
  assigned_to: string;
  resolution: string;
  resolved_ts: string | null;
  signature: string;
  root_cause: BatchError | null;
  consequences: BatchError[];
  match: GuideMatch | null;
  proposed_remedy: string | null;
  explanation: string;
  citation: string;
  route: string;
}

// ── certification · CF-V2-E13-03/04 ───────────────────────────────────────

/** A discrepancy, written down by whoever found it — the platform computes
 *  `delta` and `critical`; the finder never grades their own homework.
 *  Decimal fields arrive as strings, the same convention `Budget` and
 *  `AgentAction` already use for money on the wire. */
export interface Variance {
  variance_id: string;
  batch_id: string;
  feed_id: string;
  kind: string;
  expected: string;
  actual: string;
  delta: string;
  tolerance: string;
  critical: boolean;
  outcome: string;
  opened_by: string;
  opened_ts: string;
  explanation: string;
  waived_by: string;
  waiver_reason: string;
  waiver_expires_on: string | null;
  citation: string;
}

/** One mandatory check's result. `completed: false` means PENDING —
 *  silence is not a pass, and it is never rendered as one. */
export interface CertificationCheck {
  kind: string;
  passed: boolean;
  completed: boolean;
  evidence: string;
}

/** A batch's certification, DERIVED on every read from retained history.
 *  `verdict` is one of exactly four strings: "Certified",
 *  "Certified-with-Waiver", "Not Certified", "Pending" — rendered verbatim,
 *  never re-cased or abbreviated. There is no route that sets one. */
export interface Certification {
  batch_id: string;
  feed_id: string;
  verdict: string;
  publishable: boolean;
  derived_ts: string | null;
  checks: CertificationCheck[];
  variances: Variance[];
}

// ── the medallion layers (W3-01) ────────────────────────────────────────────

/** One cell, and whether what you are reading is the whole truth.
 *
 *  There is deliberately no `original` field — the unmasked value never leaves
 *  the adapter, so no amount of client code can reveal it. `masked` is decided
 *  by the server from the schema contract's `is_phi` flag; the browser only
 *  renders the decision. */
export interface LayerCell {
  value: string | null;
  masked: boolean;
  reason: string;
}

/** A column as the CONTRACT declares it and as the ENGINE actually has it.
 *  Both, never merged: `declared_type` is portable (`timestamp_utc`),
 *  `engine_type` is what the plane reports (`timestamptz`). A difference here
 *  is a drift, and it is the same comparison the conformance kit makes. */
export interface LayerColumn {
  name: string;
  declared_type: string;
  engine_type: string;
  nullable: boolean;
  is_phi: boolean;
  present_on_plane: boolean;
}

/** `row_count: null` means the table is NOT on the plane — distinct from 0,
 *  which means it is there and empty. The screen renders them differently
 *  because a missing migration is not an empty table. */
export interface LayerTable {
  schema_name: string;
  name: string;
  comment: string;
  append_only: boolean;
  row_count: number | null;
  phi_column_count: number;
  primary_key: string[];
  columns: LayerColumn[];
  rows_route: string;
}

/** One position on the medallion spine, built or not.
 *
 *  `status` is one of `built` · `provisioned_empty` · `not_built`, and the
 *  three are rendered as three different things. `row_count` is null rather
 *  than 0 whenever nothing is on the plane. */
export interface Layer {
  layer: string;
  label: string;
  purpose: string;
  entry_gate: string;
  status: "built" | "provisioned_empty" | "not_built";
  schema_name: string;
  wave: number;
  absence_reason: string;
  row_count: number | null;
  table_count: number;
  route: string;
}

/** Why rows did not cross a gate, grouped by the rule that excluded them. */
export interface QuarantineReason {
  rule_id: string;
  reason: string;
  stage: string;
  row_count: number;
}

/** One batch's balance at one stage. `balanced` is the LEDGER's verdict;
 *  `unattributed` is derived and shown beside it, so a green tick with
 *  unexplained rows behind it is visible rather than trusted. */
export interface ReconLine {
  batch_id: string;
  feed_id: string;
  stage: string;
  records_in: number;
  records_out: number;
  quarantined: number;
  attributed_drops: number;
  balanced: boolean;
  unattributed: number;
  recorded_ts: string;
  route: string;
}

export interface LayerDetail {
  layer: Layer;
  tables: LayerTable[];
  quarantine: QuarantineReason[];
  reconciliation: ReconLine[];
}

export interface LayerRows {
  schema_name: string;
  table: string;
  columns: string[];
  rows: Record<string, LayerCell>[];
  total_rows: number;
  truncated: boolean;
  masked_columns: string[];
  batch_id: string | null;
}

// ── the governed action surface · CF-V2-E12-03 / CF-V2-E8-04 ────────────────

/** What one offered action would do, in the operator's own language, before
 *  they confirm it. `requires_approval_identifier` is computed from the
 *  environment the server is actually running in — never guessed here. */
export interface ActionPreview {
  action: string;
  target: string;
  what_will_happen: string;
  scope_records: number;
  scope_stages: string[];
  estimated_minutes: number;
  requires_approval_identifier: boolean;
  explanation: string;
}

/** Exactly the actions `authorize` would permit right now — a console that
 *  draws a button this does not offer is a console this story exists to
 *  replace. `environment` decides whether the approval-identifier field is
 *  shown as required. */
export interface ActionSurface {
  target: string;
  offered: string[];
  previews: ActionPreview[];
  environment: string;
}

/** 'requested' is not 'succeeded' — `is_complete` stays false until
 *  something re-read the control tables and observed the outcome. */
export interface ActionRecord {
  record_id: string;
  action: string;
  target: string;
  actor_subject: string;
  requested_ts: string;
  phase: string;
  status: StatusWord;
  is_complete: boolean;
  reason: string;
  approval_identifier: string;
  verified_ts: string | null;
  outcome: string;
}

// ── the technical review queue · CF-V1-E7-04 ────────────────────────────────

/** One rule the authoring agent could not draft with confidence — the BA's
 *  own sentence, the machine's reading of it, and why the two are being
 *  shown side by side rather than published as-is. */
export interface TechnicalReview {
  review_id: string;
  feed_id: string;
  stated: string;
  machine_reading: string;
  reason: string;
  explained_to_author: string;
  confidence: number;
  state: string;
  status: StatusWord;
  created_ts: string | null;
  evidence: Record<string, unknown>;
}

/** `unrouted` must always be empty — served rather than left to CI, because
 *  a control only CI can see is a control nobody maintains. */
export interface ReviewQueue {
  reviews: TechnicalReview[];
  open_count: number;
  unrouted: string[];
}

// ── the approval packet · CF-V1-E11-02 ───────────────────────────────────────

/** One object a change would reach, and the path that found it — so a
 *  reviewer can check the reasoning, not just trust a count. */
export interface Touched {
  object_type: string;
  object_id: string;
  version: number;
  lifecycle_state: string;
  via: string;
}

/** A declared consumer lineage could not resolve. Shown, never hidden — a
 *  blank where a downstream item should be is how rubber-stamping hides. */
export interface UnknownImpact {
  name: string;
  reason: string;
}

/** The change, both sides of its impact, and the evidence — every field
 *  COMPUTED, never the author's recollection of what they touched. */
export interface ImpactPacket {
  object_type: string;
  object_id: string;
  version: number;
  lifecycle_state: string;
  author_subject: string;
  diff: string[];
  engineering_impact: Touched[];
  business_impact: Touched[];
  unknowns: UnknownImpact[];
  evidence: Record<string, unknown>;
  blocks_production: boolean;
  is_empty: boolean;
}

// ── the work queue · CF-V1-E11-01 ────────────────────────────────────────────

/** Any governed object, as the lifecycle sees it — one shape for all ten
 *  types, because there is one state machine. */
export interface Governed {
  object_type: string;
  object_id: string;
  version: number;
  lifecycle_state: string;
  status: StatusWord;
  created_by_subject: string;
  created_by_name: string;
  created_ts: string;
  approved_by_subject: string | null;
  approved_by_name: string | null;
  approved_ts: string | null;
  body: Record<string, unknown>;
  warnings: string[];
}

export interface WorkQueue {
  awaiting_my_review: Governed[];
  my_submissions: Governed[];
}

// ── the reliability score · CF-V2-E12-05 ─────────────────────────────────────

/** One signal's contribution — `measured` is the difference between "scored
 *  zero" and "not measurable yet", which a screen must never collapse. */
export interface ReliabilityComponent {
  signal: string;
  value: number;
  weight: number;
  evidence: string;
  sample_size: number;
  measured: boolean;
}

// ── rule policy configuration · CF-V1-E7-03 ──────────────────────────────────

/** Where one rule runs, what happens on failure, and when — CF-V1-E7-03. */
export interface RulePolicy {
  rule_id: string;
  layer: string;
  on_failure: string;
  threshold_percent: string | null;
  execution_order: number;
  effective_from: string | null;
  effective_to: string | null;
  alert_recipient: string;
  owner: string;
  rationale: string;
  describes: string;
}

export interface RulePolicySet {
  feed_id: string;
  version: number;
  lifecycle_state: string;
  policies: RulePolicy[];
  is_approvable: boolean;
}

export interface Reliability {
  feed_id: string;
  as_of: string;
  overall: number;
  band: string;
  confidence: number;
  components: ReliabilityComponent[];
  citation: string;
}

// ── alerts that explain themselves · CF-V2-E12-05 ────────────────────────────

/** One grouped SLA breach, explained — or honestly not. `cause_citations` is
 *  empty exactly when `manual_path` is true: the two can never disagree. */
export interface EnrichedAlert {
  group_key: string;
  feed_ids: string[];
  severity: string;
  facts: string[];
  cause: string;
  citations: string[];
  cause_citations: string[];
  manual_path: boolean;
  model_called: boolean;
  refusals: string[];
  cost_usd: string;
}

// ── merge/split evidence card · R4, human-always · CF-V3-E9-03 ──────────────

export interface SatelliteRepoint {
  entity: string;
  record_id: string;
  from_member_id: string;
  to_member_id: string;
}

export interface DuplicateCollapse {
  entity: string;
  kept_record_id: string;
  collapsed_record_id: string;
}

/** The deterministic preview. `fingerprint` is what `/execute` checks the
 *  approved plan against — a stale form resubmitted after the candidate data
 *  changed is refused rather than silently executed against new facts. */
export interface MergePlan {
  merged_away_member_id: string;
  survivor_member_id: string;
  marked_merged: string;
  repoints: SatelliteRepoint[];
  collapses: DuplicateCollapse[];
  fingerprint: string;
}

/** `narrative`/`grounded_fields` are empty together when no model answered —
 *  never rendered as if a narrative failed to load; the plan below is
 *  complete either way. */
export interface EvidenceCard {
  demographic_comparison: Record<string, "match" | "differs" | "similar">;
  plan: MergePlan;
  narrative: string;
  grounded_fields: string[];
  model_called: boolean;
}

export interface MergeExecuteResult {
  plan: MergePlan;
  steward_approval_id: string;
  authorized_ts: string;
}

// ── identity exception queue · CF-V3-E9-02 ───────────────────────────────────

/** One batch's contribution — never merged away, so a steward can see that
 *  the same person has failed three times, not just that they have. */
export interface IdentityExceptionOccurrence {
  batch_id: string;
  outcome: "resolved" | "unresolved" | "failed";
  occurred_ts: string;
  detail: string;
}

/** Current state of one person's identity problem, folded from the ledger.
 *  `key` is what every other route on this queue addresses it by. */
export interface IdentityException {
  key: string;
  source_system: string;
  source_member_id: string;
  state: "open" | "assigned" | "escalated" | "resolved";
  assigned_to: string | null;
  opened_ts: string;
  latest_ts: string;
  occurrence_count: number;
  occurrences: IdentityExceptionOccurrence[];
}

/** Per source, never rolled up — "a payer sending bad demographics becomes
 *  visible" only if the number stays split. */
export interface QueueHealth {
  source_system: string;
  open_count: number;
  breached_count: number;
  resolved_count: number;
}

// ── daily identity accounting and coverage telemetry · CF-V3-E9-04 ──────────

/** One source, one day. `total` always rides beside the percentages it is a
 *  share of — coverage without its denominator is a documented don't.
 *  `is_regression`/`drop_points` already compare this row against the one
 *  immediately before it in the same response. */
export interface CoverageSnapshot {
  source_system: string;
  business_date: string;
  total: number;
  with_link_id: number;
  with_our_id: number;
  with_both: number;
  link_id_coverage_pct: string;
  our_id_coverage_pct: string;
  both_coverage_pct: string;
  is_regression: boolean;
  drop_points: string | null;
}

/** The automated form of "validate LinkID matches between lake and legacy" —
 *  one row per day this source's scorecard ran. */
export interface ParityCheck {
  source_system: string;
  business_date: string;
  checked: number;
  matched: number;
  mismatched: number;
  match_rate_pct: string;
}

// ── the five-step onboarding wizard · CF-V1-E4-01/02/03 ─────────────────────
//
// These have existed on the API since the wizard shipped, and until now no
// page in this application declared them — the guided journey the MVP calls
// its headline promise had a complete backend and no front door.

/** One thing standing between the BA and done.
 *
 *  `route` is `CitationId.route`, already resolved server-side, so
 *  "one-click navigation back to them" is a link this client renders rather
 *  than a mapping it has to know how to build. */
export interface Obstacle {
  key: string;
  what: string;
  why_it_matters: string;
  how_to_fix: string;
  citation: string | null;
  route: string;
  blocking: boolean;
}

/** One of the five, as it actually is.
 *
 *  `status` is one of the seven words a user is ever shown; `state` is the
 *  richer machine the wizard needs to decide which control to draw. The UI
 *  reads the word and never invents an eighth. */
export interface WizardStep {
  step: string;
  ordinal: number;
  label: string;
  state: string;
  status: StatusWord;
  is_complete: boolean;
  version: number | null;
  citation: string | null;
  obstacles: Obstacle[];
}

/** The single readiness view — what is complete, what is missing, what needs
 *  somebody else. Computed from the governed objects on every request, so it
 *  cannot disagree with the lifecycle it describes. */
export interface Wizard {
  feed_id: string;
  steps: WizardStep[];
  resume_at: string;
  is_publishable: boolean;
  outstanding: Obstacle[];
  gaps: Obstacle[];
  operations_outstanding: string[];
  explanation: string;
}

export interface RuleOutcome {
  rule_id: string;
  name: string;
  tested: number;
  flagged: number;
  hit_rate: number;
  quarantined: boolean;
}

export interface DropExplanation {
  rule_id: string;
  reason: string;
  record_count: number;
  columns: string[];
}

/** One row before and after — masked in the PACK, never on the way out of an
 *  API, so the masking still applies once somebody exports it. */
export interface EvidenceExample {
  row_number: number;
  before: Record<string, string>;
  after: Record<string, string>;
}

export interface EvidenceGap {
  key: string;
  what: string;
  why_it_is_acceptable: string;
  citation: string | null;
}

export interface EvidenceFailure {
  step: string;
  explanation: string;
  citation: string | null;
  route: string;
}

/** What a reviewer receives, generated rather than assembled.
 *
 *  `fingerprint` is the whole staleness mechanism: it is computed from the
 *  configuration the test ran against, so editing a mapping afterwards makes
 *  the pack demonstrably describe a different feed. */
export interface EvidencePack {
  feed_id: string;
  fingerprint: string;
  produced_ts: string;
  rows_in: number;
  rows_loaded: number;
  rows_quarantined: number;
  balanced: boolean;
  accounts_for_every_row: boolean;
  partial: boolean;
  summary: string;
  sample_filename: string;
  drops: DropExplanation[];
  rules: RuleOutcome[];
  examples: EvidenceExample[];
  gaps: EvidenceGap[];
  failure: EvidenceFailure | null;
  markdown: string;
}

export interface NarrativeChapter {
  occurred_ts: string;
  who: string;
  what: string;
  object_type: string;
  detail: string;
}

/** The whole journey, oldest first — built from the audit ledger, so an act
 *  that produced no audit entry does not appear, which is correct. */
export interface Narrative {
  feed_id: string;
  chapters: NarrativeChapter[];
  story: string;
}
