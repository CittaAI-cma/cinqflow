"use client";

import { useFormStatus } from "react-dom";
import { toggleActive } from "@/app/admin/users/actions";
import RolesEditor from "@/components/admin/RolesEditor";
import DataTable, { type Column } from "@/components/ui/DataTable";
import Timestamp from "@/components/ui/Timestamp";
import type { AdminUser, Role } from "@/lib/users";

function ToggleActiveButton({ user }: { user: AdminUser }) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      className="btn-outline"
      disabled={pending}
      aria-label={`${user.is_active ? "Deactivate" : "Reactivate"} ${user.email}`}
    >
      {pending ? "…" : user.is_active ? "Deactivate" : "Reactivate"}
    </button>
  );
}

export default function UsersTable({ users, roles }: { users: AdminUser[]; roles: Role[] }) {
  const columns: Column<AdminUser>[] = [
    {
      key: "email",
      header: "Email",
      sortable: true,
      value: (row) => row.email,
      render: (row) => (
        <div>
          <div>{row.email}</div>
          <div className="dt-sub">{row.display_name}</div>
        </div>
      ),
    },
    {
      key: "roles",
      header: "Roles",
      render: (row) => <RolesEditor userId={row.id} current={row.roles} roles={roles} />,
    },
    {
      key: "status",
      header: "Status",
      sortable: true,
      value: (row) => (row.is_active ? 1 : 0),
      render: (row) => (
        <span className={`user-status-pill ${row.is_active ? "active" : "inactive"}`}>
          {row.is_active ? "Active" : "Deactivated"}
        </span>
      ),
    },
    {
      key: "created_ts",
      header: "Created",
      sortable: true,
      value: (row) => row.created_ts,
      render: (row) => <Timestamp value={row.created_ts} withSeconds={false} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (row) => (
        <form action={toggleActive.bind(null, row.id, !row.is_active)}>
          <ToggleActiveButton user={row} />
        </form>
      ),
    },
  ];

  return (
    <DataTable
      rows={users}
      columns={columns}
      rowKey={(row) => row.id}
      initialSort={{ key: "created_ts", dir: "desc" }}
      emptyMessage="No users yet — create the first one."
    />
  );
}
