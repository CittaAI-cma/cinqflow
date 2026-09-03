"use client";

import { useEffect, useState } from "react";

/** Timestamp — a server-rendered instant that becomes local time after mount.
 *
 *  Why this exists: formatting a date with `getHours()`/`getDate()` (or
 *  `toLocaleString()`) reads the *runtime's* timezone. The server renders in
 *  the container's zone — UTC — and the browser re-renders in the viewer's,
 *  so the two HTML strings differ and React reports a hydration mismatch and
 *  throws the whole subtree away to re-render it on the client. That is not a
 *  cosmetic warning: a discarded subtree loses client state, which is exactly
 *  what "preserve user edits during refresh" must not do, and it costs a full
 *  re-render on every navigation to a table of timestamps.
 *
 *  So the server emits a stable, timezone-independent UTC string, and the
 *  local rendering is swapped in after mount, when only one runtime is
 *  involved. `<time dateTime>` keeps the machine-readable instant either way.
 */

function pad(part: number): string {
  return String(part).padStart(2, "0");
}

/** Deterministic on both sides of the wire: always UTC, never the host zone. */
function utcLabel(date: Date, withSeconds: boolean): string {
  const base =
    `${pad(date.getUTCMonth() + 1)}/${pad(date.getUTCDate())}/${date.getUTCFullYear()} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  return withSeconds ? `${base}:${pad(date.getUTCSeconds())} UTC` : `${base} UTC`;
}

function localLabel(date: Date, withSeconds: boolean): string {
  const base =
    `${pad(date.getMonth() + 1)}/${pad(date.getDate())}/${date.getFullYear()} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return withSeconds ? `${base}:${pad(date.getSeconds())}` : base;
}

export default function Timestamp({
  value,
  withSeconds = true,
  className,
}: {
  value: string;
  withSeconds?: boolean;
  className?: string;
}) {
  const date = new Date(value);
  const valid = !Number.isNaN(date.getTime());
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!valid) return <span className={className}>{value}</span>;

  const label = mounted ? localLabel(date, withSeconds) : utcLabel(date, withSeconds);

  return (
    <time
      dateTime={date.toISOString()}
      className={className}
      // The one render where the two differ is the hydration pass itself;
      // suppressing here is precise, rather than silencing a whole tree.
      suppressHydrationWarning
      title={mounted ? utcLabel(date, true) : undefined}
    >
      {label}
    </time>
  );
}
