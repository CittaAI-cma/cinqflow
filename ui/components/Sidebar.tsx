import Link from "next/link";
import type { Destination, Navigation, Principal } from "@/lib/types";

/**
 * Navigation generated from the wave-activation manifest.
 *
 * Wave-1 destinations are ABSENT, not greyed out, because the server never
 * sends them. A disabled menu item is a promise the build cannot keep, and an
 * empty screen behind it is worse.
 *
 * `prominent` lifts a destination inside its group for this persona. It never
 * reorders the groups: two people looking at the same screen must be able to
 * give each other directions.
 */
export function Sidebar({
  principal,
  navigation,
}: {
  principal: Principal;
  navigation: Navigation | null;
}) {
  const groups: Record<string, Destination[]> = {};
  for (const destination of navigation?.destinations ?? []) {
    (groups[destination.group] ??= []).push(destination);
  }

  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="brand">CINQFLOW</div>
      <div className="wave">
        Wave {navigation?.active_wave ?? 0} · {principal.display_name}
        <br />
        {principal.roles.join(", ") || "no access assigned"}
      </div>

      {Object.entries(groups).map(([group, destinations]) => (
        <div key={group}>
          <div className="group">{group}</div>
          {destinations.map((d) => (
            <Link
              key={d.key}
              className={`dest${d.prominent ? " prominent" : ""}`}
              href={d.route}
              data-destination={d.key}
            >
              {d.label}
              <small>{d.answers}</small>
            </Link>
          ))}
        </div>
      ))}

      <div className="group">Session</div>
      <Link className="dest" href="/signin">
        Switch user
      </Link>
    </nav>
  );
}
