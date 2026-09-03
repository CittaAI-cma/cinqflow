"use client";

import { MoonIcon, SunIcon } from "@/components/icons";
import { useTheme } from "@/lib/useTheme";

/** The console ships light. Dark is opt-in and explicit — it is written to
 *  `data-theme` on <html> and remembered, never inferred from the OS, so the
 *  default surface is the white one the design calls for. */
export default function ThemeToggle() {
  const { theme, apply } = useTheme();

  return (
    <div className="nav-footer-block">
      <span className="nav-section-label">Theme</span>
      <div className="theme-switch" role="group" aria-label="Color theme">
        <button
          type="button"
          className={`theme-option${theme === "light" ? " on" : ""}`}
          aria-pressed={theme === "light"}
          onClick={() => apply("light")}
        >
          <SunIcon size={14} /> Light
        </button>
        <button
          type="button"
          className={`theme-option${theme === "dark" ? " on" : ""}`}
          aria-pressed={theme === "dark"}
          onClick={() => apply("dark")}
        >
          <MoonIcon size={14} /> Dark
        </button>
      </div>
    </div>
  );
}
