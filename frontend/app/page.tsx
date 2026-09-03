import ActionLauncher from "@/components/home/ActionLauncher";
import { USER_DISPLAY_NAME } from "@/lib/appConfig";

/** Rendered per request so the greeting matches the hour it is read in — and so
 *  server and client never disagree about which greeting to show. */
export const dynamic = "force-dynamic";

function greetingFor(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export default function Home() {
  const greeting = greetingFor(new Date().getHours());

  return (
    <div className="home">
      <h1 className="greeting">
        {greeting}, {USER_DISPLAY_NAME} <span aria-hidden="true">👋</span>
      </h1>
      <ActionLauncher />
    </div>
  );
}
