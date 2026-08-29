"use client";

import "./globals.css";

/**
 * The root layout crashed — almost always `/api/me` or `/api/navigation`
 * unreachable. Next.js requires this file to render its OWN `<html>` and
 * `<body>`, because the root layout that would normally supply them is
 * exactly what failed.
 */
export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main className="crash">
          <div className="refusal">
            <strong>CINQFLOW could not load</strong>
            <div className="note">
              The platform could not be reached. Nothing on the server changed — this is a
              transport failure, not a decision, and it has not been recorded as one.
            </div>
            <pre className="note mono wrap">{error.message || "unknown error"}</pre>
            <p>
              <button type="button" className="cited" onClick={() => reset()}>
                Try again
              </button>
            </p>
          </div>
        </main>
      </body>
    </html>
  );
}
