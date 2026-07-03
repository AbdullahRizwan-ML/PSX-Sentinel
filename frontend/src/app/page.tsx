"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, ArrowRight, Brain, Newspaper, Scale, TrendingUp } from "lucide-react";

import { useAuth } from "@/lib/auth/context";
import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConvictionDial } from "@/components/conviction-dial";
import { Button } from "@/components/ui/button";

/*
 * Unauthenticated landing page at "/".
 *
 * This is the first thing a recruiter or a not-yet-logged-in visitor
 * sees. It explains what PSX Sentinel is (framing pulled from CLAUDE.md's
 * "What this is" — deliberately not embellished marketing copy) and
 * routes to login / register. It is static: no backend calls, no fake
 * per-ticker data. The ConvictionDial is featured decoratively with an
 * illustrative static value, clearly labelled as a sample, so it reads
 * as "here's the signature output" without implying it's a live reading.
 *
 * A logged-in visitor is bounced straight to the dashboard — the same
 * auth-state check the (app) layout and the login page use.
 */

const AGENTS = [
  {
    icon: <TrendingUp className="h-4 w-4" />,
    name: "Trend analyzer",
    blurb: "Reads price action — moving averages, momentum, RSI.",
  },
  {
    icon: <Newspaper className="h-4 w-4" />,
    name: "News synthesizer",
    blurb: "Judges whether headlines are genuinely about the company.",
  },
  {
    icon: <Brain className="h-4 w-4" />,
    name: "Filing skeptic",
    blurb: "Reads corporate disclosures with a critical eye.",
  },
  {
    icon: <Scale className="h-4 w-4" />,
    name: "Arbitrator",
    blurb: "Weighs the disagreement into one conviction score.",
  },
];

export default function LandingPage() {
  const router = useRouter();
  const { user, loading } = useAuth();

  React.useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard");
    }
  }, [loading, user, router]);

  // While auth resolves, or if we're about to redirect a logged-in user,
  // show a minimal loader rather than flashing the marketing page.
  if (loading || user) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      </div>
    );
  }

  return (
    <main className="relative min-h-screen overflow-hidden">
      <header className="container flex h-16 items-center justify-between">
        <Brand size="md" asLink={false} />
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link
            href="/login"
            className="focus-ring rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            Sign in
          </Link>
        </div>
      </header>

      <div className="container grid items-center gap-12 py-16 lg:grid-cols-[1.1fr_0.9fr] lg:py-24">
        {/* Left: the pitch */}
        <div className="animate-fade-in">
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
            Pakistan Stock Exchange · AI intelligence
          </p>
          <h1 className="mt-4 font-display text-display-1 leading-[1.05] text-foreground">
            One conviction score.<br />
            <span className="italic text-primary">Four agents.</span>{" "}
            Zero noise.
          </h1>
          <p className="mt-6 max-w-xl text-base leading-relaxed text-muted-foreground">
            PSX Sentinel combines an ML price-direction model with a
            four-agent autonomous research pipeline over KSE-30 tickers.
            A trend analyzer, a news synthesizer, a filing skeptic and an
            arbitrator each weigh in — then the system distills their
            disagreement into a single score you can read in a glance.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button asChild size="lg">
              <Link href="/login">
                Sign in
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link href="/register">Create an account</Link>
            </Button>
          </div>

          <p className="mt-6 text-xs text-muted-foreground">
            A portfolio project — built to read as production, not a demo.{" "}
            <Link
              href="https://github.com/AbdullahRizwan-ML/psx-sentinel"
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              View on GitHub →
            </Link>
          </p>
        </div>

        {/* Right: the signature dial + agent list */}
        <div className="animate-fade-in">
          <div className="rounded-2xl border border-border bg-card p-8 shadow-lift">
            <div className="flex flex-col items-center">
              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                Sample conviction
              </p>
              <div className="mt-3">
                <ConvictionDial score={58.5} signal="MILDLY_BULLISH" size="lg" />
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Illustrative reading — sign in for live scores.
              </p>
            </div>

            <div className="mt-8 space-y-3 border-t border-border pt-6">
              {AGENTS.map((a) => (
                <div key={a.name} className="flex items-start gap-3">
                  <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface text-primary">
                    {a.icon}
                  </span>
                  <div>
                    <div className="text-sm font-medium text-foreground">
                      {a.name}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {a.blurb}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
