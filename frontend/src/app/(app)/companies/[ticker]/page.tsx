"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Brain,
  Coins,
  Hash,
  Landmark,
  Newspaper,
  Percent,
  Scale,
  ShieldAlert,
  TrendingUp,
  TrendingDown,
  FileText,
  Sparkles,
  Clock,
  SearchX,
} from "lucide-react";

import {
  getCompanyDetail,
  getLatestReport,
} from "@/lib/api/companies";
import type {
  CompanyDetailResponse,
  IntelligenceReportResponse,
  MlDetail,
  ScoreBreakdown,
} from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";

import { ConvictionDial } from "@/components/conviction-dial";
import { SignalBadge } from "@/components/signal-badge";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { AnalyzeButton } from "@/components/analyze-button";
import { PriceChart } from "@/components/price-chart";
import { NewsList } from "@/components/news-list";
import { WatchlistStar } from "@/components/watchlist-star";
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
  const [notFound, setNotFound] = React.useState(false);
  const [reportError, setReportError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!ticker) return;
    setLoading(true);
    setError(null);
    setNotFound(false);
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
        // Unknown ticker — a distinct "not found" state (not a transient
        // failure), so we render a dedicated card with a way back to the
        // dashboard rather than a "try again" retry prompt.
        setNotFound(true);
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

      {!loading && notFound && (
        <EmptyState
          icon={<SearchX className="h-5 w-5 text-primary" />}
          title={`No company found for "${ticker}"`}
          description={
            "PSX Sentinel currently covers the KSE-30 universe. This " +
            "ticker isn't one of them — head back to the dashboard for " +
            "the full list of tracked companies."
          }
          action={
            <Link
              href="/dashboard"
              className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-soft transition-colors hover:bg-primary/90"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to dashboard
            </Link>
          }
          className="py-16"
        />
      )}

      {!loading && !notFound && error && (
        <ErrorState
          title="Couldn't load company"
          message={error}
          onRetry={load}
        />
      )}

      {!loading && !notFound && !error && data && (
        <>
          <CompanyHeader company={data.company} report={data.report} />

          <PriceChart ticker={data.company.ticker} />

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

          {/*
           * News list rendered after the report body. It's supporting
           * evidence for the bull/bear narrative the agents produced,
           * so it reads naturally as the next section down. When
           * there's no report yet, the component shows its own
           * "run analysis to see the relevance judgment" CTA — we
           * can't honestly show a relevant-articles list without an
           * LLM judgment to filter on, since the raw matched set is
           * known-noisy per docs/KNOWN_ISSUES.md.
           */}
          <NewsList
            ticker={data.company.ticker}
            report={data.report}
          />
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
            <div className="flex items-center gap-3">
              <h1 className="font-display text-display-1 leading-none text-foreground">
                {company.ticker}
              </h1>
              <WatchlistStar
                ticker={company.ticker}
                variant="header"
                stopPropagation={false}
              />
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

      {report.score_breakdown && (
        <ScoreBreakdownStrip
          breakdown={report.score_breakdown}
          finalScore={report.conviction_score}
        />
      )}

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
 * Two render paths share this component:
 *
 *  - Rich path (`report.score_breakdown.ml_detail` present): we have
 *    per-class probabilities, the real gate result, and an explicit
 *    skip_reason for runs where the model didn't speak. We render
 *    DOWN/FLAT/UP bars and an honest status panel that distinguishes
 *    "below confidence threshold" from "insufficient history" from
 *    "model unavailable" — all three currently produce a 0 ML
 *    contribution but mean very different things, and the user
 *    deserves to see which one applied.
 *
 *  - Legacy fallback (`ml_detail` absent): older reports persisted
 *    before this field was wired up, or cached responses still in
 *    Redis from before deploy. Falls back to the original UP-class
 *    probability rendering rather than crashing or showing a broken
 *    UI.
 *
 * The "weak signal" framing (~+6pp over random on a 3-class problem,
 * never predicts FLAT) is preserved in both paths.
 */
function MlSignalCard({ report }: { report: IntelligenceReportResponse }) {
  const detail = report.score_breakdown?.ml_detail ?? null;
  if (!detail) {
    return <MlSignalCardLegacy report={report} />;
  }
  return <MlSignalCardRich detail={detail} />;
}

interface MlStatusVisual {
  badge: string;
  badgeTone: "muted" | "bullish" | "bearish";
  headline: string;
  explanation: string;
}

function describeMlStatus(detail: MlDetail): MlStatusVisual {
  const gate = detail.confidence_threshold ?? 0.55;
  const gatePct = Math.round(gate * 100);
  const maxProbPct =
    detail.max_prob !== null ? (detail.max_prob * 100).toFixed(1) : null;

  if (detail.gate_passed) {
    const cls = detail.predicted_class ?? "?";
    const tone: MlStatusVisual["badgeTone"] =
      cls === "UP" ? "bullish" : cls === "DOWN" ? "bearish" : "muted";
    return {
      badge: "Above gate",
      badgeTone: tone,
      headline: `Predicting ${cls} (${maxProbPct ?? "?"}%)`,
      explanation:
        `Top-class probability cleared the ${gatePct}% production ` +
        `gate, so the model is contributing to the conviction score ` +
        `for this run. Still a weak base-rate edge — treat as a ` +
        `minor input, not a verdict.`,
    };
  }

  switch (detail.skip_reason) {
    case "below_confidence_threshold":
      return {
        badge: "Below gate",
        badgeTone: "muted",
        headline:
          `Top-class probability ${maxProbPct ?? "?"}%, below the ` +
          `${gatePct}% gate`,
        explanation:
          `The model produced a prediction (${detail.predicted_class ?? "?"})` +
          ` but its confidence is below the production threshold. The ` +
          `Arbitrator silences sub-gate predictions rather than letting ` +
          `a coin-flip vote nudge the conviction score — so this run ` +
          `contributes 0 from the ML term.`,
      };
    case "insufficient_history":
      return {
        badge: "No prediction",
        badgeTone: "muted",
        headline: "Not enough price history",
        explanation:
          `The feature build needs 252 trading days of trailing prices ` +
          `(roughly a year) to compute the 52-week range position. This ` +
          `ticker doesn't have that yet, so the ML term contributes 0 ` +
          `until enough history accumulates.`,
      };
    case "model_unavailable":
      return {
        badge: "No prediction",
        badgeTone: "muted",
        headline: "ML model unavailable",
        explanation:
          `The trained XGBoost model couldn't be loaded for this run. ` +
          `Other agents (technical, news, filings) still ran normally; ` +
          `the ML term simply contributes 0.`,
      };
    default:
      return {
        badge: "Low confidence",
        badgeTone: "muted",
        headline: "ML contribution silenced",
        explanation:
          `The Arbitrator chose not to apply the ML term for this run. ` +
          `See the build log for current gate status.`,
      };
  }
}

function MlSignalCardRich({ detail }: { detail: MlDetail }) {
  const probs = detail.probabilities ?? {};
  const upProb = probs.UP ?? 0;
  const downProb = probs.DOWN ?? 0;
  const flatProb = probs.FLAT ?? 0;
  const gate = detail.confidence_threshold ?? 0.55;
  const gatePct = (gate * 100).toFixed(0);
  const status = describeMlStatus(detail);
  const predicted = detail.predicted_class ?? null;
  const caveat =
    detail.model_caveat ??
    "Technical-only XGBoost; ~+6pp over random; never predicts FLAT.";

  return (
    <Card className="relative overflow-hidden">
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
            <Brain className="h-3.5 w-3.5" />
            ML signal
          </div>
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
              status.badgeTone === "bullish" &&
                "bg-bullish-muted text-bullish",
              status.badgeTone === "bearish" &&
                "bg-bearish-muted text-bearish",
              status.badgeTone === "muted" &&
                "bg-muted text-muted-foreground"
            )}
          >
            {status.badge}
          </span>
        </div>

        <p className="mt-4 text-sm text-foreground">{status.headline}</p>

        {/* Per-class probability bars — DOWN / FLAT / UP. */}
        <div className="mt-5 space-y-3">
          <ClassProbBar
            label="DOWN"
            tone="bearish"
            prob={downProb}
            gate={gate}
            highlighted={predicted === "DOWN"}
          />
          <ClassProbBar
            label="FLAT"
            tone="muted"
            prob={flatProb}
            gate={gate}
            highlighted={predicted === "FLAT"}
          />
          <ClassProbBar
            label="UP"
            tone="bullish"
            prob={upProb}
            gate={gate}
            highlighted={predicted === "UP"}
          />
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span>0%</span>
            <span>Gate · {gatePct}%</span>
            <span>100%</span>
          </div>
        </div>

        <p className="mt-5 rounded-md bg-surface/80 p-3 text-xs leading-relaxed text-muted-foreground">
          <strong className="font-medium text-foreground">
            Honest read:
          </strong>{" "}
          {status.explanation}{" "}
          <span className="block mt-1.5 text-muted-foreground/80">
            {caveat}
          </span>
        </p>

        {detail.as_of_date && (
          <p className="mt-3 text-[10px] uppercase tracking-wider text-muted-foreground">
            As of trading day {detail.as_of_date}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ClassProbBar({
  label,
  tone,
  prob,
  gate,
  highlighted,
}: {
  label: string;
  tone: "bullish" | "bearish" | "muted";
  prob: number;
  gate: number;
  highlighted: boolean;
}) {
  const pct = Math.max(0, Math.min(100, prob * 100));
  return (
    <div>
      <div className="flex justify-between text-[11px]">
        <span
          className={cn(
            "uppercase tracking-wider",
            highlighted ? "font-semibold text-foreground" : "text-muted-foreground"
          )}
        >
          {label}
          {highlighted && (
            <span className="ml-1.5 text-[10px] text-muted-foreground">
              (predicted)
            </span>
          )}
        </span>
        <span
          className={cn(
            "tabular-nums",
            highlighted ? "text-foreground" : "text-muted-foreground"
          )}
        >
          {pct.toFixed(1)}%
        </span>
      </div>
      <div className="relative mt-1 h-2 overflow-hidden rounded-full bg-surface">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            tone === "bullish" && "bg-bullish",
            tone === "bearish" && "bg-bearish",
            tone === "muted" && "bg-neutral"
          )}
          style={{ width: `${pct}%` }}
        />
        <div
          aria-hidden
          className="absolute top-0 h-full w-px bg-foreground/40"
          style={{ left: `${gate * 100}%` }}
          title="Production confidence gate"
        />
      </div>
    </div>
  );
}

/*
 * Legacy fallback. Used when score_breakdown.ml_detail is absent —
 * older reports persisted before Phase 4 Session 2, or Redis-cached
 * responses still alive from the previous deploy. Behaviour is
 * intentionally identical to the pre-Session-2 component.
 */
function MlSignalCardLegacy({
  report,
}: {
  report: IntelligenceReportResponse;
}) {
  const prob = report.ml_beat_probability;
  const probPct = (prob * 100).toFixed(1);
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

/*
 * Per-term score breakdown strip.
 *
 * The conviction score is a sum: 50 (base) + technical + news +
 * filing + ml, clamped to [0, 100]. Showing the four contributions
 * makes the math visible to the user and explains *why* a score
 * landed where it did — especially useful right now when news,
 * filing, and ml all contribute 0 for most tickers (see
 * docs/KNOWN_ISSUES.md), making the score appear "mysteriously"
 * clustered around 58.5.
 */
function ScoreBreakdownStrip({
  breakdown,
  finalScore,
}: {
  breakdown: ScoreBreakdown;
  finalScore: number;
}) {
  const terms: Array<{
    label: string;
    value: number;
    icon: React.ReactNode;
  }> = [
    {
      label: "Technical",
      value: breakdown.technical_contribution,
      icon: <TrendingUp className="h-3.5 w-3.5" />,
    },
    {
      label: "News",
      value: breakdown.news_contribution,
      icon: <Newspaper className="h-3.5 w-3.5" />,
    },
    {
      label: "Filings",
      value: breakdown.filing_contribution,
      icon: <FileText className="h-3.5 w-3.5" />,
    },
    {
      label: "ML",
      value: breakdown.ml_contribution,
      icon: <Brain className="h-3.5 w-3.5" />,
    },
  ];
  // Phase 5 Session 8 terms — only on reports generated since then.
  // Older reports carry null/undefined here; we omit the pills
  // entirely rather than render a fabricated 0.0 for a term that
  // didn't exist when the report was scored.
  if (typeof breakdown.fundamentals_contribution === "number") {
    terms.push({
      label: "Fundamentals",
      value: breakdown.fundamentals_contribution,
      icon: <Percent className="h-3.5 w-3.5" />,
    });
  }
  if (typeof breakdown.flow_contribution === "number") {
    terms.push({
      label: "Flows",
      value: breakdown.flow_contribution,
      icon: <Landmark className="h-3.5 w-3.5" />,
    });
  }
  const sum = terms.reduce((s, t) => s + t.value, 0);

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <Scale className="h-3.5 w-3.5" />
          How the conviction score adds up
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-3 text-sm">
          <ScoreTermPill label="Base" value={50} neutral />
          {terms.map((t) => (
            <React.Fragment key={t.label}>
              <span className="font-display text-muted-foreground">+</span>
              <ScoreTermPill
                label={t.label}
                value={t.value}
                icon={t.icon}
              />
            </React.Fragment>
          ))}
          <span className="font-display text-muted-foreground">=</span>
          <span className="font-display text-base tabular-nums text-foreground">
            {(50 + sum).toFixed(1)}
          </span>
          {Math.abs(50 + sum - finalScore) > 0.05 && (
            <span className="text-[11px] text-muted-foreground">
              (clamped to {finalScore.toFixed(1)})
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ScoreTermPill({
  label,
  value,
  icon,
  neutral,
}: {
  label: string;
  value: number;
  icon?: React.ReactNode;
  neutral?: boolean;
}) {
  const tone = neutral
    ? "muted"
    : value > 0
    ? "bullish"
    : value < 0
    ? "bearish"
    : "muted";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs",
        tone === "bullish" && "bg-bullish-muted text-bullish",
        tone === "bearish" && "bg-bearish-muted text-bearish",
        tone === "muted" && "bg-muted text-muted-foreground"
      )}
    >
      {icon}
      <span className="uppercase tracking-wider text-[10px]">{label}</span>
      <span className="tabular-nums font-medium">
        {value > 0 ? "+" : ""}
        {value.toFixed(1)}
      </span>
    </span>
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
