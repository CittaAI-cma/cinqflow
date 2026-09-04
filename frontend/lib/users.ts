/** Admin user provisioning - thin wrappers over `lib/auth.ts`'s authenticated
 *  fetch helpers, same shape as `lib/api.ts`'s own endpoint functions. */

import { authGet, authMutate } from "@/lib/auth";

export interface Role {
  id: string;
  name: string;
  description: string;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
  created_ts: string;
  updated_ts: string | null;
}

export function listUsers() {
  return authGet<AdminUser[]>("/api/users");
}

export function listRoles() {
  return authGet<Role[]>("/api/roles");
}

export function createUser(body: {
  email: string;
  password: string;
  display_name: string;
  roles: string[];
}) {
  return authMutate<AdminUser>("/api/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function setUserActive(userId: string, isActive: boolean) {
  return authMutate<AdminUser>(`/api/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}
