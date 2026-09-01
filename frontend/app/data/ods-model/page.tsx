import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";

/**
 * The canonical ODS model — the GOVERNED, versioned truth. CF-V3-E10-02.
 *
 * NOT `/data/canonical`: that browser projects whatever the platform happens
 * to have deployed plus the glossary. This page reads the model E10-01
 * deploys through the same lifecycle every other governed object uses —
 * Draft, In Review, Approved, Published — and shows only the version a
 * downstream team may actually build against. A proposal still in review
 * never appears here as if it were current; it shows up on the version
 * history as exactly what it is, a proposal.
 */
type Summary = {
  version: number;
  published_by: string;
  published_ts: string;
  entities: string[];
};

export default async function OdsModelPage() {
  const model = await attempt<Summary>("/api/ods-model");
  if (isRefused(model)) return <RefusalNotice refusal={model} />;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / canonical ODS model
      </p>
      <h1>The canonical ODS model</h1>
      <p className="lede">
        The client&apos;s Member, Enrollment, Claims and Provider workbooks, deployed as one
        versioned, approved truth — the stated contract every mapping targets, not a contested
        spreadsheet.
      </p>

      <div className="card">
        <p className="note">
          Published version {model.version}, approved by {model.published_by} on{" "}
          {new Date(model.published_ts).toLocaleDateString()}. <Link href="/data/ods-model/versions">
            Version history and changelog
          </Link>
          .
        </p>
      </div>

      <h2>Entities</h2>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Entity</th>
            </tr>
          </thead>
          <tbody>
            {model.entities.map((name) => (
              <tr className="row" key={name}>
                <td>
                  <Link href={`/data/ods-model/${encodeURIComponent(name)}`}>{name}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
