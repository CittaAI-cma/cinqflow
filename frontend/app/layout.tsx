import type { Metadata } from "next";
import { Instrument_Sans, IBM_Plex_Mono } from "next/font/google";
import AppShell from "@/components/shell/AppShell";
import { BRAND_NAME } from "@/lib/appConfig";
import "./globals.css";

const sans = Instrument_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: BRAND_NAME,
  description: "Data governance, pipelines and operations for the CINQFLOW platform",
};

/** Applies a remembered theme before first paint so an opted-in dark session
 *  never flashes the light surface. Light is the default when nothing is stored.
 *
 *  This runs before React hydrates and writes `data-theme` onto <html>, which
 *  the server could not have known about — hence `suppressHydrationWarning` on
 *  <html> below. It suppresses one element's attributes, not the tree. */
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("theme");if(t==="dark"||t==="light"){document.documentElement.dataset.theme=t}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
