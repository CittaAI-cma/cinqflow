"use server";

import { redirect } from "next/navigation";
import { login } from "@/lib/auth";

export interface LoginState {
  error?: string;
}

export async function submitLogin(
  _previous: LoginState,
  form: FormData,
): Promise<LoginState> {
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!email || !password) {
    return { error: "Enter your email and password." };
  }

  const { error } = await login(email, password);
  if (error) return { error };

  const next = String(form.get("next") ?? "") || "/";
  // Never redirect off-site with a value that rode in on the query string.
  redirect(next.startsWith("/") ? next : "/");
}
