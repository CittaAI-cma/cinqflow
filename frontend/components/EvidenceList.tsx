/** Visual treatment for cited evidence strings. Styled like the console's citation
 *  chip, but not yet wired to a resolver — no such API exists on this slice. */
export default function EvidenceList({ items }: { items: string[] }) {
  if (!items.length) return <span className="unc">—</span>;
  return (
    <span className="evidence-list">
      {items.map((item) => (
        <span key={item} className="evidence-chip">
          {item}
        </span>
      ))}
    </span>
  );
}
