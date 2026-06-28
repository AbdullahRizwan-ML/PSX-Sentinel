import { apiRequest } from "./client";
import type {
  CompanyDetailResponse,
  CompanyResponse,
  IntelligenceReportResponse,
  MarketSummaryResponse,
  NewsArticleResponse,
  PaginatedResponse,
  PricePoint,
} from "./types";

export function listCompanies(params?: {
  page?: number;
  limit?: number;
  search?: string;
  sector?: string;
}): Promise<PaginatedResponse<CompanyResponse>> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.search) qs.set("search", params.search);
  if (params?.sector) qs.set("sector", params.sector);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiRequest<PaginatedResponse<CompanyResponse>>(
    `/api/v1/companies${suffix}`
  );
}

export function getCompanyDetail(ticker: string): Promise<CompanyDetailResponse> {
  return apiRequest<CompanyDetailResponse>(
    `/api/v1/companies/${encodeURIComponent(ticker)}`
  );
}

export function getCompanyPrices(
  ticker: string,
  limit = 90
): Promise<PricePoint[]> {
  return apiRequest<PricePoint[]>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/prices?limit=${limit}`
  );
}

/*
 * Pull the full available price history for one ticker (up to the
 * backend's configured ceiling). Used by PriceChart so MA20/MA50 can
 * be computed client-side over the entire series — that way the
 * overlay lines are accurate from the very first visible day of the
 * smallest range, instead of ramping up out of zero. The backend
 * ceiling is 2000 rows, comfortably above any ticker's ~2-year history
 * (~500 trading days) with headroom for ENGRO and for adding more
 * tickers later.
 *
 * Note: the /prices endpoint defaults its date window to the last 90
 * days when no start_date is given — so we explicitly pass a
 * start_date deep enough in the past (~7 years) to cover any seeded
 * ticker's full history. The actual row count is then capped by the
 * limit parameter, not the date window.
 */
export function getCompanyPriceHistory(
  ticker: string
): Promise<PricePoint[]> {
  const startDate = new Date();
  startDate.setFullYear(startDate.getFullYear() - 7);
  const startStr = startDate.toISOString().slice(0, 10);
  return apiRequest<PricePoint[]>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/prices` +
      `?limit=2000&start_date=${startStr}`
  );
}

export function getLatestReport(ticker: string): Promise<IntelligenceReportResponse> {
  return apiRequest<IntelligenceReportResponse>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/report`
  );
}

export function triggerAnalysis(ticker: string): Promise<IntelligenceReportResponse> {
  return apiRequest<IntelligenceReportResponse>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/analyze`,
    { method: "POST" }
  );
}

export function getMarketSummary(): Promise<MarketSummaryResponse> {
  return apiRequest<MarketSummaryResponse>("/api/v1/market/summary");
}

/*
 * Paginated news articles for a ticker. The backend endpoint returns
 * the raw keyword-matched set from `news_articles` — no per-article
 * LLM relevance judgment is attached (NewsSynthesizer only persists
 * an aggregate `relevant_articles` count, surfaced via
 * IntelligenceReportResponse.news_synthesis). The NewsList component
 * uses both this list and that aggregate count to decide what to
 * render: see the comment block at the top of NewsList for the
 * full state machine.
 */
export function getCompanyNews(
  ticker: string,
  params?: { page?: number; limit?: number }
): Promise<PaginatedResponse<NewsArticleResponse>> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.limit) qs.set("limit", String(params.limit));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiRequest<PaginatedResponse<NewsArticleResponse>>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/news${suffix}`
  );
}
