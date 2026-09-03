"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import { CloseIcon } from "@/components/icons";

export type ToastTone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
  leaving: boolean;
}

interface ToastContextValue {
  push: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TTL_MS = 4500;
/** An error stays until dismissed: it is the one tone the user may need to
 *  read twice, or copy out of, and a timer that removes it mid-read is worse
 *  than a stack that needs one click. */
const TTL_BY_TONE: Record<ToastTone, number | null> = {
  success: TTL_MS,
  info: TTL_MS,
  error: null,
};
/** Matches `--dur-base` in globals.css; the node is removed after its exit. */
const EXIT_MS = 180;
/** Beyond this, older toasts retire early — a rapid retry loop must not build
 *  a column that covers the page it is reporting on. */
const MAX_VISIBLE = 4;

/** Mounted once, in `AppShell`. Ephemeral confirmation only — inline `.alert`
 *  blocks stay the permanent record on the page; a toast never carries
 *  information that would otherwise be lost when it disappears. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(0);

  const remove = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  /** Two-phase so the exit animation can play; the timer is the backstop for
   *  a browser that never fires `animationend` (reduced motion shortens the
   *  animation to ~0ms rather than removing it, so it still fires). */
  const dismiss = useCallback(
    (id: number) => {
      setItems((current) =>
        current.map((item) => (item.id === id ? { ...item, leaving: true } : item)),
      );
      setTimeout(() => remove(id), EXIT_MS);
    },
    [remove],
  );

  const push = useCallback(
    (message: string, tone: ToastTone = "success") => {
      const id = nextId.current++;
      setItems((current) => {
        const next = [...current, { id, message, tone, leaving: false }];
        return next.length > MAX_VISIBLE ? next.slice(next.length - MAX_VISIBLE) : next;
      });
      const ttl = TTL_BY_TONE[tone];
      if (ttl !== null) setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      {/* Two regions, because politeness is per-region and cannot be varied
          per message: a failure interrupts, a confirmation waits its turn. */}
      <div className="toast-viewport">
        <div className="toast-stack" role="status" aria-live="polite">
          {items
            .filter((item) => item.tone !== "error")
            .map((item) => (
              <Toast key={item.id} item={item} onDismiss={dismiss} />
            ))}
        </div>
        <div className="toast-stack" role="alert" aria-live="assertive">
          {items
            .filter((item) => item.tone === "error")
            .map((item) => (
              <Toast key={item.id} item={item} onDismiss={dismiss} />
            ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

/** A real `<button>`, not a clickable div: dismissal has to be reachable by
 *  keyboard and announced as an action, and the whole surface stays clickable
 *  because the button wraps it. */
function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  return (
    <div
      className={`toast toast-${item.tone}${item.leaving ? " leaving" : ""}`}
      onAnimationEnd={(event) => {
        if (item.leaving && event.animationName === "toast-out") onDismiss(item.id);
      }}
    >
      <span className="toast-message">{item.message}</span>
      <button
        type="button"
        className="toast-close"
        onClick={() => onDismiss(item.id)}
        aria-label={`Dismiss: ${item.message}`}
      >
        <CloseIcon size={14} />
      </button>
    </div>
  );
}

/** Throws outside a provider on purpose — a toast call that silently no-ops
 *  is a harder bug to notice than a crash during development. */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
