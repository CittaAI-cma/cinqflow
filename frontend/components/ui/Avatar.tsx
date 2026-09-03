/** Initial-and-tint avatar. The tint is derived from the identifier so the same
 *  person is always the same colour, without storing anything. */

const TINTS = [
  "var(--acc)",
  "var(--cite)",
  "var(--gate)",
  "var(--ok)",
  "var(--proc)",
  "var(--missing)",
];

function tintFor(value: string): string {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) % 997;
  }
  return TINTS[hash % TINTS.length];
}

export default function Avatar({ name, size = 22 }: { name: string; size?: number }) {
  const initial = (name.trim()[0] ?? "?").toUpperCase();
  const tint = tintFor(name);

  return (
    <span
      className="avatar"
      style={{
        width: size,
        height: size,
        color: tint,
        background: `color-mix(in srgb, ${tint} 14%, var(--surf))`,
        fontSize: Math.round(size * 0.46),
      }}
      aria-hidden="true"
    >
      {initial}
    </span>
  );
}
