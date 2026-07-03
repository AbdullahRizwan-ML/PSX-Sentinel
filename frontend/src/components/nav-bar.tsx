"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { ChevronDown, LogOut, Menu, X } from "lucide-react";

import { useAuth } from "@/lib/auth/context";
import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

const LINKS = [{ href: "/dashboard", label: "Dashboard" }];

export function NavBar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    if (menuOpen) document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  function isActive(href: string) {
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-md">
        <div className="container flex h-16 items-center justify-between gap-6">
          <div className="flex items-center gap-8">
            <Brand size="md" />
            {/* Desktop nav — hidden below the `sm` breakpoint (640px),
                where the hamburger drawer takes over instead. */}
            <nav className="hidden items-center gap-1 sm:flex">
              {LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-ring",
                    isActive(link.href)
                      ? "bg-surface text-foreground"
                      : "text-muted-foreground hover:bg-surface hover:text-foreground"
                  )}
                >
                  {link.label}
                </Link>
              ))}
            </nav>
          </div>

          {/* Desktop-only controls: theme toggle + account menu. Hidden on
              mobile, where they move into the drawer. */}
          <div className="hidden items-center gap-2 sm:flex">
            <ThemeToggle />
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                className="focus-ring inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm shadow-soft transition-colors hover:bg-surface"
                onClick={() => setMenuOpen((v) => !v)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
              >
                <span
                  className="grid h-7 w-7 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
                  aria-hidden
                >
                  {user?.full_name?.[0]?.toUpperCase() || "U"}
                </span>
                <span className="text-sm">
                  {user?.full_name?.split(" ")[0] || "Account"}
                </span>
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
              </button>

              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full mt-2 w-56 origin-top-right animate-fade-in overflow-hidden rounded-lg border border-border bg-card shadow-lift"
                >
                  <div className="border-b border-border px-3 py-2.5 text-sm">
                    <div className="font-medium text-foreground">
                      {user?.full_name || "—"}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {user?.email}
                    </div>
                    <div className="mt-1 inline-flex items-center rounded-full bg-surface px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                      {user?.subscription_tier || "free"}
                    </div>
                  </div>
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-surface"
                    onClick={() => {
                      setMenuOpen(false);
                      void logout();
                    }}
                  >
                    <LogOut className="h-4 w-4" />
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Mobile-only hamburger — visible only below `sm`. */}
          <button
            type="button"
            className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-foreground shadow-soft transition-colors hover:bg-surface sm:hidden"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            aria-haspopup="dialog"
            aria-expanded={drawerOpen}
          >
            <Menu className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/*
       * The drawer is rendered as a sibling of <header>, NOT inside it.
       * The header has `backdrop-blur-md`, and any `backdrop-filter`
       * establishes a containing block for position:fixed descendants —
       * which would clamp the drawer's `h-full` / `inset-0` to the 64px
       * header box instead of the viewport. Keeping it outside the header
       * lets the fixed panel + backdrop size against the viewport.
       */}
      <MobileDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        links={LINKS}
        isActive={isActive}
        user={user}
        onLogout={() => {
          setDrawerOpen(false);
          void logout();
        }}
      />
    </>
  );
}

function MobileDrawer({
  open,
  onClose,
  links,
  isActive,
  user,
  onLogout,
}: {
  open: boolean;
  onClose: () => void;
  links: { href: string; label: string }[];
  isActive: (href: string) => boolean;
  user: ReturnType<typeof useAuth>["user"];
  onLogout: () => void;
}) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  const closeBtnRef = React.useRef<HTMLButtonElement>(null);

  // Escape to dismiss + lock body scroll while open + focus the close
  // button on open (basic a11y hygiene, not a full focus trap).
  React.useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeBtnRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="sm:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close menu"
        className="fixed inset-0 z-40 bg-foreground/40 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />
      {/* Panel */}
      <div
        ref={panelRef}
        className="fixed right-0 top-0 z-50 flex h-full w-72 max-w-[85vw] flex-col border-l border-border bg-card shadow-lift animate-fade-in"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <Brand size="sm" asLink={false} />
          <button
            ref={closeBtnRef}
            type="button"
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface hover:text-foreground"
            onClick={onClose}
            aria-label="Close menu"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex flex-col gap-1 p-4">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              onClick={onClose}
              className={cn(
                "focus-ring rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                isActive(link.href)
                  ? "bg-surface text-foreground"
                  : "text-muted-foreground hover:bg-surface hover:text-foreground"
              )}
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="px-4">
          <ThemeToggle labeled />
        </div>

        {/* Account block pinned to the bottom of the drawer. */}
        <div className="mt-auto border-t border-border p-4">
          <div className="mb-3 text-sm">
            <div className="font-medium text-foreground">
              {user?.full_name || "—"}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {user?.email}
            </div>
            <div className="mt-1 inline-flex items-center rounded-full bg-surface px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
              {user?.subscription_tier || "free"}
            </div>
          </div>
          <button
            type="button"
            className="focus-ring flex w-full items-center gap-2 rounded-md border border-border bg-card px-3 py-2.5 text-left text-sm text-foreground transition-colors hover:bg-surface"
            onClick={onLogout}
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
