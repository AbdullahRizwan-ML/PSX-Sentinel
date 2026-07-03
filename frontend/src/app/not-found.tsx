"use client";

import Link from "next/link";
import { Compass } from "lucide-react";

import { useAuth } from "@/lib/auth/context";
import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";

/*
 * Root not-found page. Next.js renders this for any unmatched route
 * (and when a page calls notFound()). Styled with the same Karachi Dusk
 * palette + Fraunces/Inter pairing as the rest of the app.
 *
 * The "back" destination depends on session: a signed-in user goes to
 * /dashboard, everyone else to the landing page. Auth state is read the
 * same way every other client route reads it (useAuth), so there's no
 * new pattern here. While auth is still resolving we default the link to
 * "/", which is always safe.
 */

export default function NotFound() {
  const { user, loading } = useAuth();
  const backHref = !loading && user ? "/dashboard" : "/";
  const backLabel = !loading && user ? "Back to dashboard" : "Back to home";

  return (
    <main className="relative min-h-screen">
      <header className="container flex h-16 items-center justify-between">
        <Brand size="md" asLink={false} />
        <ThemeToggle />
      </header>

      <div className="container grid min-h-[70vh] place-items-center">
        <div className="flex max-w-md flex-col items-center text-center animate-fade-in">
          <div className="grid h-14 w-14 place-items-center rounded-full bg-surface text-primary">
            <Compass className="h-6 w-6" />
          </div>
          <p className="mt-6 font-display text-display-1 leading-none text-foreground">
            404
          </p>
          <h1 className="mt-4 font-display text-xl text-foreground">
            This page wandered off
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            The page you were looking for doesn&apos;t exist, or the ticker
            isn&apos;t one we cover. PSX Sentinel tracks the KSE-30 universe
            — check the dashboard for the full list.
          </p>
          <div className="mt-6">
            <Button asChild size="lg">
              <Link href={backHref}>{backLabel}</Link>
            </Button>
          </div>
        </div>
      </div>
    </main>
  );
}
