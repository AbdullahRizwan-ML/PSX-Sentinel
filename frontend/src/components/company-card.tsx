import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { ConvictionDial } from "@/components/conviction-dial";
import { WatchlistStar } from "@/components/watchlist-star";
import { cn, formatPct, formatPrice } from "@/lib/utils";

interface CompanyCardProps {
  ticker: string;
  name: string;
  sector: string;
  isKse30: boolean;
  latestPrice?: number | null;
  latestChangePct?: number | null;
  convictionScore?: number | null;
  technicalSignal?: string | null;
}

export function CompanyCard({
  ticker,
  name,
  sector,
  isKse30,
  latestPrice,
  latestChangePct,
  convictionScore,
  technicalSignal,
}: CompanyCardProps) {
  const changeColor =
    latestChangePct === null || latestChangePct === undefined
      ? "text-muted-foreground"
      : latestChangePct > 0
      ? "text-bullish"
      : latestChangePct < 0
      ? "text-bearish"
      : "text-muted-foreground";

  return (
    <Link
      href={`/companies/${ticker}`}
      className="group focus-ring block rounded-lg border border-border bg-card p-5 shadow-soft transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-display text-xl tracking-tight text-foreground">
              {ticker}
            </span>
            {isKse30 && (
              <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-primary">
                KSE-30
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
            {name}
          </p>
          <p className="mt-0.5 text-[11px] uppercase tracking-wider text-muted-foreground/80">
            {sector}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <WatchlistStar ticker={ticker} variant="card" />
          <ArrowUpRight className="h-4 w-4 shrink-0 text-muted-foreground transition-all group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" />
        </div>
      </div>

      <div className="mt-5 flex items-end justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            Last close
          </div>
          <div className="mt-0.5 font-display text-2xl tabular-nums text-foreground">
            {formatPrice(latestPrice)}
          </div>
          <div className={cn("mt-0.5 text-xs tabular-nums", changeColor)}>
            {formatPct(latestChangePct)}
          </div>
        </div>

        <ConvictionDial
          score={convictionScore ?? null}
          signal={technicalSignal ?? undefined}
          size="sm"
          showLabel={false}
        />
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-border/70 pt-3 text-xs">
        <span className="text-muted-foreground">Conviction</span>
        <span className="font-medium tabular-nums text-foreground">
          {convictionScore !== null && convictionScore !== undefined
            ? convictionScore.toFixed(1)
            : "—"}
        </span>
      </div>
    </Link>
  );
}
