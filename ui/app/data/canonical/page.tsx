import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * The canonical model browser — domains, entities, fields. CF-V1-E6-01.
 *
 * "You cannot map to a model you cannot see."
 *
 * Both halves of this page are GENERATED: the deployed one from the DDL spec
 * the conformance kit checks the database against, the designed one from the
 * client's own 171-term glossary. There is no third list, which is what
 * "drift impossible by construction" means — a hand-maintained data dictionary
 * is exactly the artefact this platform exists to retire, and one embedded in
 * the tool that replaced it would be worse, because it would look
 * authoritative.
 *
 * The GAP is shown rather than hidden. The client has designed twenty
 * entities; one is provisioned. A browser listing only what exists would hide
 * the roadmap, and one listing everything without the distinction would let a
 * BA map to a table nobody has created.
 */
type Entity = {
  name: string;
  domains: string[];
  schema_name: string;
  deployed: boolean;
  comment: string;
  field_count: number;
  defined_count: number;
  phi_count: number;
};
type Model = {
  domains: string[];
  entities: Entity[];
  deployed_entities: number;
  designed_not_deployed: string[];
  defined_fields: number;
  total_fields: number;
  unclaimed_tables: string[];
};
type Field = {
  name: string;
  entity: string;
  definition: string;
  definition_missing: boolean;
  term: string;
  is_phi: boolean;
  deployed: boolean;
};

export default async function CanonicalModelPage({
  searchParams,
}: {
  searchParams: Promise<{ domain?: string; q?: string }>;
}) {
  const { domain = "", q = "" } = await searchParams;
  const model = await attempt<Model>(
    `/api/canonical${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`,
  );
  if (isRefused(model)) return <RefusalNotice refusal={model} />;

  const found = q
    ? await attempt<Field[]>(`/api/canonical/search?q=${encodeURIComponent(q)}`)
    : null;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / canonical model
      </p>
      <h1>The canonical model</h1>
      <p className="lede">
        What the estate maps <em>to</em>. Generated from the deployed schemas and the business
        glossary — nothing here is maintained by hand, so nothing here can be out of date.
      </p>

      <div className="card">
        <p className="note">
          {model.deployed_entities} entit{model.deployed_entities === 1 ? "y is" : "ies are"}{" "}
          provisioned; {model.designed_not_deployed.length} are designed and not yet deployed.{" "}
          {model.defined_fields} of {model.total_fields} fields have a business definition
          {model.total_fields > model.defined_fields
            ? `, and ${model.total_fields - model.defined_fields} do not`
            : ""}
          .
        </p>
      </div>

      <form className="card" method="get">
        <label htmlFor="q">Find a field</label>{" "}
        <input
          id="q"
          name="q"
          type="search"
          defaultValue={q}
          placeholder="date of birth, or DOB"
        />{" "}
        <button type="submit">Search</button>
        <p className="note">
          Business term or column name. &ldquo;date of birth&rdquo; and &ldquo;DOB&rdquo; both
          reach <span className="mono">Member_Date_Of_Birth</span>, because the glossary records
          every spelling this concept has ever arrived under.
        </p>
      </form>

      {found && !isRefused(found) ? (
        <>
          <h2>
            {found.length} field{found.length === 1 ? "" : "s"} matching &ldquo;{q}&rdquo;
          </h2>
          <div className="card scroll">
            <table>
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Field</th>
                  <th>Business term</th>
                  <th>Definition</th>
                </tr>
              </thead>
              <tbody>
                {found.map((field) => (
                  <tr className="row" key={`${field.entity}.${field.name}`}>
                    <td>
                      <Link href={`/data/canonical/${field.entity}`}>{field.entity}</Link>
                    </td>
                    <td className="mono">
                      {field.name}
                      {field.is_phi ? " · PHI" : ""}
                    </td>
                    <td>{field.term || <span className="note">—</span>}</td>
                    <td className={field.definition_missing ? "note" : undefined}>
                      {field.definition}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h2>Domains</h2>
      <p className="note">
        <Link href="/data/canonical">All</Link>
        {model.domains.map((name) => (
          <span key={name}>
            {" · "}
            <Link href={`/data/canonical?domain=${encodeURIComponent(name)}`}>{name}</Link>
          </span>
        ))}
      </p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Entity</th>
              <th>Domains</th>
              <th>Fields</th>
              <th>Defined</th>
              <th>PHI</th>
              <th>Deployed</th>
            </tr>
          </thead>
          <tbody>
            {model.entities.map((entity) => (
              <tr className="row" key={entity.name}>
                <td>
                  <Link href={`/data/canonical/${entity.name}`}>{entity.name}</Link>
                </td>
                <td className="note">{entity.domains.join(", ")}</td>
                <td>{entity.field_count}</td>
                <td>
                  {entity.defined_count}
                  {entity.defined_count < entity.field_count ? (
                    <span className="note">
                      {" "}
                      ({entity.field_count - entity.defined_count} missing)
                    </span>
                  ) : null}
                </td>
                <td>{entity.phi_count}</td>
                <td>
                  {entity.deployed ? (
                    entity.schema_name
                  ) : (
                    <span className="note">designed, not yet deployed</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {model.unclaimed_tables.length > 0 ? (
        <div className="card">
          <strong>Tables no business domain claims</strong>
          <p className="note">
            {model.unclaimed_tables.join(", ")} — provisioned, but nobody&apos;s business language
            names them. Worth a steward&apos;s attention rather than hiding.
          </p>
        </div>
      ) : null}
    </>
  );
}
