"use client";

import * as React from "react";
import { TrendingUp, TrendingDown, Activity } from "lucide-react";

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
import { ErrorState } from "@/components/error-state";
import { formatPct } from "@/lib/utils";

interface DashboardData {
  companies: CompanyDetailResponse[];
  market: MarketSummaryResponse | null;
}

export default function DashboardPage() {
  const [data, setData] = React.useState<DashboardData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

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

          <div className="mt-8 mb-3 flex items-baseline justify-between">
            <h2 className="font-display text-lg text-foreground">
              Tracked universe
            </h2>
            <span className="text-xs text-muted-foreground">
              {data.companies.length} companies
            </span>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.companies.map((c) => (
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
        </>
      )}
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
