// Self-hosted, latin subset, variable. Committed as .woff2 rather than pulled
// through next/font/google, because next/font/google needs network AT BUILD
// TIME and this platform has to build inside an enterprise network that may
// not have it. Deterministic, offline, and no layout shift.
import localFont from "next/font/local";

export const sans = localFont({
  src: "./fonts/Inter-Variable.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-sans",
  // The stack a user sees for the ~80ms before the face lands. Metrics are
  // close enough to Inter that swap is not a visible jolt.
  fallback: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
});

export const mono = localFont({
  src: "./fonts/JetBrainsMono-Variable.woff2",
  weight: "400 700",
  display: "swap",
  variable: "--font-mono",
  fallback: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
});
