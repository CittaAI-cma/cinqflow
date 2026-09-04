"use client";

import { useRouter } from "next/navigation";
import CreateUserForm from "@/components/admin/CreateUserForm";
import Modal from "@/components/ui/Modal";
import { UsersIcon } from "@/components/icons";
import type { Role } from "@/lib/users";

/** Route-driven: lives at /admin/users/new, so it's linkable and survives a
 *  reload, and closing it navigates back to the list. Same pattern as
 *  AddIngestionModal. */
export default function CreateUserModal({ roles }: { roles: Role[] }) {
  const router = useRouter();
  const close = () => router.push("/admin/users");

  return (
    <Modal title="Create user" badge={<UsersIcon size={18} />} onClose={close}>
      <CreateUserForm roles={roles} onCancel={close} />
    </Modal>
  );
}
