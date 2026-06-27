"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { ChevronDown, LogOut } from "lucide-react";

import { useAuth } from "@/lib/auth/context";
import { Brand } from "@/components/brand";
import { cn } from "@/lib/utils";

export function NavBar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = React.useState(false);
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

  const links = [
    { href: "/dashboard", label: "Dashboard" },
  ];

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/85 backdrop-blur-md">
      <div className="container flex h-16 items-center justify-between gap-6">
        <div className="flex items-center gap-8">
          <Brand size="md" />
          <nav className="hidden items-center gap-1 sm:flex">
            {links.map((link) => {
              const active =
                pathname === link.href || pathname.startsWith(link.href + "/");
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-ring",
                    active
                      ? "bg-surface text-foreground"
                      : "text-muted-foreground hover:bg-surface hover:text-foreground"
                  )}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>

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
            <span className="hidden text-sm sm:inline">
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
    </header>
  );
}
