import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/api";

/**
 * SSO only. Nobody anonymous.
 *
 * At rung 0.5 the `authn` pin is the `static` adapter and the "token" is a
 * subject — which is why this page lists people rather than asking for a
 * password. There is no password field here and never will be: the platform
 * verifies a token somebody else issued, and holds no credential of its own.
 * At rung 1 this page redirects to Keycloak; at rung 3, to Entra. Same code
 * path, different discovery URL in the connection profile.
 */
const DEV_USERS = [
  { token: "dev-engineer@cinqcare.test", name: "Arun Menon", role: "Engineer" },
  { token: "dev-analyst@cinqcare.test", name: "Priya Nair", role: "Read-Only" },
  { token: "dev-admin@cinqcare.test", name: "Steve Mathews", role: "Administrator" },
  // Wave 1 (ADR-0022) and Wave 2 (CF-V2-E12-03) added four roles, then an
  // eighth, to `authn.py` and `profiles/dev-users.yaml` — kept in step with
  // each other by a test, but never with this list, so nobody could sign in
  // as any of them to see what their own screens look like. W2-33 is the
  // first story that needed one of these five to test a real permission
  // gate in a browser, which is what surfaced the gap.
  { token: "dev-ba@cinqcare.test", name: "Meera Iyer", role: "Business Analyst" },
  { token: "dev-steward@cinqcare.test", name: "Daniel Okafor", role: "Data Steward" },
  { token: "dev-platform@cinqcare.test", name: "Ravi Shankar", role: "Platform Engineer" },
  { token: "dev-approver@cinqcare.test", name: "Grace Lin", role: "Business Approver" },
  { token: "dev-operations@cinqcare.test", name: "Tomás Rivera", role: "Operations" },
  { token: "dev-nogroup@cinqcare.test", name: "Unassigned User", role: "no CINQFLOW group" },
];

async function signIn(formData: FormData) {
  "use server";
  const subject = String(formData.get("subject") ?? "");
  const jar = await cookies();
  jar.set(SESSION_COOKIE, subject, { httpOnly: true, sameSite: "lax", path: "/" });
  redirect("/");
}

export default function SignIn() {
  return (
    <main>
      <h1>Sign in to CINQFLOW</h1>
      <p className="lede">
        Single sign-on. Nobody touches the platform anonymously, and the platform holds no
        password of its own.
      </p>
      <div className="card">
        <div className="note" style={{ marginBottom: 12 }}>
          Rung 0.5 · <span className="mono">static</span> identity provider. Keycloak at rung 1,
          Entra at rung 3 — same code path, a different discovery URL in the connection profile.
        </div>
        {DEV_USERS.map((user) => (
          <form key={user.token} action={signIn} style={{ marginBottom: 8 }}>
            <input type="hidden" name="subject" value={user.token} />
            <button className="primary" data-signin={user.token} style={{ width: "100%" }}>
              {user.name} — {user.role}
            </button>
          </form>
        ))}
      </div>
    </main>
  );
}
