import Link from "next/link";

/** Reached by an explicit `notFound()` — an unknown batch, upload, feed or
 *  ingest group — as well as by a bad URL. Those two cases feel different to
 *  the person hitting them, so this names both rather than saying "404".
 *
 *  A missing id here is usually not a typo: it is a batch that belongs to a
 *  different environment, or an upload someone deleted. Saying so is more
 *  useful than "page not found", which invites the user to blame themselves
 *  and retype a UUID they copied correctly.
 */
export default function NotFound() {
  return (
    <div className="card u-rise-in" style={{ marginTop: 18, maxWidth: 720 }}>
      <span className="panel-label">Not found</span>
      <h2 style={{ margin: "8px 0 6px", fontSize: 19 }}>
        There is nothing at this address.
      </h2>
      <p className="meta" style={{ maxWidth: "62ch" }}>
        Either the URL is wrong, or the upload, batch, feed or group it names is
        not in this environment — an id from another environment, or a record
        that has since been deleted, both land here.
      </p>
      <div className="run-processing-actions" style={{ marginTop: 14 }}>
        <Link href="/data/intake" className="btn-dark">
          Go to ingestion
        </Link>
        <Link href="/" className="btn-outline">
          Home
        </Link>
      </div>
    </div>
  );
}
