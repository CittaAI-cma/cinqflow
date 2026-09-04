import ApiUnreachable from "@/components/ui/ApiUnreachable";
import Link from "next/link";
import UsersTable from "@/components/admin/UsersTable";
import { PlusIcon } from "@/components/icons";
import { listRoles, listUsers } from "@/lib/users";

/** The register itself, so /admin/users and /admin/users/new can both render
 *  it — the modal route shows this behind the dialog, same pattern as
 *  `IngestionRegister`. */
export default async function UsersRegister() {
  try {
    const [users, roles] = await Promise.all([listUsers(), listRoles()]);
    return (
      <>
        <div className="admin-users-toolbar">
          <Link href="/admin/users/new" className="btn-dark">
            <PlusIcon size={15} /> New user
          </Link>
        </div>
        <UsersTable users={users} />
        <p className="dt-sub" style={{ marginTop: 10 }}>
          {roles.length} roles available — Business Analyst, Data Steward, Data Engineer,
          Operations, Approver, Administrator, Read-Only User.
        </p>
      </>
    );
  } catch (err) {
    console.error("listUsers/listRoles failed:", err);
    return <ApiUnreachable />;
  }
}
