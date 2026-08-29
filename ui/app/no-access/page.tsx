/**
 * The person in no CINQFLOW group.
 *
 * A STATE, not an error. Treating them as an error hands a real employee a
 * broken application; this page hands them the one sentence that gets them
 * working: who to ask.
 */
export default function NoAccess() {
  return (
    <main>
      <h1>No access assigned</h1>
      <p className="lede">
        Your account is valid, but it is not in a CINQFLOW group yet.
      </p>
      <div className="card">
        <p>
          Contact your administrator to be assigned a role. Nothing is broken — there is
          simply nothing you are permitted to see until then, and the attempt has been
          recorded in the audit trail.
        </p>
      </div>
    </main>
  );
}
