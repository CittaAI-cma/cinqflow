import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { attempt, isRefused, token } from "@/lib/api";
import type { Navigation, Principal } from "@/lib/types";

export const metadata: Metadata = {
  title: "CINQFLOW",
  description: "Landing to Silver Raw, and the platform explaining itself.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const signedIn = (await token()) !== null;
  const me = signedIn ? await attempt<Principal>("/api/me") : null;
  const nav = signedIn ? await attempt<Navigation>("/api/navigation") : null;

  const principal = me && !isRefused(me) ? me : null;
  const navigation = nav && !isRefused(nav) ? nav : null;

  return (
    <html lang="en">
      <body>
        {principal ? (
          <div className="shell">
            <Sidebar principal={principal} navigation={navigation} />
            <main>{children}</main>
          </div>
        ) : (
          <main>{children}</main>
        )}
      </body>
    </html>
  );
}
