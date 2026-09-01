import Link from "next/link";

/**
 * No route matches. Distinct from a REFUSAL (`RefusalNotice`) — that is the
 * server saying "no" about something real; this is the platform saying
 * nothing here exists to ask about.
 */
export default function NotFound() {
  return (
    <main>
      <h1>Nothing here</h1>
      <p className="lede">This address does not match anything CINQFLOW serves.</p>
      <div className="card">
        <p>
          If you followed a citation here, the citation itself may be malformed — a citation
          that resolves to nothing is treated as worse than none. <Link href="/">Go home</Link>.
        </p>
      </div>
    </main>
  );
}
