import UsersRegister from "@/components/admin/UsersRegister";
import { requireRole } from "@/lib/auth";

export const dynamic = "force-dynamic";
export const metadata = { title: "Users — Admin" };

export default async function AdminUsersPage() {
  await requireRole("administrator");
  return <UsersRegister />;
}
