import type { Metadata } from "next";
import "./globals.css";
import { mono, sans } from "./fonts";
import { Sidebar } from "@/components/Sidebar";
import { attempt, isRefused, token } from "@/lib/api";
import type { Navigation, Principal } from "@/lib/types";

export const metadata: Metadata = {
  title: "CINQFLOW",
  description: "Landing to Silver Raw, and the platform explaining itself.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const signedIn = (await token()) !== null;
  const [me, nav] = signedIn
    ? await Promise.all([attempt<Principal>("/api/me"), attempt<Navigation>("/api/navigation")])
    : [null, null];

  const principal = me && !isRefused(me) ? me : null;
  const navigation = nav && !isRefused(nav) ? nav : null;

  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        {principal ? (
          <div className="shell">
            <a className="skip-link" href="#main">
              Skip to content
            </a>
            <Sidebar principal={principal} navigation={navigation} />
            <main id="main" tabIndex={-1}>
              {children}
            </main>
          </div>
        ) : (
          <main id="main">{children}</main>
        )}
      </body>
    </html>
  );
}
