"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import { submitCreateUser, type CreateUserState } from "@/app/admin/users/actions";
import Checkbox from "@/components/ui/Checkbox";
import FormField from "@/components/ui/FormField";
import type { Role } from "@/lib/users";

function Footer({ onCancel }: { onCancel: () => void }) {
  const { pending } = useFormStatus();
  return (
    <div className="modal-footer">
      <button type="button" className="btn-outline" onClick={onCancel} disabled={pending}>
        Cancel
      </button>
      <button type="submit" className="btn-dark" disabled={pending}>
        {pending ? "Creating…" : "Create user"}
      </button>
    </div>
  );
}

export default function CreateUserForm({
  roles,
  onCancel,
}: {
  roles: Role[];
  onCancel: () => void;
}) {
  const [state, action] = useActionState<CreateUserState, FormData>(submitCreateUser, {});
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());

  function toggleRole(name: string, checked: boolean) {
    setSelectedRoles((current) => {
      const next = new Set(current);
      if (checked) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  return (
    <form action={action}>
      <div className="form-section">
        <div className="form-grid">
          <FormField label="Email" htmlFor="email" required>
            <input
              id="email"
              name="email"
              type="email"
              required
              autoFocus
              placeholder="name@cinqcare.com"
            />
          </FormField>

          <FormField label="Display name" htmlFor="display_name" hint="Defaults to the email">
            <input id="display_name" name="display_name" type="text" placeholder="Jordan Lee" />
          </FormField>

          <FormField
            label="Password"
            htmlFor="password"
            required
            hint="At least 8 characters — shared with the new user directly."
            span
          >
            <input
              id="password"
              name="password"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
            />
          </FormField>

          <FormField label="Roles" span>
            <div className="role-check-list">
              {roles.map((role) => (
                <label key={role.id} className="role-check-row">
                  <Checkbox
                    id={`role-${role.name}`}
                    name="roles"
                    value={role.name}
                    checked={selectedRoles.has(role.name)}
                    onChange={(checked) => toggleRole(role.name, checked)}
                    label={role.description}
                  />
                  {role.description}
                </label>
              ))}
            </div>
          </FormField>
        </div>

        {state.error ? (
          <p className="field-error" role="alert" style={{ padding: "0 22px 12px" }}>
            {state.error}
          </p>
        ) : null}
      </div>

      <Footer onCancel={onCancel} />
    </form>
  );
}
