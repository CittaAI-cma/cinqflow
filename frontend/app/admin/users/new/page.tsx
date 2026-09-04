import CreateUserModal from "@/components/admin/CreateUserModal";
import UsersRegister from "@/components/admin/UsersRegister";
import { listRoles } from "@/lib/users";
import { requireRole } from "@/lib/auth";

export const dynamic = "force-dynamic";
export const metadata = { title: "New user — Admin" };

export default async function NewUserPage() {
  await requireRole("administrator");
  const roles = await listRoles();

  return (
    <>
      <UsersRegister />
      <CreateUserModal roles={roles} />
    </>
  );
}
