"use client";

import { useActionState, useEffect, useMemo, useRef, useState } from "react";
import { useFormStatus } from "react-dom";
import { saveSpec, type StudioState } from "@/app/mapping/actions";
import Confidence from "@/components/ui/Confidence";
import { evidenceClass } from "@/lib/evidence";
import { useToast } from "@/lib/useToast";
import type {
  MappingFieldSpec,
  MappingVersionDetail,
  MappingVocabulary,
  SpecFieldError,
} from "@/lib/api";

function SaveBar({
  hint,
  dirty,
  problems,
}: {
  hint: string;
  dirty: boolean;
  /** Rejected-field count from the last attempt. The banner explaining the
   *  rejection renders at the top of the form, which on a 40-row spec is
   *  thousands of pixels above the button that caused it — so the count is
   *  repeated here, beside the control the analyst is actually looking at. */
  problems: number;
}) {
  const { pending } = useFormStatus();
  return (
    <div className="row save-bar" style={{ justifyContent: "space-between", marginTop: 14 }}>
      <span className="meta">
        {/* The refusal outranks the dirty dot. After a rejected save both are
            true — nothing was written, so the edits are still unsaved — but
            "nothing was saved" already carries that, and it is the fact the
            analyst needs at the button they just pressed. */}
        {problems > 0 ? (
          <span className="error">
            {problems} field{problems === 1 ? "" : "s"} refused — nothing was saved.
          </span>
        ) : dirty ? (
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

/** Matches a validation error to the row it belongs to.
 *
 *  It used to match on `field_index`, which the server counts over the fields
 *  it actually received. `saveSpec` skips the rows whose Drop box is ticked and
 *  appends the "add a mapping" row at the end, so the array the server judged
 *  is not the array this table renders: drop row 1 and every error after it
 *  annotated the row above the real one, and an error on the appended row
 *  annotated nothing at all.
 *
 *  `SpecError` has always carried `source` as well, and a source column is what
 *  identifies a row here — the table is keyed by it, and `validate_spec`
 *  refuses a spec that maps one twice. So the match is by source, and the index
 *  is only a fallback for the one row that has no source of its own yet. */
function errorFor(
  errors: SpecFieldError[] | undefined,
  source: string,
  index: number,
  attribute: string,
): string | null {
  const hit = errors?.find(
    (e) =>
      e.attribute === attribute &&
      (e.source ? e.source === source : e.field_index === index),
  );
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

/** An example of the format an op expects, so the box that just became required
 *  says what to put in it. Placeholder text only — the server owns the rule. */
const ARG_EXAMPLE: Record<string, string> = {
  parse_date: "MM/DD/YYYY",
  concat: "text to append",
  substring: "0",
  cast: "string",
};

/** The casts that can satisfy a target's declared type, from the server's own
 *  `CAST_FOR_TYPE` table.
 *
 *  An illegal cast the spec already carries is still offered, because silently
 *  rewriting a persisted value would hide a real defect — but it is the only
 *  illegal one offered, and it is marked. A target with no declared type (or an
 *  API too old to publish the table) falls back to the full cast list and lets
 *  the server judge, exactly as before. */
function castsFor(
  vocabulary: MappingVocabulary,
  target: string,
  current: string,
): { options: string[]; declared: string | null; allowed: string[] | null } {
  const declared = vocabulary.target_types[target] ?? null;
  const allowed = declared ? vocabulary.casts_for_type?.[declared] : undefined;
  if (!allowed || allowed.length === 0) {
    return { options: vocabulary.casts, declared, allowed: null };
  }
  return {
    options: allowed.includes(current) ? allowed : [current, ...allowed],
    declared,
    allowed,
  };
}

/** One field of the spec.
 *
 *  Its own component so that changing a dropdown re-renders one row instead of
 *  the whole table. That matters because the row now *reacts* to its dropdowns:
 *  picking `default` under Nulls, or `quarantine` under Value map, or any
 *  transform that takes an argument, makes the box that rule needs `required`
 *  and focusable — which is the whole fix for "my dropdown edits vanished".
 *  Those four edits are each reachable with nothing but a dropdown, each made
 *  the spec invalid on its own, and the save is all-or-nothing over one
 *  artifact, so one of them discarded every unrelated edit in the table.
 *
 *  The text inputs stay uncontrolled (`defaultValue`), which is what keeps a
 *  200-column spec from re-rendering on every keystroke. Only the three select
 *  values this row's own requirements depend on are held in state.
 */
function SpecRow({
  field,
  index,
  vocabulary,
  errors,
  rationale,
  requiredTargets,
}: {
  field: MappingFieldSpec;
  index: number;
  vocabulary: MappingVocabulary;
  errors: SpecFieldError[] | undefined;
  rationale: MappingVersionDetail["ai_context"][string] | undefined;
  requiredTargets: Set<string>;
}) {
  const [target, setTarget] = useState(field.target);
  const [cast, setCast] = useState(field.cast);
  const [op, setOp] = useState(field.transform?.op ?? "");
  const [onNull, setOnNull] = useState(field.on_null);
  const [onUnmapped, setOnUnmapped] = useState(field.on_unmapped_value);
  /** Set when changing the target forced the cast to change with it, so the
   *  substitution is stated rather than just happening. */
  const [castFollowed, setCastFollowed] = useState<string | null>(null);

  const targetError = errorFor(errors, field.source, index, "target");
  const castError = errorFor(errors, field.source, index, "cast");
  const transformError = errorFor(errors, field.source, index, "transform");
  const nullError =
    errorFor(errors, field.source, index, "on_null") ??
    errorFor(errors, field.source, index, "default");
  const mapError = errorFor(errors, field.source, index, "on_unmapped_value");
  const sourceError = errorFor(errors, field.source, index, "source");
  const isRequired = requiredTargets.has(target);

  // Every "this box is now mandatory" below is the server's rule, read from the
  // vocabulary it publishes — never a copy of the rule written here.
  const argName = argNameFor(op);
  const argRequired = Boolean(op && (vocabulary.op_args?.[op]?.length ?? 0) > 0);
  const defaultRequired = (vocabulary.on_null_needs_default ?? []).includes(onNull);
  const valueMapRequired = (vocabulary.on_unmapped_needs_value_map ?? []).includes(onUnmapped);

  const { options: castOptions, declared, allowed } = castsFor(vocabulary, target, cast);
  const castIsIllegal = Boolean(allowed && !allowed.includes(cast));

  /** Changing the target can make the current cast unable to satisfy the new
   *  declared type — the exact edit that produced
   *  "'members.first_name' is declared string; cast 'int' cannot satisfy it"
   *  and threw away the whole table with it. The cast follows the target to
   *  the one that fits, and says so, rather than waiting to be refused. */
  function chooseTarget(next: string) {
    setTarget(next);
    const nextDeclared = vocabulary.target_types[next];
    const nextAllowed = nextDeclared ? vocabulary.casts_for_type?.[nextDeclared] : undefined;
    if (nextAllowed?.length && !nextAllowed.includes(cast)) {
      setCast(nextAllowed[0]);
      setCastFollowed(nextAllowed[0]);
    } else {
      setCastFollowed(null);
    }
  }

  return (
    <tr>
      <td className="mono">
        <input type="hidden" name={`source_${index}`} value={field.source} />
        {/* The row carries its own note through the form. Without this every
            save wrote `note: null` over whatever the field held, because the
            action read a form field the studio never rendered. */}
        <input type="hidden" name={`note_${index}`} value={field.note ?? ""} />
        {field.source}
        {sourceError ? <div className="error small">{sourceError}</div> : null}
        {rationale ? (
          <div className="studio-rationale" title="Why the AI proposed this">
            {rationale.concept ? <div className="meta small">{rationale.concept}</div> : null}
            {/* The rationale is about the target the model named. Once the
                analyst points the column somewhere else, that confidence and
                that evidence describe a claim nobody is making any more —
                rendering them here would attach a model's 0.98 to a mapping the
                model never proposed. So the meter is withdrawn and what
                happened is stated instead. */}
            {rationale.target && rationale.target !== target ? (
              <div className="meta small">
                Rated <span className="mono">{rationale.target}</span>, which you changed —
                its confidence and evidence no longer describe this mapping.
              </div>
            ) : (
              <>
                <Confidence value={rationale.confidence} />
                <div className="evidence-list">
                  {rationale.evidence.map((item) => (
                    <span key={item} className={`evidence-chip ${evidenceClass(item)}`}>
                      {item}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : null}
      </td>
      <td>
        <select
          name={`target_${index}`}
          value={target}
          onChange={(event) => chooseTarget(event.target.value)}
          className={targetError ? "bad" : undefined}
          aria-invalid={targetError ? true : undefined}
          aria-label={`Canonical target for ${field.source}`}
        >
          {[...new Set([field.target, ...vocabulary.targets])].filter(Boolean).map((option) => (
            <option key={option} value={option}>
              {option}
              {vocabulary.target_types[option] ? ` · ${vocabulary.target_types[option]}` : ""}
              {requiredTargets.has(option) ? " · required" : ""}
            </option>
          ))}
        </select>
        {isRequired ? <span className="tag danger">required</span> : null}
        {targetError ? <div className="error small">{targetError}</div> : null}
      </td>
      <td>
        <select
          name={`cast_${index}`}
          value={cast}
          onChange={(event) => {
            setCast(event.target.value);
            setCastFollowed(null);
          }}
          className={castError || castIsIllegal ? "bad" : undefined}
          aria-invalid={castError || castIsIllegal ? true : undefined}
          aria-label={`Cast for ${field.source}`}
        >
          {castOptions.map((option) => (
            <option key={option} value={option}>
              {option}
              {allowed && !allowed.includes(option) ? " · cannot satisfy the target" : ""}
            </option>
          ))}
        </select>
        {castFollowed ? (
          <div className="meta small">
            followed the target to <span className="mono">{castFollowed}</span>
          </div>
        ) : declared && allowed ? (
          <div className="meta small">
            {declared}
            {allowed.length === 1 ? " · the only cast that fits" : ""}
          </div>
        ) : null}
        {castError ? <div className="error small">{castError}</div> : null}
      </td>
      <td>
        <select
          name={`op_${index}`}
          value={op}
          onChange={(event) => setOp(event.target.value)}
          aria-label={`Transform for ${field.source}`}
        >
          <option value="">none</option>
          {vocabulary.ops.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <input
          name={`op_arg_${index}`}
          defaultValue={primaryArg(field.transform)}
          placeholder={argRequired ? (ARG_EXAMPLE[op] ?? argName ?? "argument") : "argument"}
          required={argRequired}
          aria-label={argName ? `${op} ${argName}` : `Transform argument for ${field.source}`}
          title={
            extraArgs(field.transform).length
              ? `Also carries ${extraArgs(field.transform).join(", ")} — preserved on save`
              : argRequired
                ? `${op} cannot run without ${argName}`
                : undefined
          }
          aria-invalid={transformError ? true : undefined}
          className={transformError ? "bad narrow" : "narrow"}
        />
        {/* A transform can carry arguments this one box does not edit. They
            travel with the row so a save cannot drop them; see `buildArgs` in
            app/mapping/actions.ts. */}
        <input type="hidden" name={`op_original_${index}`} value={field.transform?.op ?? ""} />
        <input
          type="hidden"
          name={`op_args_${index}`}
          value={JSON.stringify(field.transform?.args ?? {})}
        />
        {argRequired ? (
          <div className="meta small">
            needs <span className="mono">{argName}</span>
          </div>
        ) : null}
        {extraArgs(field.transform).length ? (
          <div className="meta small">+{extraArgs(field.transform).join(", ")}</div>
        ) : null}
        {transformError ? <div className="error small">{transformError}</div> : null}
      </td>
      <td>
        <select
          name={`on_null_${index}`}
          value={onNull}
          onChange={(event) => setOnNull(event.target.value)}
          aria-label={`Null handling for ${field.source}`}
        >
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
          required={defaultRequired}
          aria-label={`Default value for ${field.source}`}
          title={defaultRequired ? "on_null 'default' cannot be saved without a value" : undefined}
          aria-invalid={nullError ? true : undefined}
          className={nullError ? "bad narrow" : "narrow"}
        />
        {defaultRequired ? <div className="meta small">needs a value</div> : null}
        {nullError ? <div className="error small">{nullError}</div> : null}
      </td>
      <td>
        <input
          name={`value_map_${index}`}
          defaultValue={Object.entries(field.value_map)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")}
          placeholder="M=male, F=female"
          required={valueMapRequired}
          aria-label={`Value map for ${field.source}`}
          title={
            valueMapRequired
              ? `on_unmapped_value '${onUnmapped}' only means something with a value map`
              : undefined
          }
          aria-invalid={mapError ? true : undefined}
          className={mapError ? "bad" : undefined}
        />
        <select
          name={`on_unmapped_${index}`}
          value={onUnmapped}
          onChange={(event) => setOnUnmapped(event.target.value)}
          aria-label={`Unmapped value rule for ${field.source}`}
        >
          {vocabulary.on_unmapped_value.map((rule) => (
            <option key={rule} value={rule}>
              {rule}
            </option>
          ))}
        </select>
        {valueMapRequired ? <div className="meta small">needs a map</div> : null}
        {mapError ? <div className="error small">{mapError}</div> : null}
      </td>
      <td className="center">
        <input
          type="checkbox"
          name={`edited_${index}`}
          defaultChecked={field.edited}
          aria-label={`${field.source} is analyst-owned`}
          title="Marks this field as analyst-owned rather than AI-proposed"
        />
      </td>
      <td className="center">
        <input
          type="checkbox"
          name={`remove_${index}`}
          aria-label={`Remove the mapping for ${field.source}`}
          title="Remove this mapping"
        />
      </td>
    </tr>
  );
}

export default function MappingStudio({
  mapping,
  basePath,
}: {
  mapping: MappingVersionDetail;
  /** The route this studio is being rendered on — `/mapping/{feed}` or
   *  `/runs/{uploadId}/mapping`. Submitted with the form so `saveSpec` can
   *  revalidate the surface the analyst is actually looking at; without it a
   *  save made on the run surface left that route serving a cached render of
   *  the pre-save spec. */
  basePath?: string;
}) {
  const [state, action] = useActionState<StudioState, FormData>(saveSpec, {});
  const { spec, vocabulary, ai_context: aiContext } = mapping;
  const specLevel = state.errors?.filter((e) => e.field_index === -1) ?? [];
  const requiredTargets = useMemo(
    () => new Set(Object.values(vocabulary.primary_keys).flat()),
    [vocabulary.primary_keys],
  );
  const missing = missingRequired(mapping);

  /** Rejected fields whose source column is not a row in this table — the
   *  "add a mapping" row the server judged and refused. They have nowhere to be
   *  annotated, so they are named in the banner instead of being invisible. */
  const unplaced = (state.errors ?? []).filter(
    (e) => e.field_index !== -1 && e.source && !spec.fields.some((f) => f.source === e.source),
  );

  /** Every control in this table's *text* inputs is uncontrolled
   *  (`defaultValue`), which is what keeps a 200-column spec from re-rendering
   *  on every keystroke. The cost is that nothing in React's tree knows an edit
   *  happened, so a stray reload or a click on a version chip used to discard
   *  the lot in silence. Listening for `input` on the form recovers the one bit
   *  that matters — "something changed" — without controlling every field. */
  const formRef = useRef<HTMLFormElement>(null);
  const [dirty, setDirty] = useState(false);

  /** Identity of the spec these uncontrolled inputs were built from.
   *
   *  React never re-applies `defaultValue` on a re-render, so the table went on
   *  showing whatever was typed into it whether the write succeeded or was
   *  refused — it could not tell the analyst those two apart. Keying the form
   *  on the stored spec's own content fixes that in one move: a save that
   *  changed the spec produces a new key, the subtree remounts, and every input
   *  re-reads what the server now holds. It also clears the "add a mapping"
   *  row, which otherwise kept its value and made the next save fail with
   *  "'X' is already mapped at field N".
   *
   *  Content, deliberately, and not `updated_ts`: a completing preview calls
   *  `set_mapping_status(..., "previewed")`, which bumps `updated_ts` without
   *  touching the spec (`workers/run_preview.py`). `WorkflowSteps` refreshes
   *  the route when that lands, so keying on the timestamp would have thrown
   *  away an analyst's unsaved edits the moment their preview finished. A
   *  refused save leaves the stored spec untouched, so the key is stable and
   *  every edit survives for them to correct. */
  const specKey = useMemo(() => JSON.stringify(spec), [spec]);

  // A completed save is the new clean baseline. The inline "Draft saved." line
  // stays as the permanent record on the page; the toast is what carries the
  // confirmation to someone whose eyes are at the bottom of a 40-row spec
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
  }, [state.saved, state.attempt]);

  useEffect(() => {
    if (state.error) push(state.error, "error");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.error, state.attempt]);

  // The rejection case had no toast at all — and it is the most common one, and
  // the only one whose explanation renders at the top of a form whose Save
  // button is at the bottom. So a rejected save looked exactly like nothing
  // happening. Now it announces itself and moves focus to the first field the
  // server refused, which also scrolls it into view.
  useEffect(() => {
    const errors = state.errors;
    if (!errors?.length) return;
    const count = errors.filter((e) => e.field_index !== -1).length;
    push(
      count > 0
        ? `${count} field${count === 1 ? "" : "s"} refused — nothing was saved.`
        : "The spec was refused — nothing was saved.",
      "error",
    );
    const first = formRef.current?.querySelector<HTMLElement>(".bad, [aria-invalid='true']");
    if (first) {
      first.scrollIntoView({ block: "center", behavior: "smooth" });
      first.focus({ preventScroll: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.errors, state.attempt]);

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
      /* Deliberately not cleared on submit. Only a *completed* save is a clean
         baseline (see the `state.saved` effect), and clearing it here disarmed
         the beforeunload guard the moment you clicked Save — so after a refused
         save, when the edits are still unsaved and still yours to fix, the
         browser would let you navigate away and lose the lot without a word. */
      /* Remounts the editor when, and only when, the *stored spec* differs
         from the one these inputs were built from — see `specKey`. */
      key={specKey}
    >
      <input type="hidden" name="feed" value={mapping.feed} />
      <input type="hidden" name="version" value={mapping.version} />
      <input type="hidden" name="field_count" value={spec.fields.length} />
      {basePath ? <input type="hidden" name="base_path" value={basePath} /> : null}

      {state.error ? <p className="error">{state.error}</p> : null}
      {state.saved ? <p className="ok">Draft saved.</p> : null}
      {state.errors?.length ? (
        <p className="alert error">
          {state.errors.length} problem{state.errors.length === 1 ? "" : "s"} — nothing was
          saved, including edits on rows that were fine. Each is shown against its field below.
          {unplaced.length ? (
            <>
              {" "}
              The new mapping{unplaced.length === 1 ? "" : "s"} you added{" "}
              {unplaced.length === 1 ? "was" : "were"} refused:{" "}
              {unplaced.map((e) => (
                <span key={`${e.source}-${e.attribute}`}>
                  <span className="mono">{e.source}</span> — {e.message}.{" "}
                </span>
              ))}
            </>
          ) : null}
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
            {spec.fields.map((field, index) => (
              <SpecRow
                key={`${field.source}-${index}`}
                field={field}
                index={index}
                vocabulary={vocabulary}
                errors={state.errors}
                rationale={aiContext[field.source]}
                requiredTargets={requiredTargets}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="card grid" style={{ marginTop: 14 }}>
        <label htmlFor="new_source">Add a mapping the AI left unmapped</label>
        <div className="row">
          <input id="new_source" name="new_source" placeholder="source column" />
          <select name="new_target" defaultValue="" aria-label="Canonical target for the new mapping">
            <option value="">choose a canonical target…</option>
            {vocabulary.targets.map((target) => (
              <option key={target} value={target}>
                {target} · {vocabulary.target_types[target]}
              </option>
            ))}
          </select>
          <select name="new_cast" defaultValue="string" aria-label="Cast for the new mapping">
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
        problems={state.errors?.length ?? 0}
        hint={`Only the ${vocabulary.targets.length} canonical targets and ${vocabulary.ops.length} named transforms are offered — a spec is data, never code.`}
      />
    </form>
  );
}

function tablesOf(targets: string[]): string[] {
  return [...new Set(targets.map((t) => `silver_raw.${t.split(".")[0]}`))].sort();
}
