/** The API did not answer a server render.
 *
 *  Server component on purpose: every call site is already a server component
 *  that caught its own fetch, and this needs `process.env.NODE_ENV`.
 *
 *  The reason it exists is the copy. Three screens each hardcoded "start it
 *  with `make api`, and the worker with `make worker`" — advice that is only
 *  ever true in a bare local dev loop. In a deployed environment (Railway runs
 *  the API and worker as one process; compose runs them as services) there is
 *  no `make` to run, and telling an analyst to run one is worse than saying
 *  nothing: it sends them after a fix that does not exist. */
export default function ApiUnreachable({ what = "The API" }: { what?: string }) {
  const isLocalDev = process.env.NODE_ENV !== "production";

  return (
    <p className="alert error">
      <b>{what} is not responding.</b>{" "}
      {isLocalDev ? (
        <>
          If you are running this locally, start it with <span className="mono">make api</span>{" "}
          and the queue consumer with <span className="mono">make worker</span>.
        </>
      ) : (
        <>
          Nothing was lost — this screen only reads state. Retry in a moment; if it persists,
          the platform team needs to check the control plane.
        </>
      )}
    </p>
  );
}
