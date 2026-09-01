import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Principal } from "@/lib/types";

/**
 * Users & Roles.
 *
 * CINQFLOW is never the source of truth for identity. This screen SHOWS what
 * the identity provider asserts; assigning access happens there. That is why
 * there is no "add user" button — not hidden, absent, because the verb does not
 * exist on the authn pin.
 */
export default async function Users() {
  const users = await attempt<Principal[]>("/api/users");
  if (isRefused(users)) return <RefusalNotice refusal={users} />;

  return (
    <>
      <h1>Users &amp; Roles</h1>
      <p className="lede">Who can do what. Read from the identity provider, never authored here.</p>

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Person</th>
              <th>Subject</th>
              <th>Roles</th>
              <th>Permitted actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr className="row" key={user.subject}>
                <td>{user.display_name}</td>
                <td className="mono">{user.subject}</td>
                <td>
                  {user.roles.join(", ") || (
                    <span className="uncited">no access assigned</span>
                  )}
                </td>
                <td className="note">{user.permitted_actions.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card note">
        An administrator manages access and cannot approve anything. The person who grants
        permissions being able to use them all is how segregation of duties dies.
      </div>
    </>
  );
}
