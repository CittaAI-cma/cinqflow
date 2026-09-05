"use client";

import { useState } from "react";
import { useFormStatus } from "react-dom";
import { updateRoles } from "@/app/admin/users/actions";
import Checkbox from "@/components/ui/Checkbox";
import type { Role } from "@/lib/users";

function SaveButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className="btn-dark" disabled={pending}>
      {pending ? "Saving…" : "Save roles"}
    </button>
  );
}

/** Inline role assignment for one user - the same checkbox list
 *  `CreateUserForm` uses at creation, now editable afterwards. Roles are what
 *  persona and capabilities derive from (`auth/persona.py`), so without this
 *  every bootstrap administrator stays administrator-only and nobody can sign
 *  a gate. Submits through `updateRoles` (admin-only at the API too). */
export default function RolesEditor({
  userId,
  current,
  roles,
}: {
  userId: string;
  current: string[];
  roles: Role[];
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set(current));

  function toggle(name: string, checked: boolean) {
    setSelected((now) => {
      const next = new Set(now);
      if (checked) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  if (!open) {
    return (
      <span className="role-chip-list">
        {current.length === 0 ? (
          <span className="role-chip">no role</span>
        ) : (
          current.map((role) => (
            <span key={role} className="role-chip">
              {role.replace(/_/g, " ")}
            </span>
          ))
        )}
        <button
          type="button"
          className="btn-outline"
          onClick={() => {
            setSelected(new Set(current));
            setOpen(true);
          }}
          aria-label="Edit roles"
        >
          Edit
        </button>
      </span>
    );
  }

  return (
    <form
      action={updateRoles.bind(null, userId)}
      onSubmit={() => setOpen(false)}
      className="role-check-list"
    >
      {roles.map((role) => (
        <label key={role.id} className="role-check-row">
          <Checkbox
            id={`roles-${userId}-${role.name}`}
            name="roles"
            value={role.name}
            checked={selected.has(role.name)}
            onChange={(checked) => toggle(role.name, checked)}
            label={role.description}
          />
          {role.description}
        </label>
      ))}
      <div className="run-processing-actions">
        <SaveButton />
        <button type="button" className="btn-outline" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
