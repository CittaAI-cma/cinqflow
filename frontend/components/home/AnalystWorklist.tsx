import Link from "next/link";
import StatusWord from "@/components/StatusWord";
import Timestamp from "@/components/ui/Timestamp";
import { getWorklist } from "@/lib/api";
import { uploadStatusWord } from "@/lib/statusWords";

/** The Data Analyst home (PR-4): what is waiting for a decision, then the
 *  recent runs. One call (`GET /api/worklist`) - the O(1) answer the register
 *  used to compute in the browser. `waiting_since` is the moment the gate
 *  opened in the step ledger, not when the file arrived. An approver sees
 *  exactly the runs the register flags "Needs Review"; nothing here is a
 *  second opinion on the control plane's state. */
export default async function AnalystWorklist() {
  const worklist = await getWorklist().catch(() => null);
  if (!worklist) {
    return (
      <section className="home-section">
        <p className="meta">The control plane did not answer, so the worklist cannot be shown.</p>
      </section>
    );
  }

  const g1 = worklist.uploads_at_g1;
  const g2 = worklist.mapping_versions_at_g2;
  const waiting = worklist.counts.waiting_at_g1 + worklist.counts.approvable_at_g2;

  return (
    <section className="home-section">
      <p className="home-lede">
        {waiting === 0
          ? "Nothing is waiting for you at a gate."
          : `${waiting} run${waiting === 1 ? " is" : "s are"} waiting for you at a gate.`}
      </p>

      {waiting > 0 ? (
        <div className="card" style={{ padding: 0 }}>
          <div className="dt-wrap">
            <table className="dt">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Feed</th>
                  <th>Gate</th>
                  <th>Waiting since</th>
                </tr>
              </thead>
              <tbody>
                {g1.map((upload) => (
                  <tr key={upload.upload_id}>
                    <td>
                      <Link href={`/runs/${encodeURIComponent(upload.upload_id)}/review`}>
                        {upload.filename}
                      </Link>
                    </td>
                    <td className="mono">{upload.feed}</td>
                    <td>
                      <span className="tag gate">G1</span>{" "}
                      <span className="meta">approve or reject the interpretation</span>
                    </td>
                    <td>
                      <Timestamp value={upload.waiting_since} withSeconds={false} />
                    </td>
                  </tr>
                ))}
                {g2.map((version) => (
                  <tr key={`${version.feed}:${version.version}`}>
                    <td>
                      <Link href={`/mapping/${encodeURIComponent(version.feed)}?v=${version.version}`}>
                        mapping v{version.version}
                      </Link>
                    </td>
                    <td className="mono">{version.feed}</td>
                    <td>
                      <span className="tag gate">G2</span>{" "}
                      <span className="meta">previewed — approve to promote</span>
                    </td>
                    <td>
                      <Timestamp value={version.waiting_since} withSeconds={false} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {worklist.recent_uploads.length ? (
        <div className="card" style={{ marginTop: 14 }}>
          <span className="panel-label">Recent runs</span>
          <ul className="plain home-recent">
            {worklist.recent_uploads.map((upload) => (
              <li key={upload.upload_id}>
                <Link href={`/uploads/${encodeURIComponent(upload.upload_id)}`}>
                  {upload.filename}
                </Link>{" "}
                <span className="mono meta">{upload.feed}</span>{" "}
                <StatusWord word={uploadStatusWord(upload.status)} raw={upload.status} />{" "}
                <Timestamp value={upload.created_ts} withSeconds={false} className="meta" />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="meta" style={{ marginTop: 14 }}>
          No runs yet — upload a file from Ingestion to start one.
        </p>
      )}
    </section>
  );
}
