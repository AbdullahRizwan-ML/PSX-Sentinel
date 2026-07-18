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
 * One metric's peer-rank record inside FundamentalsDetail. Mirrors
 * backend FundMetricRank (Phase 5 Session 8). used=false + reason
 * documents why a metric was excluded (e.g. PSX Terminal's literal-0.0
 * dividend yields for LUCK/MARI).
 */
export interface FundMetricRank {
  used: boolean;
  value: number | null;
  n_ranked: number | null;
  percentile: number | null;
  tilt: number | null;
  reason: string | null;
}

/*
 * Audit detail for the fundamentals value tilt. Mirrors backend
 * FundamentalsDetail (Phase 5 Session 8).
 */
export interface FundamentalsDetail {
  used: boolean;
  skip_reason: string | null;
  metrics: { pe_ratio?: FundMetricRank; dividend_yield?: FundMetricRank } | null;
  combined_points: number | null;
  metric_magnitude_points: number | null;
  peer_universe_size: number | null;
  caveat: string | null;
}

/*
 * Audit detail for the sector FIPI/LIPI flow regime term. Mirrors
 * backend FlowDetail (Phase 5 Session 8). skip_reason distinguishes
 * the honest-zero paths (stale data / unmapped sector / not enough
 * days) from a genuine near-zero flow reading.
 */
export interface FlowDetail {
  used: boolean;
  skip_reason: string | null;
  sector: string | null;
  nccpl_sectors: string[] | null;
  variant: string | null;
  latest_flow_date: string | null;
  window_days: number | null;
  window_start: string | null;
  window_end: string | null;
  net_value_pkr: number | null;
  gross_value_pkr: number | null;
  imbalance_ratio: number | null;
  scale: number | null;
  magnitude_points: number | null;
  staleness_days: number | null;
  stale_threshold_days: number | null;
}

/*
 * Per-term contributions that (plus a base of 50) sum to the final
 * conviction_score. Mirrors backend ScoreBreakdown.
 *
 * The fundamentals/flow terms (Phase 5 Session 8) are optional:
 * null/undefined on any report generated before that session — the
 * breakdown strip omits those pills entirely rather than faking a 0.
 */
export interface ScoreBreakdown {
  technical_contribution: number;
  news_contribution: number;
  filing_contribution: number;
  ml_contribution: number;
  ml_detail: MlDetail | null;
  fundamentals_contribution?: number | null;
  flow_contribution?: number | null;
  fundamentals_detail?: FundamentalsDetail | null;
  flow_detail?: FlowDetail | null;
}

/*
 * Per-run summary of the NewsSynthesizer agent. Mirrors backend
 * NewsSynthesis (Phase 4 Session 5), which is hoisted out of
 * IntelligenceReport.agent_outputs['news_synthesizer']['output'].
 *
 * Used by NewsList to distinguish the two zero-states:
 *   - article_count === 0                                  → no articles matched
 *   - article_count > 0 && relevant_articles === 0         → matched but none judged relevant
 *   - article_count > 0 && relevant_articles < article_count → partial (LLM said some are relevant
 *     but doesn't tag which ones — current schema doesn't carry per-article flags)
 *   - article_count > 0 && relevant_articles === article_count → all matched are relevant
 */
export interface NewsSynthesis {
  sentiment: string;
  uniformity: string;
  article_count: number;
  relevant_articles: number;
  narrative_summary: string;
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
  // Optional because older persisted reports may not carry it (or a
  // cached response from before Phase 4 Session 5 hoisted the field).
  news_synthesis?: NewsSynthesis | null;
}

/*
 * Single news article row. Mirrors backend NewsArticleResponse.
 */
export interface NewsArticleResponse {
  id: string;
  ticker: string;
  source: string;
  headline: string;
  summary: string | null;
  url: string;
  published_at: string;
  sentiment_score: number | null;
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
