"use client";

/** The last resort: an error thrown by the root layout itself, before
 *  `AppShell` (and therefore the sidebar, the toast provider and the app's own
 *  stylesheet context) is mounted. Next replaces the entire document with
 *  this, so it has to carry its own <html>/<body> and cannot rely on any
 *  component or class defined inside the shell — hence the inline styles,
 *  which are the one place in this codebase they are the right call.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#ffffff",
          color: "#000000",
          fontFamily: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
        }}
      >
        <main style={{ maxWidth: 520, padding: 24 }} role="alert">
          <h1 style={{ fontSize: 20, margin: "0 0 8px" }}>CINQFLOW could not start.</h1>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: "#3f3f3b", margin: "0 0 16px" }}>
            The application shell itself failed to load, so nothing on this page
            is usable. No data was read or written. Reloading is safe.
          </p>
          {error.digest ? (
            <p
              style={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 12,
                color: "#75756e",
                margin: "0 0 16px",
              }}
            >
              Reference {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              background: "#1f2328",
              color: "#ffffff",
              border: 0,
              borderRadius: 8,
              padding: "9px 16px",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </main>
      </body>
    </html>
  );
}
