"use server";

import { redirect } from "next/navigation";
import { clearSession } from "@/lib/auth";

/** Stateless JWTs: this clears the session cookies Next.js holds and nothing
 *  more. A token already handed to something else stays valid until it
 *  expires (≤15 minutes by default) - see
 *  docs/blueprints/auth-and-user-management.md §5 for the follow-up
 *  (server-side revocation). */
export async function signOut(): Promise<void> {
  await clearSession();
  redirect("/login");
}
