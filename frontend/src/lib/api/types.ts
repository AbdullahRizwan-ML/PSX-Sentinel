/*
 * Types mirroring backend Pydantic schemas under backend/app/schemas/.
 * Keep these in sync with the FastAPI source of truth — if the API shape
 * changes, update both ends. There is no codegen step right now.
 */

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserResponse {
  id: string;
  email: string;
  full_name: string;
  subscription_tier: string;
  is_active: boolean;
  created_at: string;
}

export interface UserRegisterRequest {
  email: string;
  password: string;
  full_name: string;
}

export interface UserLoginRequest {
  email: string;
  password: string;
}

export interface CompanyResponse {
  ticker: string;
  name: string;
  sector: string;
  market_cap_pkr: number | null;
  is_kse30: boolean;
  is_kmi30: boolean;
  last_updated: string;
}

export interface CompanyDetailResponse extends CompanyResponse {
  shares_outstanding: number | null;
  listing_date: string | null;
  latest_price: number | null;
  latest_change_pct: number | null;
  latest_conviction_score: number | null;
}

export interface PaginatedResponse<T> {
  total: number;
  page: number;
  limit: number;
  pages: number;
  items: T[];
}

export interface PricePoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change_pct: number | null;
}

export type TechnicalSignal =
  | "STRONG_BUY"
  | "BUY"
  | "NEUTRAL"
  | "SELL"
  | "STRONG_SELL";

export interface IntelligenceReportResponse {
  id: string;
  ticker: string;
  generated_at: string;
  report_date: string;
  ml_beat_probability: number;
  conviction_score: number;
  bull_case: string;
  bear_case: string;
  risk_factors: string[];
  technical_signal: TechnicalSignal;
  total_tokens_used: number;
  generation_time_seconds: number;
}

export interface MarketSummaryResponse {
  top_gainers: Array<{
    ticker: string;
    name: string;
    change_pct: number | null;
    close: number;
  }>;
  top_losers: Array<{
    ticker: string;
    name: string;
    change_pct: number | null;
    close: number;
  }>;
  total_companies: number;
  market_date: string;
}

export interface ApiErrorBody {
  detail?: string;
  type?: string;
}
