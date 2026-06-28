"use client";

import * as React from "react";

import { ApiError } from "@/lib/api/client";
import {
  addToWatchlist as apiAddToWatchlist,
  getWatchlist as apiGetWatchlist,
  removeFromWatchlist as apiRemoveFromWatchlist,
} from "@/lib/api/watchlist";
import type { WatchlistItem } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/context";

/*
 * Shared watchlist state, scoped to authenticated routes.
 *
 * Two reasons this lives in a context rather than per-component:
 *
 *   1. The same ticker can be present in multiple places at once — a
 *      dashboard card AND a company-detail header AND (eventually) a
 *      filtered dashboard view. Without shared state, toggling the star
 *      in one place wouldn't update the others, and the optimistic-
 *      update story would split-brain across them.
 *
 *   2. The dashboard "My Watchlist" tab needs to know membership
 *      synchronously to filter client-side. Re-fetching on every render
 *      is wasteful when the data already needs to be loaded once for
 *      the stars themselves.
 *
 * Membership is exposed as a Set<string> of uppercase tickers so the
 * star component and the dashboard filter can both do O(1) lookups.
 *
 * Optimistic-update protocol on toggle:
 *
 *   - Add path: optimistically place the ticker into the Set, then call
 *     POST /watchlist. On 201, swap the placeholder for the real
 *     WatchlistItem. On 409 ("already on watchlist"), keep the
 *     placeholder — the server state matches our optimistic guess. On
 *     any other error, roll back by removing the ticker from the Set
 *     and surface the error to the caller via a thrown ApiError.
 *
 *   - Remove path: optimistically remove the ticker (and any cached
 *     WatchlistItem) from state, then call DELETE /watchlist/{ticker}.
 *     On 200, nothing to do. On 404 ("not on watchlist"), the server
 *     state matches the optimistic remove — keep removed. On any other
 *     error, re-add the ticker and surface via thrown ApiError.
 *
 * 409-on-add and 404-on-remove being treated as success-equivalents
 * makes the toggle idempotent from the UI's point of view — important
 * because the same Star can be tapped twice quickly or on stale data
 * after a different tab made a change, and we don't want either of
 * those harmless cases to produce an error banner.
 */

interface WatchlistContextValue {
  ready: boolean;
  loading: boolean;
  loadError: string | null;
  items: WatchlistItem[];
  tickerSet: Set<string>;
  isOnWatchlist: (ticker: string) => boolean;
  add: (ticker: string) => Promise<void>;
  remove: (ticker: string) => Promise<void>;
  toggle: (ticker: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const WatchlistContext = React.createContext<WatchlistContextValue | null>(
  null
);

export function WatchlistProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user } = useAuth();
  const [items, setItems] = React.useState<WatchlistItem[]>([]);
  const [ready, setReady] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    if (!user) {
      setItems([]);
      setReady(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetWatchlist();
      setItems(list);
      setReady(true);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : "Couldn't load your watchlist.";
      setLoadError(msg);
      setReady(true);
    } finally {
      setLoading(false);
    }
  }, [user]);

  React.useEffect(() => {
    if (user) {
      void refresh();
    } else {
      setItems([]);
      setReady(false);
    }
  }, [user, refresh]);

  const tickerSet = React.useMemo(
    () => new Set(items.map((i) => i.ticker.toUpperCase())),
    [items]
  );

  const isOnWatchlist = React.useCallback(
    (ticker: string) => tickerSet.has(ticker.toUpperCase()),
    [tickerSet]
  );

  const add = React.useCallback(async (rawTicker: string) => {
    const ticker = rawTicker.toUpperCase();

    // Optimistic: place a placeholder item immediately. Synthetic id +
    // synthetic added_at + null company_name; the swap on 201 will
    // overwrite with the real server payload. If this fails, the
    // rollback below removes it.
    const placeholder: WatchlistItem = {
      id: `optimistic-${ticker}`,
      ticker,
      company_name: null,
      added_at: new Date().toISOString(),
      notes: null,
    };
    let alreadyPresent = false;
    setItems((prev) => {
      if (prev.some((i) => i.ticker.toUpperCase() === ticker)) {
        alreadyPresent = true;
        return prev;
      }
      return [placeholder, ...prev];
    });
    if (alreadyPresent) return;

    try {
      const real = await apiAddToWatchlist(ticker);
      setItems((prev) =>
        prev.map((i) =>
          i.ticker.toUpperCase() === ticker ? real : i
        )
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Server already has it. Refresh to pick up the real row so the
        // placeholder gets replaced by canonical data on next render.
        void refresh();
        return;
      }
      // Roll back the optimistic insert.
      setItems((prev) =>
        prev.filter((i) => i.ticker.toUpperCase() !== ticker)
      );
      throw err;
    }
  }, [refresh]);

  const remove = React.useCallback(async (rawTicker: string) => {
    const ticker = rawTicker.toUpperCase();

    // Optimistic: pop it out and stash the popped row so we can roll
    // back without losing notes/added_at if the network call fails.
    let popped: WatchlistItem | undefined;
    setItems((prev) => {
      popped = prev.find((i) => i.ticker.toUpperCase() === ticker);
      if (!popped) return prev;
      return prev.filter((i) => i.ticker.toUpperCase() !== ticker);
    });
    if (!popped) return;

    try {
      await apiRemoveFromWatchlist(ticker);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Server already lacks it. Optimistic remove was correct.
        return;
      }
      // Roll back: put the popped row back at its original spot.
      // Simpler approximation: prepend. The watchlist is small, order
      // is least-recently-added-first elsewhere, and a re-prepend is
      // close enough — refresh() would re-sort by added_at anyway.
      const restored = popped;
      setItems((prev) => {
        if (prev.some((i) => i.ticker.toUpperCase() === ticker)) {
          return prev;
        }
        return [restored, ...prev];
      });
      throw err;
    }
  }, []);

  const toggle = React.useCallback(
    async (ticker: string) => {
      if (isOnWatchlist(ticker)) {
        await remove(ticker);
      } else {
        await add(ticker);
      }
    },
    [add, remove, isOnWatchlist]
  );

  const value: WatchlistContextValue = {
    ready,
    loading,
    loadError,
    items,
    tickerSet,
    isOnWatchlist,
    add,
    remove,
    toggle,
    refresh,
  };

  return (
    <WatchlistContext.Provider value={value}>
      {children}
    </WatchlistContext.Provider>
  );
}

export function useWatchlist(): WatchlistContextValue {
  const ctx = React.useContext(WatchlistContext);
  if (!ctx) {
    throw new Error(
      "useWatchlist must be used inside <WatchlistProvider>"
    );
  }
  return ctx;
}
