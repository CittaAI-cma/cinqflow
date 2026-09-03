"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { saveSpec, type StudioState } from "@/app/mapping/actions";
import type { MappingVersionDetail, SpecFieldError } from "@/lib/api";

function SaveBar({ dirtyHint }: { dirtyHint: string }) {
  const { pending } = useFormStatus();
  return (
    <div className="row" style={{ justifyContent: "space-between", marginTop: 14 }}>
      <span className="meta">{dirtyHint}</span>
      <button type="submit" disabled={pending}>
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

export default function MappingStudio({ mapping }: { mapping: MappingVersionDetail }) {
  const [state, action] = useActionState<StudioState, FormData>(saveSpec, {});
  const { spec, vocabulary } = mapping;
  const specLevel = state.errors?.filter((e) => e.field_index === -1) ?? [];

  return (
    <form action={action}>
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

              return (
                <tr key={`${field.source}-${index}`}>
                  <td className="mono">
                    <input type="hidden" name={`source_${index}`} value={field.source} />
                    {field.source}
                    {sourceError ? <div className="error small">{sourceError}</div> : null}
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
                          </option>
                        ))}
                    </select>
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
                      defaultValue={Object.values(field.transform?.args ?? {})[0] ?? ""}
                      placeholder="argument"
                      className={transformError ? "bad narrow" : "narrow"}
                    />
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
        dirtyHint={`Only the ${vocabulary.targets.length} canonical targets and ${vocabulary.ops.length} named transforms are offered — a spec is data, never code.`}
      />
    </form>
  );
}

function tablesOf(targets: string[]): string[] {
  return [...new Set(targets.map((t) => `silver_raw.${t.split(".")[0]}`))].sort();
}
