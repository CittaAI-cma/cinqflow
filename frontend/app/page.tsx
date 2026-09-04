import ActionLauncher from "@/components/home/ActionLauncher";
import { requireUser } from "@/lib/auth";

/** Rendered per request so the greeting matches the hour it is read in — and so
 *  server and client never disagree about which greeting to show. */
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
      <ActionLauncher />
    </div>
  );
}
