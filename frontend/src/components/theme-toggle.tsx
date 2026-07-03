"use client";

import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/lib/theme/context";
import { cn } from "@/lib/utils";

/*
 * ThemeToggle — flips light/dark. Two presentations from one component:
 *
 *  - default (icon): a compact square button for the desktop NavBar,
 *    sitting beside the account menu.
 *  - labeled: a full-width row for the mobile nav drawer, where a bare
 *    icon would read as ambiguous next to text nav items.
 *
 * The icon shows the theme you'd switch *to* (a moon in light mode,
 * a sun in dark mode), which is the conventional affordance.
 */

interface ThemeToggleProps {
  labeled?: boolean;
  className?: string;
}

export function ThemeToggle({ labeled = false, className }: ThemeToggleProps) {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";
  const nextLabel = isDark ? "Switch to light mode" : "Switch to dark mode";

  if (labeled) {
    return (
      <button
        type="button"
        onClick={toggle}
        className={cn(
          "focus-ring flex w-full items-center justify-between rounded-md border border-border bg-card px-3 py-2.5 text-sm text-foreground transition-colors hover:bg-surface",
          className
        )}
        aria-label={nextLabel}
      >
        <span className="flex items-center gap-2">
          {isDark ? (
            <Sun className="h-4 w-4" />
          ) : (
            <Moon className="h-4 w-4" />
          )}
          {isDark ? "Light mode" : "Dark mode"}
        </span>
        <span className="text-xs uppercase tracking-wider text-muted-foreground">
          {isDark ? "Dark" : "Light"}
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className={cn(
        "focus-ring inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-muted-foreground shadow-soft transition-colors hover:bg-surface hover:text-foreground",
        className
      )}
      aria-label={nextLabel}
      title={nextLabel}
    >
      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
