import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * One canonical entity, with every field's definition inline. CF-V1-E6-01.
 *
 * The DEFINITION column is the reason this page exists. A field list without
 * definitions is a schema dump — a BA reading `Member_Internal_ID` learns
 * nothing they did not already guess. With the glossary's own words beside it,
 * they can tell whether it is the field they mean.
 *
 * Where there is no definition the page says so, in those words. Not a blank
 * cell, and not the column's name repeated back as if it were an explanation:
 * "definition missing" is a finding a steward acts on, and it should read like
 * one.
 */
type Field = {
  name: string;
  domains: string[];
  definition: string;
  definition_missing: boolean;
  glossary_id: string | null;
  term: string;
  synonyms: string[];
  is_phi: boolean;
  type: string | null;
  nullable: boolean | null;
  deployed: boolean;
};
type Entity = {
  name: string;
  domains: string[];
  schema_name: string;
  deployed: boolean;
  comment: string;
  field_count: number;
  defined_count: number;
  phi_count: number;
  fields: Field[];
};

export default async function CanonicalEntityPage({
  params,
}: {
  params: Promise<{ entity: string }>;
}) {
  const { entity } = await params;
  const found = await attempt<Entity>(`/api/canonical/${encodeURIComponent(entity)}`);

  if (isRefused(found)) {
    return (
      <>
        <p className="note">
          <Link href="/data/canonical">Canonical model</Link> / {entity}
        </p>
        <h1>{entity}</h1>
        <RefusalNotice refusal={found} />
      </>
    );
  }

  const missing = found.field_count - found.defined_count;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href="/data/canonical">Canonical model</Link> / {found.name}
      </p>
      <h1>{found.name}</h1>
      <p className="lede">
        {found.domains.join(", ")} · {found.field_count} fields ·{" "}
        {found.deployed ? (
          <>provisioned in {found.schema_name}</>
        ) : (
          <>designed, not yet deployed</>
        )}
      </p>

      {found.comment ? <p className="note">{found.comment}</p> : null}

      {!found.deployed ? (
        <div className="card">
          <strong>This entity does not exist yet</strong>
          <p className="note">
            The client&apos;s glossary designs it; nothing has provisioned it. It is shown so the
            model can be read whole — but a mapping written against it would have no table to
            load into.
          </p>
        </div>
      ) : null}

      {missing > 0 ? (
        <div className="card">
          <strong>
            {missing} field{missing === 1 ? "" : "s"} have no business definition
          </strong>
          <p className="note">
            Shown below as &ldquo;definition missing&rdquo;. Each one is a column somebody will
            have to explain to the next person who maps to it.
          </p>
        </div>
      ) : null}

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th>Type</th>
              <th>Business term</th>
              <th>Definition</th>
              <th>Also known as</th>
            </tr>
          </thead>
          <tbody>
            {found.fields.map((field) => (
              <tr className="row" key={field.name}>
                <td className="mono">
                  {field.name}
                  {field.is_phi ? <span className="note"> · PHI</span> : null}
                </td>
                <td>
                  {field.type ?? <span className="note">not deployed</span>}
                  {field.nullable === false ? <span className="note"> · required</span> : null}
                </td>
                <td>
                  {field.glossary_id ? (
                    <Link href={`/data/intake/glossary/${field.glossary_id}`}>{field.term}</Link>
                  ) : (
                    <span className="note">—</span>
                  )}
                </td>
                <td className={field.definition_missing ? "note" : undefined}>
                  {field.definition}
                </td>
                <td className="note mono">
                  {field.synonyms.length > 0 ? field.synonyms.join(", ") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
