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

export type MlPredictedClass = "UP" | "DOWN" | "FLAT";

export type MlSkipReason =
  | "model_unavailable"
  | "insufficient_history"
  | "below_confidence_threshold";

/*
 * Per-run detail of the ML price-direction signal. Mirrors the
 * MlDetail Pydantic schema in backend/app/schemas/intelligence.py,
 * which itself mirrors the ml_detail dict persisted by the
 * Arbitrator. All fields are nullable because the block is emitted
 * even when the model was unavailable.
 */
export interface MlDetail {
  gate_passed: boolean;
  skip_reason: MlSkipReason | string | null;
  predicted_class: MlPredictedClass | string | null;
  max_prob: number | null;
  probabilities: { DOWN?: number; FLAT?: number; UP?: number } | null;
  confidence_threshold: number | null;
  as_of_date: string | null;
  magnitude_points: number | null;
  model_caveat: string | null;
}

/*
 * Per-term contributions that (plus a base of 50) sum to the final
 * conviction_score. Mirrors backend ScoreBreakdown.
 */
export interface ScoreBreakdown {
  technical_contribution: number;
  news_contribution: number;
  filing_contribution: number;
  ml_contribution: number;
  ml_detail: MlDetail | null;
}

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
  // Optional because older persisted reports (or reports cached
  // before this field was added to the schema) may not carry it.
  score_breakdown?: ScoreBreakdown | null;
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

/*
 * Watchlist item. Mirrors backend WatchlistItemResponse.
 * company_name is populated from a Company join on the backend.
 */
export interface WatchlistItem {
  id: string;
  ticker: string;
  company_name: string | null;
  added_at: string;
  notes: string | null;
}

export interface AddWatchlistRequest {
  ticker: string;
  notes?: string;
}
