"use client";

/*
 * ThemeProvider — light/dark theme state for the whole app.
 *
 * Approach: toggles the `.dark` class on <html>, matching Tailwind's
 * `darkMode: ["class"]` config and the existing `.dark { ... }` token
 * block in globals.css. We deliberately do NOT introduce a second
 * mechanism (no `data-theme` attribute) so there's exactly one source
 * of truth the CSS keys off.
 *
 * First-paint flash is handled by an inline script in the root layout
 * <head> that sets the class before React hydrates (see app/layout.tsx).
 * This provider then reconciles its React state from whatever class the
 * script already applied, so the toggle button reflects reality on mount
 * without causing a hydration mismatch (both server and first client
 * render start from "light", then this effect corrects it post-mount).
 *
 * Persistence: localStorage key `psx-theme`. First-time visitors with no
 * stored choice fall back to `prefers-color-scheme`.
 */

import * as React from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "psx-theme";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

function applyThemeClass(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Start from "light" on both server and first client render to avoid a
  // hydration mismatch; the mount effect below corrects it immediately.
  const [theme, setThemeState] = React.useState<Theme>("light");

  React.useEffect(() => {
    // The inline no-flash script already set the class from
    // localStorage / prefers-color-scheme. Read it back as the truth.
    const isDark = document.documentElement.classList.contains("dark");
    setThemeState(isDark ? "dark" : "light");
  }, []);

  const setTheme = React.useCallback((t: Theme) => {
    setThemeState(t);
    applyThemeClass(t);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      // localStorage can throw in private mode / disabled storage —
      // theme still applies for the current session, just won't persist.
    }
  }, []);

  const toggle = React.useCallback(() => {
    setThemeState((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      applyThemeClass(next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* see setTheme */
      }
      return next;
    });
  }, []);

  const value = React.useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggle }),
    [theme, setTheme, toggle]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within <ThemeProvider>");
  }
  return ctx;
}
