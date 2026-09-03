"use server";

import { revalidatePath } from "next/cache";
import {
  approveMappingVersion,
  createMappingVersion,
  requestPreview,
  saveMappingSpec,
  type MappingFieldSpec,
  type MappingSpecShape,
  type SpecFieldError,
} from "@/lib/api";

export interface StudioState {
  errors?: SpecFieldError[];
  error?: string;
  saved?: boolean;
  /** Set by G2: the batch the approved mapping was queued to promote. */
  batchId?: string;
}

/** Rebuilds the spec from the submitted form and saves it as the draft.
 *  Field-level errors come back for annotation rather than being thrown. */
export async function saveSpec(_previous: StudioState, form: FormData): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const targetTable = String(form.get("target_table") ?? "");
  const count = Number(form.get("field_count") ?? 0);

  const fields: MappingFieldSpec[] = [];
  for (let i = 0; i < count; i += 1) {
    if (form.get(`remove_${i}`) === "on") continue;
    const source = String(form.get(`source_${i}`) ?? "").trim();
    if (!source) continue;

    const op = String(form.get(`op_${i}`) ?? "");
    const arg = String(form.get(`op_arg_${i}`) ?? "").trim();
    const valueMapRaw = String(form.get(`value_map_${i}`) ?? "").trim();
    const valueMap: Record<string, string> = {};
    for (const pair of valueMapRaw.split(",")) {
      const [key, value] = pair.split("=").map((part) => part?.trim());
      if (key && value) valueMap[key] = value;
    }

    fields.push({
      source,
      target: String(form.get(`target_${i}`) ?? "").trim(),
      cast: String(form.get(`cast_${i}`) ?? "string"),
      transform: op ? { op, args: arg ? { [argNameFor(op)]: arg } : {} } : null,
      value_map: valueMap,
      on_null: String(form.get(`on_null_${i}`) ?? "pass"),
      default: String(form.get(`default_${i}`) ?? "").trim() || null,
      on_unmapped_value: String(form.get(`on_unmapped_${i}`) ?? "pass"),
      edited: form.get(`edited_${i}`) === "on",
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
  if (result.errors || result.error) return result;

  revalidatePath(`/mapping/${feed}`);
  return { saved: true };
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

  const result = await createMappingVersion(feed, {
    from_proposal_id: proposalId,
    derive_from_version: deriveFrom,
  });
  if (result.error) return { error: result.error };

  revalidatePath(`/mapping/${feed}`);
  return { saved: true };
}

/** G2: the analyst takes responsibility for this version. The write happens in a
 *  worker, so this returns as soon as the decision is recorded. */
export async function approveVersion(
  _previous: StudioState,
  form: FormData,
): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const note = String(form.get("note") ?? "").trim();

  const { error, batchId } = await approveMappingVersion(feed, version, note);
  if (error) return { error };
  revalidatePath(`/mapping/${feed}`);
  return { saved: true, batchId };
}

export async function runPreview(_previous: StudioState, form: FormData): Promise<StudioState> {
  const feed = String(form.get("feed") ?? "");
  const version = Number(form.get("version") ?? 0);
  const { error } = await requestPreview(feed, version);
  if (error) return { error };
  revalidatePath(`/mapping/${feed}`);
  return { saved: true };
}
