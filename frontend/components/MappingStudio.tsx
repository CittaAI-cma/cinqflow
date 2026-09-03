"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { useFormStatus } from "react-dom";
import { saveSpec, type StudioState } from "@/app/mapping/actions";
import Confidence from "@/components/ui/Confidence";
import { useToast } from "@/lib/useToast";
import type { MappingVersionDetail, SpecFieldError } from "@/lib/api";

function SaveBar({ hint, dirty }: { hint: string; dirty: boolean }) {
  const { pending } = useFormStatus();
  return (
    <div className="row save-bar" style={{ justifyContent: "space-between", marginTop: 14 }}>
      <span className="meta">
        {dirty ? (
          <span className="save-bar-dirty">
            <span className="save-bar-dot" aria-hidden="true" />
            Unsaved changes
          </span>
        ) : (
          hint
        )}
      </span>
      <button
        type="submit"
        disabled={pending}
        data-busy={pending ? "true" : undefined}
        title={dirty ? "Save these edits as the current draft" : "Nothing has changed since the last save"}
      >
        {pending ? "Validating…" : "Save draft"}
      </button>
    </div>
  );
}

function errorFor(
  errors: SpecFieldError[] | undefined,
  index: number,
  attribute: string,
): string | null {
  const hit = errors?.find((e) => e.field_index === index && e.attribute === attribute);
  return hit ? hit.message : null;
}

/** The entity's identity columns (see `MappingVocabulary.primary_keys`) that
 * this spec's already-mapped fields do not cover — exactly the check G2 makes
 * server-side, run here so the analyst sees the wall before they reach it. */
function missingRequired(mapping: MappingVersionDetail): string[] {
  const mapped = new Set(mapping.spec.fields.map((f) => f.target));
  const touched = new Set(mapping.spec.fields.map((f) => f.target.split(".")[0]));
  const missing: string[] = [];
  for (const table of touched) {
    for (const target of mapping.vocabulary.primary_keys[table] ?? []) {
      if (!mapped.has(target)) missing.push(target);
    }
  }
  return missing;
}


/** Mirrors `argNameFor` in `app/mapping/actions.ts` — the argument the single
 *  editor box speaks for. Kept in step with it deliberately: the box edits one
 *  named argument, and the server merges it over whatever else the transform
 *  already carried. */
function argNameFor(op: string | undefined): string | null {
  if (!op) return null;
  if (op === "parse_date") return "format";
  if (op === "concat") return "with";
  if (op === "substring") return "start";
  if (op === "cast") return "to";
  return "value";
}

function primaryArg(transform: { op: string; args: Record<string, string> } | null): string {
  if (!transform) return "";
  const key = argNameFor(transform.op);
  return (key ? transform.args[key] : undefined) ?? "";
}

/** Arguments the box does not edit, named so the analyst can see they exist
 *  rather than discovering later that a save quietly changed the spec. */
function extraArgs(transform: { op: string; args: Record<string, string> } | null): string[] {
  if (!transform) return [];
  const key = argNameFor(transform.op);
  return Object.keys(transform.args).filter((name) => name !== key);
}

export default function MappingStudio({ mapping }: { mapping: MappingVersionDetail }) {
  const [state, action] = useActionState<StudioState, FormData>(saveSpec, {});
  const { spec, vocabulary, ai_context: aiContext } = mapping;
  const specLevel = state.errors?.filter((e) => e.field_index === -1) ?? [];
  const requiredTargets = new Set(Object.values(vocabulary.primary_keys).flat());
  const missing = missingRequired(mapping);

  /** Every control in this table is uncontrolled (`defaultValue`), which is
   *  what keeps a 200-column spec from re-rendering on every keystroke. The
   *  cost is that nothing in React's tree knows an edit happened, so a stray
   *  reload or a click on a version chip used to discard the lot in silence.
   *  Listening for `input` on the form recovers the one bit that matters —
   *  "something changed" — without controlling a single field. */
  const formRef = useRef<HTMLFormElement>(null);
  const [dirty, setDirty] = useState(false);

  // A completed save is the new clean baseline. The inline "Draft saved."
  // line stays as the permanent record on the page; the toast is what carries
  // the confirmation to someone whose eyes are at the bottom of a 40-row spec
  // table, far from where that line renders.
  const { push } = useToast();
  useEffect(() => {
    if (state.saved) {
      setDirty(false);
      push("Draft saved.", "success");
    }
    // `push` is stable from the provider; depending on it would re-fire the
    // toast on unrelated re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.saved]);

  useEffect(() => {
    if (state.error) push(state.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.error]);

  useEffect(() => {
    if (!dirty) return;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
      // Browsers show their own wording; assigning returnValue is what arms it.
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <form
      action={action}
      ref={formRef}
      onInput={() => setDirty(true)}
      onSubmit={() => setDirty(false)}
    >
      <input type="hidden" name="feed" value={mapping.feed} />
      <input type="hidden" name="version" value={mapping.version} />
      <input type="hidden" name="field_count" value={spec.fields.length} />

      {state.error ? <p className="error">{state.error}</p> : null}
      {state.saved ? <p className="ok">Draft saved.</p> : null}
      {state.errors?.length ? (
        <p className="error">
          {state.errors.length} problem{state.errors.length === 1 ? "" : "s"} — nothing was
          saved. Each is shown against its field below.
        </p>
      ) : null}
      {missing.length ? (
        <p className="alert error">
          {missing.length} required field{missing.length === 1 ? "" : "s"} not yet mapped:{" "}
          <span className="mono">{missing.join(", ")}</span>. Each is an entity's own identity —
          G2 will not approve this draft until they are mapped.
        </p>
      ) : null}

      <div className="card grid">
        <div>
          <label htmlFor="target_table">Primary target entity</label>
          <select id="target_table" name="target_table" defaultValue={spec.target_table}>
            {[...new Set([spec.target_table, ...tablesOf(vocabulary.targets)])].map((table) => (
              <option key={table} value={table}>
                {table}
              </option>
            ))}
          </select>
          {specLevel.map((e) => (
            <p key={e.message} className="error">
              {e.message}
            </p>
          ))}
          <span className="meta">
            Field targets stay fully qualified, so one feed can populate several entities.
          </span>
        </div>
      </div>

      <div className="card scroll" style={{ padding: 0, marginTop: 14 }}>
        <table className="studio">
          <thead>
            <tr>
              <th>Source column</th>
              <th>Canonical target</th>
              <th>Cast</th>
              <th>Transform</th>
              <th>Nulls</th>
              <th>Value map</th>
              <th>Mine</th>
              <th>Drop</th>
            </tr>
          </thead>
          <tbody>
            {spec.fields.map((field, index) => {
              const targetError = errorFor(state.errors, index, "target");
              const castError = errorFor(state.errors, index, "cast");
              const transformError = errorFor(state.errors, index, "transform");
              const nullError =
                errorFor(state.errors, index, "on_null") ??
                errorFor(state.errors, index, "default");
              const mapError = errorFor(state.errors, index, "on_unmapped_value");
              const sourceError = errorFor(state.errors, index, "source");
              const rationale = aiContext[field.source];
              const isRequired = requiredTargets.has(field.target);

              return (
                <tr key={`${field.source}-${index}`}>
                  <td className="mono">
                    <input type="hidden" name={`source_${index}`} value={field.source} />
                    {field.source}
                    {sourceError ? <div className="error small">{sourceError}</div> : null}
                    {rationale ? (
                      <div className="studio-rationale" title="Why the AI proposed this">
                        {rationale.concept ? (
                          <div className="meta small">{rationale.concept}</div>
                        ) : null}
                        <Confidence value={rationale.confidence} />
                        <div className="evidence-list">
                          {rationale.evidence.map((item) => (
                            <span key={item} className="evidence-chip">
                              {item}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <select
                      name={`target_${index}`}
                      defaultValue={field.target}
                      className={targetError ? "bad" : undefined}
                    >
                      {[...new Set([field.target, ...vocabulary.targets])]
                        .filter(Boolean)
                        .map((target) => (
                          <option key={target} value={target}>
                            {target}
                            {vocabulary.target_types[target]
                              ? ` · ${vocabulary.target_types[target]}`
                              : ""}
                            {requiredTargets.has(target) ? " · required" : ""}
                          </option>
                        ))}
                    </select>
                    {isRequired ? <span className="tag danger">required</span> : null}
                    {targetError ? <div className="error small">{targetError}</div> : null}
                  </td>
                  <td>
                    <select
                      name={`cast_${index}`}
                      defaultValue={field.cast}
                      className={castError ? "bad" : undefined}
                    >
                      {vocabulary.casts.map((cast) => (
                        <option key={cast} value={cast}>
                          {cast}
                        </option>
                      ))}
                    </select>
                    {castError ? <div className="error small">{castError}</div> : null}
                  </td>
                  <td>
                    <select name={`op_${index}`} defaultValue={field.transform?.op ?? ""}>
                      <option value="">none</option>
                      {vocabulary.ops.map((op) => (
                        <option key={op} value={op}>
                          {op}
                        </option>
                      ))}
                    </select>
                    <input
                      name={`op_arg_${index}`}
                      defaultValue={primaryArg(field.transform)}
                      placeholder={argNameFor(field.transform?.op) ?? "argument"}
                      title={
                        extraArgs(field.transform).length
                          ? `Also carries ${extraArgs(field.transform).join(", ")} — preserved on save`
                          : undefined
                      }
                      className={transformError ? "bad narrow" : "narrow"}
                    />
                    {/* A transform can carry arguments this one box does not
                        edit. They travel with the row so a save cannot drop
                        them; see `buildArgs` in app/mapping/actions.ts. */}
                    <input
                      type="hidden"
                      name={`op_original_${index}`}
                      value={field.transform?.op ?? ""}
                    />
                    <input
                      type="hidden"
                      name={`op_args_${index}`}
                      value={JSON.stringify(field.transform?.args ?? {})}
                    />
                    {extraArgs(field.transform).length ? (
                      <div className="meta small">+{extraArgs(field.transform).join(", ")}</div>
                    ) : null}
                    {transformError ? <div className="error small">{transformError}</div> : null}
                  </td>
                  <td>
                    <select name={`on_null_${index}`} defaultValue={field.on_null}>
                      {vocabulary.on_null.map((rule) => (
                        <option key={rule} value={rule}>
                          {rule}
                        </option>
                      ))}
                    </select>
                    <input
                      name={`default_${index}`}
                      defaultValue={field.default ?? ""}
                      placeholder="default"
                      className={nullError ? "bad narrow" : "narrow"}
                    />
                    {nullError ? <div className="error small">{nullError}</div> : null}
                  </td>
                  <td>
                    <input
                      name={`value_map_${index}`}
                      defaultValue={Object.entries(field.value_map)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(", ")}
                      placeholder="M=male, F=female"
                    />
                    <select name={`on_unmapped_${index}`} defaultValue={field.on_unmapped_value}>
                      {vocabulary.on_unmapped_value.map((rule) => (
                        <option key={rule} value={rule}>
                          {rule}
                        </option>
                      ))}
                    </select>
                    {mapError ? <div className="error small">{mapError}</div> : null}
                  </td>
                  <td className="center">
                    <input
                      type="checkbox"
                      name={`edited_${index}`}
                      defaultChecked={field.edited}
                      title="Marks this field as analyst-owned rather than AI-proposed"
                    />
                  </td>
                  <td className="center">
                    <input type="checkbox" name={`remove_${index}`} title="Remove this mapping" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card grid" style={{ marginTop: 14 }}>
        <label>Add a mapping the AI left unmapped</label>
        <div className="row">
          <input name="new_source" placeholder="source column" />
          <select name="new_target" defaultValue="">
            <option value="">choose a canonical target…</option>
            {vocabulary.targets.map((target) => (
              <option key={target} value={target}>
                {target} · {vocabulary.target_types[target]}
              </option>
            ))}
          </select>
          <select name="new_cast" defaultValue="string">
            {vocabulary.casts.map((cast) => (
              <option key={cast} value={cast}>
                {cast}
              </option>
            ))}
          </select>
        </div>
      </div>

      <SaveBar
        dirty={dirty}
        hint={`Only the ${vocabulary.targets.length} canonical targets and ${vocabulary.ops.length} named transforms are offered — a spec is data, never code.`}
      />
    </form>
  );
}

function tablesOf(targets: string[]): string[] {
  return [...new Set(targets.map((t) => `silver_raw.${t.split(".")[0]}`))].sort();
}
