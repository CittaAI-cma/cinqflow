export interface KpiItem {
  key: string;
  value: React.ReactNode;
  label: string;
  tone?: "ok" | "danger";
}

export default function Kpi({ items }: { items: KpiItem[] }) {
  return (
    <div className="kpi">
      {items.map((item) => (
        <div key={item.key} className={item.tone ?? undefined}>
          <b>{item.value}</b>
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  );
}
