"use client";

import * as React from "react";
import { Star } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { useWatchlist } from "@/lib/watchlist/context";
import { cn } from "@/lib/utils";

/*
 * The star toggle for watchlisting a ticker.
 *
 * Visual treatment lives in the "Karachi Dusk" accent (terracotta)
 * rather than the primary teal: the page already uses teal heavily
 * (KSE-30 chips, conviction-score colorway, link hover), and the
 * accent is the existing palette role for "user-engaged choice"
 * (analyze CTA, hover accents). A filled accent star is therefore
 * the natural visual signal for "this is yours", and it scans
 * separately from the rest of the card's teal-on-cream language.
 *
 * Optimistic update is delegated entirely to the WatchlistProvider —
 * this component just calls toggle() and waits. The provider handles
 * the placeholder insert / rollback / 409+404 idempotence. While the
 * call is in flight we disable the button (to prevent double-clicks)
 * and surface failures through `onError`.
 *
 * One important detail: dashboard cards are <Link>s wrapping the
 * whole card area, so a star inside that link must stop event
 * propagation and preventDefault — otherwise clicking the star also
 * navigates into the company detail page.
 */

type Variant = "card" | "header";

const SIZE_MAP: Record<Variant, { wrapper: string; icon: string }> = {
  // On a card: small, sits in the top-right corner of the card,
  // doesn't compete with the ticker label.
  card: { wrapper: "h-8 w-8", icon: "h-4 w-4" },
  // On the company-detail header: a bit bigger so it feels at
  // home next to the H1.
  header: { wrapper: "h-10 w-10", icon: "h-[18px] w-[18px]" },
};

interface WatchlistStarProps {
  ticker: string;
  variant?: Variant;
  onError?: (message: string) => void;
  /** Stop click events from bubbling to a wrapping Link. */
  stopPropagation?: boolean;
  className?: string;
}

export function WatchlistStar({
  ticker,
  variant = "card",
  onError,
  stopPropagation = true,
  className,
}: WatchlistStarProps) {
  const { isOnWatchlist, toggle, ready } = useWatchlist();
  const [busy, setBusy] = React.useState(false);
  const onList = isOnWatchlist(ticker);
  const sizes = SIZE_MAP[variant];

  // Visual state during in-flight: the optimistic update has already
  // flipped onList, so we don't need a separate "pending" colour — we
  // just disable interaction and dim the icon a touch.
  const handleClick = React.useCallback(
    async (e: React.MouseEvent<HTMLButtonElement>) => {
      if (stopPropagation) {
        e.preventDefault();
        e.stopPropagation();
      }
      if (busy || !ready) return;
      setBusy(true);
      try {
        await toggle(ticker);
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? err.message
            : `Couldn't update watchlist for ${ticker}.`;
        if (onError) onError(msg);
      } finally {
        setBusy(false);
      }
    },
    [ticker, toggle, busy, ready, stopPropagation, onError]
  );

  const label = onList
    ? `Remove ${ticker} from watchlist`
    : `Add ${ticker} to watchlist`;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy || !ready}
      aria-pressed={onList}
      aria-label={label}
      title={label}
      className={cn(
        "focus-ring inline-grid place-items-center rounded-full border transition-all",
        // Resting visual: muted outline when not on list, accent fill when on list.
        onList
          ? "border-accent/40 bg-accent/10 text-accent hover:bg-accent/15"
          : "border-border bg-card/80 text-muted-foreground hover:border-accent/40 hover:text-accent",
        // Subtle pressed-feel scale on hover, only when not busy.
        !busy && "hover:scale-[1.05] active:scale-[0.98]",
        busy && "opacity-60 cursor-wait",
        !ready && "opacity-40 cursor-default",
        sizes.wrapper,
        className
      )}
    >
      <Star
        className={cn(
          sizes.icon,
          "transition-all",
          onList && "fill-accent"
        )}
        strokeWidth={onList ? 1.5 : 1.75}
      />
    </button>
  );
}
