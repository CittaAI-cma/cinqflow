"use client";

import { useFormStatus } from "react-dom";
import { toggleActive } from "@/app/admin/users/actions";
import DataTable, { type Column } from "@/components/ui/DataTable";
import Timestamp from "@/components/ui/Timestamp";
import type { AdminUser } from "@/lib/users";

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

export default function UsersTable({ users }: { users: AdminUser[] }) {
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
      render: (row) => (
        <span className="role-chip-list">
          {row.roles.length === 0 ? (
            <span className="role-chip">no role</span>
          ) : (
            row.roles.map((role) => (
              <span key={role} className="role-chip">
                {role.replace(/_/g, " ")}
              </span>
            ))
          )}
        </span>
      ),
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
