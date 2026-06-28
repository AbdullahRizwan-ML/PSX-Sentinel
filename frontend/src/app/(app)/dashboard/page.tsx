"use client";

import * as React from "react";
import { TrendingUp, TrendingDown, Activity, Star } from "lucide-react";

import {
  getCompanyDetail,
  listCompanies,
  getMarketSummary,
} from "@/lib/api/companies";
import type {
  CompanyDetailResponse,
  MarketSummaryResponse,
} from "@/lib/api/types";
import { ApiError } from "@/lib/api/client";

import { CompanyCard } from "@/components/company-card";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { useWatchlist } from "@/lib/watchlist/context";
import { cn, formatPct } from "@/lib/utils";

interface DashboardData {
  companies: CompanyDetailResponse[];
  market: MarketSummaryResponse | null;
}

type DashboardTab = "all" | "watchlist";

export default function DashboardPage() {
  const [data, setData] = React.useState<DashboardData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [tab, setTab] = React.useState<DashboardTab>("all");
  const watchlist = useWatchlist();

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // List the universe of tracked companies (10-ticker KSE-30 subset)
      const list = await listCompanies({ limit: 50 });

      // listCompanies returns CompanyResponse (no latest_price/conviction).
      // Fetch each company's detail in parallel so the dashboard cards show
      // real at-a-glance signal instead of a placeholder.
      const details = await Promise.all(
        list.items.map((c) =>
          getCompanyDetail(c.ticker).catch(() => null)
        )
      );
      const companies = details.filter(
        (d): d is CompanyDetailResponse => d !== null
      );

      let market: MarketSummaryResponse | null = null;
      try {
        market = await getMarketSummary();
      } catch {
        // market summary is supplementary — failure shouldn't blank the page
      }

      setData({ companies, market });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Couldn't load the dashboard.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
            Dashboard
          </p>
          <h1 className="mt-1 font-display text-display-2 text-foreground">
            Today on the floor
          </h1>
          <p className="mt-1.5 max-w-xl text-sm text-muted-foreground">
            Every company you're tracking, distilled into one conviction
            score by the agent pipeline. Click any card for the full report.
          </p>
        </div>
        {data?.market && (
          <div className="rounded-md border border-border bg-card px-4 py-2 text-xs text-muted-foreground shadow-soft">
            <span className="font-medium text-foreground">
              {data.market.total_companies}
            </span>{" "}
            companies · market as of{" "}
            <span className="font-medium text-foreground">
              {new Date(data.market.market_date).toLocaleDateString("en-PK", {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </span>
          </div>
        )}
      </div>

      {loading && <DashboardSkeleton />}

      {!loading && error && (
        <ErrorState
          title="Couldn't load companies"
          message={error}
          onRetry={load}
        />
      )}

      {!loading && !error && data && (
        <>
          {data.market && <MarketStrip market={data.market} />}

          <div className="mt-8 mb-3 flex items-end justify-between gap-4">
            <DashboardTabs
              activeTab={tab}
              onChange={setTab}
              watchlistCount={watchlist.tickerSet.size}
            />
            <span className="pb-1 text-xs text-muted-foreground">
              {tab === "all"
                ? `${data.companies.length} companies`
                : `${
                    data.companies.filter((c) =>
                      watchlist.tickerSet.has(c.ticker.toUpperCase())
                    ).length
                  } companies`}
            </span>
          </div>

          <CompanyGrid
            tab={tab}
            companies={data.companies}
            watchlistSet={watchlist.tickerSet}
            watchlistReady={watchlist.ready}
            onSwitchTab={setTab}
          />
        </>
      )}
    </div>
  );
}

function DashboardTabs({
  activeTab,
  onChange,
  watchlistCount,
}: {
  activeTab: DashboardTab;
  onChange: (next: DashboardTab) => void;
  watchlistCount: number;
}) {
  // Single-row segmented control. Keeps the visual weight low so the
  // company cards stay the dominant element. Mirrors the existing
  // "pill" pattern used for KSE-30 chips elsewhere on the page.
  return (
    <div
      role="tablist"
      aria-label="Dashboard filter"
      className="inline-flex rounded-full border border-border bg-card p-1 shadow-soft"
    >
      <TabButton
        active={activeTab === "all"}
        onClick={() => onChange("all")}
      >
        All companies
      </TabButton>
      <TabButton
        active={activeTab === "watchlist"}
        onClick={() => onChange("watchlist")}
        icon={
          <Star
            className={cn(
              "h-3.5 w-3.5",
              activeTab === "watchlist" ? "fill-accent text-accent" : ""
            )}
            strokeWidth={1.5}
          />
        }
      >
        My watchlist
        {watchlistCount > 0 && (
          <span
            className={cn(
              "ml-1.5 rounded-full px-1.5 text-[10px] tabular-nums",
              activeTab === "watchlist"
                ? "bg-primary-foreground/15 text-primary-foreground"
                : "bg-muted text-muted-foreground"
            )}
          >
            {watchlistCount}
          </span>
        )}
      </TabButton>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "focus-ring inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors",
        active
          ? "bg-primary text-primary-foreground shadow-soft"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {icon}
      {children}
    </button>
  );
}

function CompanyGrid({
  tab,
  companies,
  watchlistSet,
  watchlistReady,
  onSwitchTab,
}: {
  tab: DashboardTab;
  companies: CompanyDetailResponse[];
  watchlistSet: Set<string>;
  watchlistReady: boolean;
  onSwitchTab: (next: DashboardTab) => void;
}) {
  const filtered =
    tab === "all"
      ? companies
      : companies.filter((c) =>
          watchlistSet.has(c.ticker.toUpperCase())
        );

  // Watchlist tab + still loading membership: don't show "empty" yet,
  // it might just be in-flight. Small inline skeleton instead.
  if (tab === "watchlist" && !watchlistReady) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-[200px] animate-soft-pulse rounded-lg border border-border bg-card"
          />
        ))}
      </div>
    );
  }

  // Watchlist tab + empty: this is the designed state the prompt
  // specifically calls out — reuse the same EmptyState pattern the
  // company-detail "no report yet" CTA established in Session 1, so
  // the visual language is consistent across the app.
  if (tab === "watchlist" && filtered.length === 0) {
    return (
      <EmptyState
        icon={<Star className="h-5 w-5 text-accent" />}
        title="Your watchlist is empty"
        description={
          "Tap the star on any company card or the company detail " +
          "page to pin it here. Watchlist is per-user, syncs across " +
          "devices, and is how you'll get focused alerts later."
        }
        action={
          <button
            type="button"
            onClick={() => onSwitchTab("all")}
            className="focus-ring rounded-md bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Browse all companies
          </button>
        }
        className="py-16"
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {filtered.map((c) => (
        <CompanyCard
          key={c.ticker}
          ticker={c.ticker}
          name={c.name}
          sector={c.sector}
          isKse30={c.is_kse30}
          latestPrice={c.latest_price}
          latestChangePct={c.latest_change_pct}
          convictionScore={c.latest_conviction_score}
        />
      ))}
    </div>
  );
}

function MarketStrip({ market }: { market: MarketSummaryResponse }) {
  const gainer = market.top_gainers[0];
  const loser = market.top_losers[0];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <div className="rounded-lg border border-border bg-card p-4 shadow-soft">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <TrendingUp className="h-3.5 w-3.5 text-bullish" />
          Top gainer
        </div>
        {gainer ? (
          <div className="mt-2 flex items-baseline justify-between gap-2">
            <span className="font-display text-xl text-foreground">
              {gainer.ticker}
            </span>
            <span className="text-sm font-medium tabular-nums text-bullish">
              {formatPct(gainer.change_pct)}
            </span>
          </div>
        ) : (
          <div className="mt-2 text-sm text-muted-foreground">—</div>
        )}
      </div>
      <div className="rounded-lg border border-border bg-card p-4 shadow-soft">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <TrendingDown className="h-3.5 w-3.5 text-bearish" />
          Top loser
        </div>
        {loser ? (
          <div className="mt-2 flex items-baseline justify-between gap-2">
            <span className="font-display text-xl text-foreground">
              {loser.ticker}
            </span>
            <span className="text-sm font-medium tabular-nums text-bearish">
              {formatPct(loser.change_pct)}
            </span>
          </div>
        ) : (
          <div className="mt-2 text-sm text-muted-foreground">—</div>
        )}
      </div>
      <div className="rounded-lg border border-border bg-card p-4 shadow-soft">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <Activity className="h-3.5 w-3.5 text-primary" />
          Universe
        </div>
        <div className="mt-2 flex items-baseline justify-between gap-2">
          <span className="font-display text-xl text-foreground">
            {market.total_companies}
          </span>
          <span className="text-xs text-muted-foreground">
            companies tracked
          </span>
        </div>
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-[78px] animate-soft-pulse rounded-lg border border-border bg-card"
          />
        ))}
      </div>
      <div className="mt-8 mb-3 h-5 w-40 animate-soft-pulse rounded bg-surface" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-[200px] animate-soft-pulse rounded-lg border border-border bg-card"
          />
        ))}
      </div>
    </div>
  );
}
