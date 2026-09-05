import ActionLauncher from "@/components/home/ActionLauncher";
import AnalystWorklist from "@/components/home/AnalystWorklist";
import PlatformAttention from "@/components/home/PlatformAttention";
import { requireUser } from "@/lib/auth";

/** Rendered per request so the greeting matches the hour it is read in — and so
 *  server and client never disagree about which greeting to show.
 *
 *  PR-4: the page branches on `user.persona` (derived from roles on the
 *  backend). Data Analyst: the worklist - what is waiting at a gate - then
 *  the recent runs. Data Platform: what needs attention - failed steps, dead
 *  letters, what is in flight, feeds by health. Both keep the greeting and the
 *  `ActionLauncher`; persona changes emphasis, never what is reachable. */
export const dynamic = "force-dynamic";

function greetingFor(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export default async function Home() {
  const user = await requireUser();
  const greeting = greetingFor(new Date().getHours());
  const name = user.display_name?.trim() || user.email;

  return (
    <div className="home">
      <h1 className="greeting">
        {greeting}, {name} <span aria-hidden="true">👋</span>
      </h1>
      {user.persona === "data_platform" ? (
        <PlatformAttention canRerun={user.capabilities.can_rerun_steps} />
      ) : (
        <AnalystWorklist />
      )}
      <ActionLauncher />
    </div>
  );
}
