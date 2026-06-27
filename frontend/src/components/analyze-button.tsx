"use client";

import * as React from "react";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { triggerAnalysis } from "@/lib/api/companies";
import { ApiError } from "@/lib/api/client";
import type { IntelligenceReportResponse } from "@/lib/api/types";

interface AnalyzeButtonProps {
  ticker: string;
  variant?: "default" | "accent" | "outline";
  size?: "default" | "sm" | "lg";
  label?: string;
  onComplete?: (report: IntelligenceReportResponse) => void;
  onError?: (message: string) => void;
}

/*
 * Calls POST /companies/{ticker}/analyze which runs the 4-agent pipeline
 * synchronously and returns the saved report. Takes ~5-15s depending on
 * how many agents skip their LLM call. Shows a stage indicator while
 * running so the user knows what's happening — silence on a 15s request
 * looks like a freeze.
 */

const STAGES = [
  "Trend analyzer",
  "News synthesizer",
  "Filing skeptic",
  "Arbitrator",
] as const;

export function AnalyzeButton({
  ticker,
  variant = "accent",
  size = "default",
  label = "Run analysis",
  onComplete,
  onError,
}: AnalyzeButtonProps) {
  const [running, setRunning] = React.useState(false);
  const [stage, setStage] = React.useState(0);

  React.useEffect(() => {
    if (!running) return;
    setStage(0);
    const interval = window.setInterval(() => {
      setStage((s) => Math.min(s + 1, STAGES.length - 1));
    }, 3000);
    return () => window.clearInterval(interval);
  }, [running]);

  async function onClick() {
    if (running) return;
    setRunning(true);
    try {
      const report = await triggerAnalysis(ticker);
      onComplete?.(report);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.status === 429
            ? "Analysis already running for this ticker. Give it a moment."
            : err.message
          : "Analysis failed — please try again.";
      onError?.(msg);
    } finally {
      setRunning(false);
      setStage(0);
    }
  }

  return (
    <div className="inline-flex flex-col items-start gap-2">
      <Button
        type="button"
        variant={variant}
        size={size}
        onClick={onClick}
        disabled={running}
      >
        {running ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
        {running ? "Analyzing…" : label}
      </Button>
      {running && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {STAGES.map((s, i) => (
            <span
              key={s}
              className={
                i < stage
                  ? "text-foreground"
                  : i === stage
                  ? "font-medium text-primary"
                  : "text-muted-foreground/50"
              }
            >
              {s}
              {i < STAGES.length - 1 && (
                <span className="px-1 text-muted-foreground/40">→</span>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
