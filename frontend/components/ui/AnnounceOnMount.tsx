"use client";

import { useEffect } from "react";
import { useToast } from "@/lib/useToast";

const KEY_PREFIX = "toast:pending:";

/** Call from a form's `onSubmit`, before the server action fires — writes the
 *  toast that should appear once the *result* of this submit is on screen.
 *
 *  A plain `useFormStatus` effect inside the submitting form doesn't work
 *  here: `revalidatePath` swaps the form out for a read-only decision panel
 *  in the same transition that resolves the action, so the form can go
 *  straight from "pending" to "unmounted" without an intermediate commit
 *  where an effect could fire. Routing the message through `sessionStorage`
 *  lets whatever mounts *next* announce it instead. */
export function announceOnSubmit(key: string, message: string) {
  try {
    sessionStorage.setItem(KEY_PREFIX + key, message);
  } catch {
    // Storage blocked: the action still completes, it just goes unannounced.
  }
}

/** Drop into whatever renders once the awaited state is reached (e.g. the
 *  read-only decision panel). Fires the queued toast on mount, once, only if
 *  `announceOnSubmit` was actually called for this key in this tab. */
export default function AnnounceOnMount({ storageKey }: { storageKey: string }) {
  const { push } = useToast();

  useEffect(() => {
    try {
      const key = KEY_PREFIX + storageKey;
      const message = sessionStorage.getItem(key);
      if (message) {
        sessionStorage.removeItem(key);
        push(message, "success");
      }
    } catch {
      // Storage blocked: no toast, no crash.
    }
    // Intentionally mount-only: this announces the one submit that just led here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
