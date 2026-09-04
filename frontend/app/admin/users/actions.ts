"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { requireRole } from "@/lib/auth";
import { createUser, setUserActive } from "@/lib/users";

export interface CreateUserState {
  error?: string;
}

export async function submitCreateUser(
  _previous: CreateUserState,
  form: FormData,
): Promise<CreateUserState> {
  await requireRole("administrator"); // defence in depth: the API enforces this too

  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  const displayName = String(form.get("display_name") ?? "").trim();
  const roles = form.getAll("roles").map(String);

  if (!email || !password) {
    return { error: "Email and password are required." };
  }
  if (password.length < 8) {
    return { error: "Password must be at least 8 characters." };
  }

  const { error } = await createUser({
    email,
    password,
    display_name: displayName || email,
    roles,
  });
  if (error) return { error };

  revalidatePath("/admin/users");
  redirect("/admin/users");
}

/** Bound per-row: `toggleActive.bind(null, user.id, !user.is_active)`, used
 *  directly as a <form action>. */
export async function toggleActive(userId: string, nextActive: boolean): Promise<void> {
  await requireRole("administrator");
  await setUserActive(userId, nextActive);
  revalidatePath("/admin/users");
}
