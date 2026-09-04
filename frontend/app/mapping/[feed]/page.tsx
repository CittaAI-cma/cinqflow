import Link from "next/link";
import MappingPageBody from "@/components/MappingPageBody";

export const dynamic = "force-dynamic";

export default async function MappingPage({
  params,
  searchParams,
}: {
  params: Promise<{ feed: string }>;
  searchParams: Promise<{ v?: string; proposal?: string; limit?: string }>;
}) {
  const { feed } = await params;
  const { v, proposal, limit } = await searchParams;

  return (
    <>
      <p className="meta">
        <Link href="/data/intake">← Data Intake</Link>
      </p>

      <h2 style={{ marginTop: 12 }}>
        Mapping studio <span className="mono">{feed}</span>
      </h2>

      <MappingPageBody
        feed={feed}
        v={v}
        proposal={proposal}
        limit={limit}
        baseHref={`/mapping/${encodeURIComponent(feed)}`}
      />
    </>
  );
}
