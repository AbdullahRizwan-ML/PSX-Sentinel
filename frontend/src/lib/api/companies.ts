import { apiRequest } from "./client";
import type {
  CompanyDetailResponse,
  CompanyResponse,
  IntelligenceReportResponse,
  MarketSummaryResponse,
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
