"use client";

import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

/** Single source of truth for the theme so the sidebar switch and the rail
 *  button cannot disagree. Light is the default; dark is explicit and stored. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const current = document.documentElement.dataset.theme;
    if (current === "dark" || current === "light") setTheme(current);
  }, []);

  function apply(next: Theme) {
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Private browsing or blocked storage: the choice just does not persist.
    }
  }

  return {
    theme,
    apply,
    toggle: () => apply(theme === "dark" ? "light" : "dark"),
  };
}
