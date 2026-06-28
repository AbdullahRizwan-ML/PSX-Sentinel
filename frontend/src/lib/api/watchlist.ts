import { apiRequest } from "./client";
import type { AddWatchlistRequest, WatchlistItem } from "./types";

/*
 * Watchlist endpoints — Phase 4 Session 4.
 *
 * Backend lives at /api/v1/watchlist (see
 * backend/app/api/v1/intelligence.py). Live-verified end-to-end via
 * backend/scripts/verify_watchlist_endpoints.py — confirmed behavior:
 *
 *   GET    /api/v1/watchlist          -> 200, list[WatchlistItem]
 *   POST   /api/v1/watchlist          -> 201 on add
 *                                        409 if already on watchlist
 *                                        404 if ticker unknown
 *                                        (backend uppercases the ticker
 *                                         so lowercase input is fine)
 *   DELETE /api/v1/watchlist/{ticker} -> 200 {"message": "..."} on remove
 *                                        404 if not on watchlist
 *                                        (backend uppercases the path
 *                                         param too)
 *
 * Callers should expect ApiError with .status in {409, 404} for the
 * known edge cases — the watchlist context unwraps these to decide
 * whether to roll back an optimistic update.
 */

export function getWatchlist(): Promise<WatchlistItem[]> {
  return apiRequest<WatchlistItem[]>("/api/v1/watchlist");
}

export function addToWatchlist(
  ticker: string,
  notes?: string
): Promise<WatchlistItem> {
  const body: AddWatchlistRequest = { ticker };
  if (notes !== undefined) body.notes = notes;
  return apiRequest<WatchlistItem>("/api/v1/watchlist", {
    method: "POST",
    body,
  });
}

export function removeFromWatchlist(
  ticker: string
): Promise<{ message: string }> {
  return apiRequest<{ message: string }>(
    `/api/v1/watchlist/${encodeURIComponent(ticker)}`,
    { method: "DELETE" }
  );
}
