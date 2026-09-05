"use server";

import { revalidatePath } from "next/cache";
import {
  createMappingVersion,
  requestPreview,
  saveMappingSpec,
  type MappingFieldSpec,
  type MappingSpecShape,
  type SpecFieldError,
} from "@/lib/api";
import { authMutate } from "@/lib/auth";
import { valueMapRecord } from "@/lib/valueMap";

export interface StudioState {
  errors?: SpecFieldError[];
  error?: string;
  saved?: boolean;
  /** Set by G2: the batch the approved mapping was queued to promote. */
  batchId?: string;
  /** Increments on every completed attempt, successful or refused. `useEffect`
   *  in the studio keys its toast and its focus-the-first-error behaviour off
   *  this rather than off `errors`, because two consecutive saves that fail
   *  the same way produce an identical `errors` array — and an effect that
   *  depends on an identical value does not re-fire, so the second click was
   *  silent. */
  attempt?: number;
}

/** Revalidates the surface the form was submitted from as well as the durable
 *  studio route.
 *
 *  `MappingPageBody` is deliberately shared between `/mapping/[feed]` and
 *  `/runs/[uploadId]/mapping`, but every action here only ever revalidated the
 *  first — so a save made on the run surface left that route serving its cached
 *  render. The visible symptom is the one this whole pass is about: the table
 *  keeps showing what you typed, because nothing re-fetched what was stored.
 *  `basePath` comes from the same `baseHref` the page already threads through. */
function revalidateStudio(feed: string, basePath?: string | null): void {
  const durable = `/mapping/${feed}`;
  revalidatePath(durable);
  if (basePath && basePath !== durable) revalidatePath(basePath);
}

/** Rebuilds the spec from the submitted form and saves it as the draft.
 *  Field-level errors come back for annotation rather than being thrown. */
export async function saveSpec(_previous: StudioState, form: FormData): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const targetTable = String(form.get("target_table") ?? "");
  const count = Number(form.get("field_count") ?? 0);
  const basePath = String(form.get("base_path") ?? "") || null;
  const attempt = (_previous.attempt ?? 0) + 1;

  const fields: MappingFieldSpec[] = [];
  for (let i = 0; i < count; i += 1) {
    if (form.get(`remove_${i}`) === "on") continue;
    const source = String(form.get(`source_${i}`) ?? "").trim();
    if (!source) continue;

    const op = String(form.get(`op_${i}`) ?? "");
    const arg = String(form.get(`op_arg_${i}`) ?? "").trim();
    // The editor exposes one argument box, but a saved transform can legally
    // carry several (`substring` takes start *and* length). Rebuilding the
    // args from the single box alone silently dropped every other one on save
    // — a spec that round-tripped through the studio untouched came back
    // different. So the row carries its original args, and the box edits only
    // the primary one; the rest are preserved as-is, unless the analyst
    // changed the operation, in which case the old op's arguments no longer
    // mean anything and are correctly discarded.
    const originalOp = String(form.get(`op_original_${i}`) ?? "");
    let preservedArgs: Record<string, string> = {};
    if (op && op === originalOp) {
      try {
        const parsed: unknown = JSON.parse(String(form.get(`op_args_${i}`) ?? "{}"));
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
            preservedArgs[key] = String(value);
          }
        }
      } catch {
        // A malformed hidden field is not worth failing a save over; the
        // primary argument below still applies.
        preservedArgs = {};
      }
    }
    // Parsed by the same function the row echoes back, so what the analyst
    // reads under the box is what the spec will carry - see lib/valueMap.ts.
    const valueMap = valueMapRecord(String(form.get(`value_map_${i}`) ?? ""));

    fields.push({
      source,
      target: String(form.get(`target_${i}`) ?? "").trim(),
      cast: String(form.get(`cast_${i}`) ?? "string"),
      transform: op ? { op, args: buildArgs(op, arg, preservedArgs) } : null,
      value_map: valueMap,
      on_null: String(form.get(`on_null_${i}`) ?? "pass"),
      default: String(form.get(`default_${i}`) ?? "").trim() || null,
      on_unmapped_value: String(form.get(`on_unmapped_${i}`) ?? "pass"),
      edited: form.get(`edited_${i}`) === "on",
      // The studio has no note editor, so this read the value of an input that
      // does not exist and wrote `note: null` over whatever the field carried —
      // every save silently erased any note in the spec. The row now carries
      // its own note through the form the same way it carries the transform
      // arguments the single arg box does not edit, so a save round-trips it
      // untouched. When a note editor arrives it writes to this same name.
      note: String(form.get(`note_${i}`) ?? "").trim() || null,
    });
  }

  const newSource = String(form.get("new_source") ?? "").trim();
  const newTarget = String(form.get("new_target") ?? "").trim();
  if (newSource && newTarget) {
    fields.push({
      source: newSource,
      target: newTarget,
      cast: String(form.get("new_cast") ?? "string"),
      transform: null,
      value_map: {},
      on_null: "pass",
      default: null,
      on_unmapped_value: "pass",
      edited: true, // added by the analyst, so it is theirs from the start
      note: null,
    });
  }

  const spec: MappingSpecShape = { target_table: targetTable, fields };
  const result = await saveMappingSpec(feed, version, spec);
  if (result.errors || result.error) return { ...result, attempt };

  revalidateStudio(feed, basePath);
  return { saved: true, attempt };
}

/** The edited primary argument merged over whatever else the transform
 *  already carried. Clearing the box removes the primary argument but leaves
 *  the others intact — the box only ever speaks for its own key. */
function buildArgs(
  op: string,
  primary: string,
  preserved: Record<string, string>,
): Record<string, string> {
  const key = argNameFor(op);
  const args = { ...preserved };
  if (primary) args[key] = primary;
  else delete args[key];
  return args;
}

/** parse_date takes `format`, concat takes `with`, substring takes `start`, cast takes `to`. */
function argNameFor(op: string): string {
  if (op === "parse_date") return "format";
  if (op === "concat") return "with";
  if (op === "substring") return "start";
  if (op === "cast") return "to";
  return "value";
}

export async function startDraft(_previous: StudioState, form: FormData): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const proposalId = String(form.get("from_proposal_id") ?? "") || undefined;
  const deriveFrom = Number(form.get("derive_from_version") ?? 0) || undefined;
  const basePath = String(form.get("base_path") ?? "") || null;
  // Without this the API records its own default placeholder as the author of
  // every mapping version, so `created_by` named nobody. Same correction as the
  // uploader's in 3a1b0ff: the form carries the signed-in user rather than the
  // endpoint growing a hard auth requirement it did not have.
  const createdBy = String(form.get("created_by") ?? "").trim() || undefined;

  const result = await createMappingVersion(feed, {
    from_proposal_id: proposalId,
    derive_from_version: deriveFrom,
    created_by: createdBy,
  });
  if (result.error) return { error: result.error };

  revalidateStudio(feed, basePath);
  return { saved: true };
}

/** G2: the analyst takes responsibility for this version. The write happens in a
 *  worker, so this returns as soon as the decision is recorded. Authenticated:
 *  the API records the session's user as the approver and refuses anyone
 *  without `can_decide_gates`; a 409's `message — hint` comes back verbatim. */
export async function approveVersion(
  _previous: StudioState,
  form: FormData,
): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const note = String(form.get("note") ?? "").trim();
  const basePath = String(form.get("base_path") ?? "") || null;

  const { data, error } = await authMutate<{ batch_id?: string }>(
    `/api/feeds/${encodeURIComponent(feed)}/mapping-versions/${version}/approve`,
    { method: "POST", body: JSON.stringify({ note: note || null }) },
  );
  if (error) return { error };
  revalidateStudio(feed, basePath);
  return { saved: true, batchId: data?.batch_id };
}

export async function runPreview(_previous: StudioState, form: FormData): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const basePath = String(form.get("base_path") ?? "") || null;
  const { error } = await requestPreview(feed, version);
  if (error) return { error };
  revalidateStudio(feed, basePath);
  return { saved: true };
}
