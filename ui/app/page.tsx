import { redirect } from "next/navigation";
import {
  AccessChanges,
  Arrived,
  AskShortcut,
  Feeds,
  loadHomeData,
  NeedsYou,
  RefusalsToday,
  Runs,
} from "@/components/home/slots";
import { RefusalNotice } from "@/components/Refusal";
import { MetricTile } from "@/components/ui/MetricTile";
import { attempt, isRefused, token } from "@/lib/api";
import type { Batch, Feed, HomeSlot, Principal } from "@/lib/types";

/**
 * Home, shaped by persona — composed from SLOTS, not from a ternary.
 *
 * The merge rule: persona shapes the home and the RANKING; it never shapes the
 * vocabulary or the depth. This file used to hold `roles.includes("engineer")
 * ? … : …`, which covered two of the three Wave-0 roles and made the third an
 * accident of the else-branch. The order now arrives from core/persona.py on
 * /api/me, so "what an Engineer sees first" is a server fact with a test
 * rather than a branch in a component.
 *
 * A slot whose wave has not activated is absent from that list — never a stub,
 * never a placeholder card. Same rule the navigation applies to Wave-1
 * destinations.
 */

/** The title for each slot. UI copy lives here; the RANK lives on the server,
 *  and the one-line subtitle is the slot's own `answers` string, so a screen
 *  and the manifest that justifies it cannot drift apart. */
const TITLES: Record<string, string> = {
  "needs-you": "What needs you",
  arrived: "What arrived",
  runs: "Runs",
  feeds: "Feeds",
  "ask-shortcut": "Ask in your own words",
  "refusals-today": "What the platform refused",
  "access-changes": "Who changed what",
};

function SlotBody({ slot, feeds, batches }: { slot: string; feeds: Feed[]; batches: Batch[] }) {
  switch (slot) {
    case "needs-you":
      return <NeedsYou batches={batches} />;
    case "arrived":
      return <Arrived batches={batches} />;
    case "runs":
      return <Runs batches={batches} />;
    case "feeds":
      return <Feeds feeds={feeds} />;
    case "ask-shortcut":
      return <AskShortcut />;
    case "refusals-today":
      return <RefusalsToday />;
    case "access-changes":
      return <AccessChanges />;
    default:
      // A slot the server ranked and this build cannot draw renders NOTHING.
      // A placeholder here would be exactly the stub the wave manifest exists
      // to prevent, one layer further down.
      return null;
  }
}

export default async function Home() {
  if (!(await token())) redirect("/signin");

  const me = await attempt<Principal>("/api/me");
  if (isRefused(me)) return <RefusalNotice refusal={me} />;
  if (!me.has_access) redirect("/no-access");

  const { feeds, batches, refusal } = await loadHomeData();
  if (refusal) return <RefusalNotice refusal={refusal} />;

  const slots = (me.home_slots ?? []).filter((slot) => slot.key in TITLES);
  // The page title IS the first slot's title, and the lede is that slot's own
  // one-line reason for existing. A persona-ranked home whose headline does
  // not match its first card is a home that reads as generic.
  const [lead, ...rest]: HomeSlot[] = slots;

  return (
    <>
      <h1>{lead ? TITLES[lead.key] : "CINQFLOW"}</h1>
      <p className="lede">{lead?.answers ?? "What is happening on the platform."}</p>

      <div className="grid">
        <MetricTile
          label="Feeds published"
          value={feeds.filter((f) => f.lifecycle_state === "published").length}
        />
        <MetricTile label="Runs in view" value={batches.length} />
        <MetricTile
          label="Needing attention"
          value={
            batches.filter((b) => b.status === "Needs Attention" || b.status === "Missing").length
          }
          tone="attention"
        />
      </div>

      {lead && <SlotBody slot={lead.key} feeds={feeds} batches={batches} />}

      {rest.map((slot) => (
        <section key={slot.key} aria-labelledby={`slot-${slot.key}`}>
          <h2 id={`slot-${slot.key}`}>{TITLES[slot.key]}</h2>
          <p className="note">{slot.answers}</p>
          <SlotBody slot={slot.key} feeds={feeds} batches={batches} />
        </section>
      ))}
    </>
  );
}
