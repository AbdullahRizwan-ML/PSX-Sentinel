"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Brain,
  Coins,
  Hash,
  ShieldAlert,
  TrendingUp,
  TrendingDown,
  Sparkles,
  Clock,
} from "lucide-react";

import {
  getCompanyDetail,
  getLatestReport,
} from "@/lib/api/companies";
import type {
  CompanyDetailResponse,
  IntelligenceReportResponse,
} from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";

import { ConvictionDial } from "@/components/conviction-dial";
import { SignalBadge } from "@/components/signal-badge";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { AnalyzeButton } from "@/components/analyze-button";
import { Card, CardContent } from "@/components/ui/card";
import {
  cn,
  formatPct,
  formatPkr,
  formatPrice,
  formatRelativeTime,
} from "@/lib/utils";

interface PageData {
  company: CompanyDetailResponse;
  report: IntelligenceReportResponse | null;
}

export default function CompanyDetailPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = params.ticker?.toUpperCase() ?? "";

  const [data, setData] = React.useState<PageData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reportError, setReportError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    setError(null);
    setReportError(null);
    try {
      const company = await getCompanyDetail(ticker);
      let report: IntelligenceReportResponse | null = null;
      try {
        report = await getLatestReport(ticker);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // No report yet — totally fine, we'll show the empty-state CTA.
          report = null;
        } else if (err instanceof ApiError) {
          setReportError(err.message);
        } else {
          setReportError("Couldn't load report.");
        }
      }
      setData({ company, report });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(`No company found with ticker "${ticker}".`);
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Couldn't load this company.");
      }
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <Link
        href="/dashboard"
        className="focus-ring inline-flex items-center gap-1.5 rounded-md text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to dashboard
      </Link>

      {loading && <DetailSkeleton />}

      {!loading && error && (
        <ErrorState
          title="Couldn't load company"
          message={error}
          onRetry={load}
        />
      )}

      {!loading && !error && data && (
        <>
          <CompanyHeader company={data.company} report={data.report} />

          {reportError && (
            <ErrorState
              title="Couldn't load latest report"
              message={reportError}
              onRetry={load}
            />
          )}

          {data.report ? (
            <ReportBody
              report={data.report}
              ticker={data.company.ticker}
              onAnalyzed={(r) =>
                setData((d) => (d ? { ...d, report: r } : d))
              }
            />
          ) : (
            <EmptyState
              icon={<Sparkles className="h-5 w-5 text-primary" />}
              title="No intelligence report yet"
              description={
                "Run the 4-agent pipeline against the latest price, news, and " +
                "filing data for " +
                data.company.ticker +
                ". Takes ~5-15 seconds depending on data availability."
              }
              action={
                <AnalyzeButton
                  ticker={data.company.ticker}
                  size="lg"
                  label="Generate report"
                  onComplete={(r) =>
                    setData((d) => (d ? { ...d, report: r } : d))
                  }
                  onError={(m) => setReportError(m)}
                />
              }
              className="py-16"
            />
          )}
        </>
      )}
    </div>
  );
}

function CompanyHeader({
  company,
  report,
}: {
  company: CompanyDetailResponse;
  report: IntelligenceReportResponse | null;
}) {
  const changeColor =
    company.latest_change_pct === null || company.latest_change_pct === undefined
      ? "text-muted-foreground"
      : company.latest_change_pct > 0
      ? "text-bullish"
      : company.latest_change_pct < 0
      ? "text-bearish"
      : "text-muted-foreground";

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0">
        <div className="grid gap-6 p-6 md:grid-cols-[1fr_auto] md:items-center md:gap-10">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-display text-display-1 leading-none text-foreground">
                {company.ticker}
              </h1>
              {company.is_kse30 && (
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-primary">
                  KSE-30
                </span>
              )}
              {company.is_kmi30 && (
                <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">
                  KMI-30
                </span>
              )}
            </div>
            <p className="mt-2 text-lg text-foreground">{company.name}</p>
            <p className="mt-1 text-xs uppercase tracking-wider text-muted-foreground">
              {company.sector}
            </p>

            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat
                icon={<Coins className="h-3.5 w-3.5" />}
                label="Last close"
                value={formatPrice(company.latest_price)}
                sub={
                  <span className={cn("tabular-nums", changeColor)}>
                    {formatPct(company.latest_change_pct)}
                  </span>
                }
              />
              <Stat
                icon={<Hash className="h-3.5 w-3.5" />}
                label="Market cap"
                value={formatPkr(company.market_cap_pkr)}
              />
              <Stat
                icon={<TrendingUp className="h-3.5 w-3.5" />}
                label="Signal"
                value={
                  report ? (
                    <SignalBadge
                      signal={report.technical_signal}
                      size="sm"
                    />
                  ) : (
                    "—"
                  )
                }
              />
              <Stat
                icon={<Clock className="h-3.5 w-3.5" />}
                label="Last analyzed"
                value={
                  report
                    ? formatRelativeTime(report.generated_at)
                    : "Never"
                }
              />
            </div>
          </div>

          <div className="flex flex-col items-center md:border-l md:border-border md:pl-10">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
              Conviction
            </p>
            <div className="mt-2">
              <ConvictionDial
                score={
                  report?.conviction_score ??
                  company.latest_conviction_score ??
                  null
                }
                signal={report?.technical_signal}
                size="lg"
              />
            </div>
            {report && (
              <div className="mt-4">
                <AnalyzeButton
                  ticker={company.ticker}
                  variant="outline"
                  size="sm"
                  label="Re-run analysis"
                  onComplete={(r) => {
                    // Refresh the page-level state from parent via a custom event.
                    // We dispatch through a window event for simplicity here.
                    window.dispatchEvent(
                      new CustomEvent("psx:report-refreshed", { detail: r })
                    );
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 font-display text-xl tabular-nums leading-none text-foreground">
        {value}
      </div>
      {sub && <div className="mt-1 text-xs">{sub}</div>}
    </div>
  );
}

function ReportBody({
  report,
  ticker,
  onAnalyzed,
}: {
  report: IntelligenceReportResponse;
  ticker: string;
  onAnalyzed: (r: IntelligenceReportResponse) => void;
}) {
  // Listen for refresh events from the header's re-run button
  React.useEffect(() => {
    function handler(e: Event) {
      const ce = e as CustomEvent<IntelligenceReportResponse>;
      if (ce.detail?.ticker === ticker) onAnalyzed(ce.detail);
    }
    window.addEventListener("psx:report-refreshed", handler);
    return () => window.removeEventListener("psx:report-refreshed", handler);
  }, [ticker, onAnalyzed]);

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <CaseCard
          tone="bullish"
          icon={<TrendingUp className="h-4 w-4" />}
          title="The bull case"
          body={report.bull_case}
        />
        <CaseCard
          tone="bearish"
          icon={<TrendingDown className="h-4 w-4" />}
          title="The bear case"
          body={report.bear_case}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[2fr_3fr]">
        <RiskFactorsCard factors={report.risk_factors} />
        <MlSignalCard report={report} />
      </div>

      <ReportMetaStrip report={report} />
    </div>
  );
}

function CaseCard({
  tone,
  icon,
  title,
  body,
}: {
  tone: "bullish" | "bearish";
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <Card
      className={cn(
        "relative overflow-hidden",
        tone === "bullish" && "border-bullish/30",
        tone === "bearish" && "border-bearish/30"
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-0 top-0 h-0.5",
          tone === "bullish" ? "bg-bullish" : "bg-bearish"
        )}
      />
      <CardContent className="p-6">
        <div
          className={cn(
            "inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-medium uppercase tracking-wider",
            tone === "bullish"
              ? "bg-bullish-muted text-bullish"
              : "bg-bearish-muted text-bearish"
          )}
        >
          {icon}
          {title}
        </div>
        <p className="mt-4 whitespace-pre-line text-sm leading-relaxed text-foreground">
          {body}
        </p>
      </CardContent>
    </Card>
  );
}

function RiskFactorsCard({ factors }: { factors: string[] }) {
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <ShieldAlert className="h-3.5 w-3.5" />
          Risk factors
        </div>
        {factors.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No specific risk factors flagged.
          </p>
        ) : (
          <ul className="mt-4 space-y-2.5">
            {factors.map((f, i) => (
              <li
                key={i}
                className="flex gap-2.5 text-sm leading-relaxed text-foreground"
              >
                <span
                  aria-hidden
                  className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent"
                />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/*
 * Honest framing of the ML signal.
 *
 * The trained XGBoost model achieves ~39% test accuracy vs 33% random
 * baseline — a real but very weak edge. In production the Arbitrator
 * gates the ML contribution at max_prob > 0.55, and no live ticker has
 * cleared that gate yet (Phase 3 Session 3 build log). Because of that:
 *
 *  - We render the UP-class probability (the only field the report API
 *    exposes today) but explicitly label it as a low-confidence model
 *    output, not as a verdict.
 *  - The richer score_breakdown.ml_detail block (gate_passed, all three
 *    class probabilities, skip_reason) lives in the DB but is NOT in
 *    the current IntelligenceReportResponse schema, so the frontend
 *    can't show it without a backend schema change. Flagged honestly
 *    instead of faked.
 */
function MlSignalCard({ report }: { report: IntelligenceReportResponse }) {
  const prob = report.ml_beat_probability;
  const probPct = (prob * 100).toFixed(1);
  // The API only exposes the UP-class probability (legacy field name
  // ml_beat_probability, repurposed in Phase 3 Session 3). Without
  // DOWN/FLAT probs we can only confirm the 0.55 production gate when
  // UP itself clears it — for lower values the gate may still be passed
  // by another class, but we can't tell from this endpoint.
  const upClassClearsGate = prob > 0.55;
  const directionalLabel =
    prob > 0.5 ? "leaning up" : prob < 0.5 ? "leaning down" : "flat";

  return (
    <Card className="relative overflow-hidden">
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
            <Brain className="h-3.5 w-3.5" />
            ML signal
          </div>
          {!upClassClearsGate && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Low confidence
            </span>
          )}
        </div>

        <div className="mt-5 flex items-baseline gap-3">
          <span className="font-display text-4xl tabular-nums text-foreground">
            {probPct}%
          </span>
          <span className="text-sm text-muted-foreground">
            5-day UP-move probability ({directionalLabel})
          </span>
        </div>

        {/* Confidence bar with the 0.55 gate marker */}
        <div className="mt-5 space-y-2">
          <div className="relative h-2 overflow-hidden rounded-full bg-surface">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                prob > 0.55
                  ? "bg-bullish"
                  : prob < 0.45
                  ? "bg-bearish"
                  : "bg-neutral"
              )}
              style={{ width: `${Math.min(100, prob * 100)}%` }}
            />
            <div
              aria-hidden
              className="absolute top-0 h-full w-px bg-foreground/40"
              style={{ left: "55%" }}
              title="Production confidence gate"
            />
          </div>
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>0%</span>
            <span>Gate · 55%</span>
            <span>100%</span>
          </div>
        </div>

        <p className="mt-5 rounded-md bg-surface/80 p-3 text-xs leading-relaxed text-muted-foreground">
          <strong className="font-medium text-foreground">
            Honest read:
          </strong>{" "}
          the model trained on 5-day price direction beats random by ~6
          percentage points and never predicts the FLAT class. In
          production the Arbitrator only lets it move the conviction
          score when its top-class probability clears 55% — currently
          no live ticker does, so this probability is informational
          only, not actionable.
        </p>
      </CardContent>
    </Card>
  );
}

function ReportMetaStrip({ report }: { report: IntelligenceReportResponse }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-5 py-3 text-xs text-muted-foreground shadow-soft">
      <span>
        Report generated{" "}
        <span className="font-medium text-foreground">
          {formatRelativeTime(report.generated_at)}
        </span>{" "}
        for{" "}
        <span className="font-medium text-foreground">
          {new Date(report.report_date).toLocaleDateString("en-PK", {
            year: "numeric",
            month: "short",
            day: "numeric",
          })}
        </span>
      </span>
      <div className="flex items-center gap-4">
        <span>
          <span className="font-medium text-foreground tabular-nums">
            {report.total_tokens_used.toLocaleString()}
          </span>{" "}
          tokens
        </span>
        <span>
          <span className="font-medium text-foreground tabular-nums">
            {report.generation_time_seconds.toFixed(1)}s
          </span>{" "}
          runtime
        </span>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-48 animate-soft-pulse rounded-lg border border-border bg-card" />
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="h-56 animate-soft-pulse rounded-lg border border-border bg-card" />
        <div className="h-56 animate-soft-pulse rounded-lg border border-border bg-card" />
      </div>
      <div className="grid gap-6 lg:grid-cols-[2fr_3fr]">
        <div className="h-44 animate-soft-pulse rounded-lg border border-border bg-card" />
        <div className="h-44 animate-soft-pulse rounded-lg border border-border bg-card" />
      </div>
    </div>
  );
}
