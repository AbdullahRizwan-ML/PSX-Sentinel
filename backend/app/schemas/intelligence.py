"""
PSX Sentinel — Intelligence, Alert & Watchlist Schemas

Pydantic v2 models for intelligence reports, analysis job status,
user alerts, and watchlist management endpoints.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MlDetail(BaseModel):
    """
    Per-run detail of the ML price-direction signal, mirroring the
    ``ml_detail`` sub-dict that ``Arbitrator._build_score_breakdown``
    persists into ``IntelligenceReport.agent_outputs``.

    All fields are nullable because the Arbitrator emits the block
    even when the model was unavailable or when the gate failed —
    consumers can distinguish "real bullish signal" from "model
    unavailable" from "below confidence threshold" by reading
    ``gate_passed`` and ``skip_reason`` together.
    """

    # ``model_caveat`` collides with Pydantic v2's protected ``model_``
    # namespace — silence the warning since this is a deliberate name
    # mirrored from the persisted JSON, not an internal field.
    model_config = ConfigDict(protected_namespaces=())

    gate_passed: bool
    skip_reason: str | None = None
    predicted_class: str | None = None
    max_prob: float | None = None
    probabilities: dict[str, float] | None = None
    confidence_threshold: float | None = None
    as_of_date: str | None = None
    magnitude_points: float | None = None
    model_caveat: str | None = None


class FundMetricRank(BaseModel):
    """
    One metric's peer-rank record inside ``FundamentalsDetail``.
    ``used=False`` + ``reason`` documents WHY a metric was excluded
    (e.g. PSX Terminal's literal-0.0 dividend yields for LUCK/MARI) —
    the "distinguish real zero from no/bad data" discipline.
    """

    used: bool
    value: float | None = None
    n_ranked: int | None = None
    percentile: float | None = None
    tilt: float | None = None
    reason: str | None = None


class FundamentalsDetail(BaseModel):
    """
    Audit detail for the Phase 5 Session 8 fundamentals value tilt.
    Mirrors the ``fundamentals_detail`` dict persisted by
    ``Arbitrator._fundamentals_contribution``. Always emitted (even at
    0.0) so consumers can tell a computed-zero from an honest skip.
    """

    used: bool
    skip_reason: str | None = None
    metrics: dict[str, FundMetricRank] | None = None
    combined_points: float | None = None
    metric_magnitude_points: float | None = None
    peer_universe_size: int | None = None
    caveat: str | None = None


class FlowDetail(BaseModel):
    """
    Audit detail for the Phase 5 Session 8 sector FIPI/LIPI flow
    regime term. Mirrors the ``flow_detail`` dict persisted by
    ``Arbitrator._flow_contribution``. ``skip_reason`` distinguishes
    the honest-zero paths (stale data / unmapped sector / not enough
    days) from a genuine near-zero flow reading.
    """

    used: bool
    skip_reason: str | None = None
    sector: str | None = None
    nccpl_sectors: list[str] | None = None
    variant: str | None = None
    latest_flow_date: str | None = None
    window_days: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    net_value_pkr: float | None = None
    gross_value_pkr: float | None = None
    imbalance_ratio: float | None = None
    scale: float | None = None
    magnitude_points: float | None = None
    staleness_days: int | None = None
    stale_threshold_days: int | None = None


class ScoreBreakdown(BaseModel):
    """
    Per-term contributions that sum (plus the base of 50) to the
    final ``conviction_score`` on an IntelligenceReport.

    Mirrors the ``score_breakdown`` dict emitted by
    ``Arbitrator._build_score_breakdown`` and persisted under
    ``IntelligenceReport.agent_outputs['arbitrator']['output']``.

    The two Phase 5 Session 8 terms (fundamentals/flow) are optional:
    ``None`` on any report generated before that session — visibly
    distinct from a computed 0.0.
    """

    technical_contribution: float
    news_contribution: float
    filing_contribution: float
    ml_contribution: float
    ml_detail: MlDetail | None = None
    fundamentals_contribution: float | None = None
    flow_contribution: float | None = None
    fundamentals_detail: FundamentalsDetail | None = None
    flow_detail: FlowDetail | None = None


class NewsSynthesis(BaseModel):
    """
    Per-run summary of the NewsSynthesizer agent, hoisted from
    ``IntelligenceReport.agent_outputs['news_synthesizer']['output']``
    so the frontend can distinguish meaningfully different "no news to
    show" states without re-fetching or parsing the raw blob.

    The two zero-states this exists to disambiguate:

    1. ``article_count == 0``  → no articles were matched for this
       ticker at all (the keyword matcher in NewsCollector found
       nothing). NewsSynthesizer skipped its LLM call entirely per the
       project's "skip when no real data" rule.
    2. ``article_count > 0 and relevant_articles == 0`` → articles were
       matched and the LLM actually ran a relevance judgment, but
       judged 0 of them as genuinely about this company (typically the
       noisy-keyword-match case described in docs/KNOWN_ISSUES.md —
       e.g. general "petroleum levy" headlines tangentially mentioning
       PPL/PSO).

    The schema is additive metadata only — it carries the counts and
    judgment summary NewsSynthesizer already produces and persists.
    No change to the agent's analysis logic or LLM prompt.
    """

    sentiment: str
    uniformity: str
    article_count: int
    relevant_articles: int
    narrative_summary: str


class IntelligenceReportResponse(BaseModel):
    """Full intelligence report generated by the 4-agent system."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    generated_at: datetime
    report_date: date
    ml_beat_probability: float
    conviction_score: float
    bull_case: str
    bear_case: str
    risk_factors: list[str]
    technical_signal: str
    total_tokens_used: int
    generation_time_seconds: float
    # Additive over the legacy schema. Pulled out of the persisted
    # ``agent_outputs`` blob by the validator below so the frontend
    # doesn't have to dig into a raw JSON dict. ``None`` for any
    # report row written before this field existed (and during the
    # in-progress placeholder window before the Arbitrator finishes).
    score_breakdown: ScoreBreakdown | None = None
    # Same pattern as ``score_breakdown`` — additive metadata hoisted
    # out of ``agent_outputs['news_synthesizer']['output']`` so the
    # frontend can tell apart the two zero-states described on
    # ``NewsSynthesis`` above. ``None`` for any report row written
    # before NewsSynthesizer was wired in (Phase 2B Session 2 and
    # later all carry it).
    news_synthesis: NewsSynthesis | None = None

    @model_validator(mode="before")
    @classmethod
    def _hoist_agent_outputs(cls, data: Any) -> Any:
        """
        Pull ``score_breakdown`` and ``news_synthesis`` out of the
        persisted ``agent_outputs`` JSON so they appear as typed
        top-level fields on the response.

        Handles three input shapes:
        - ORM instance (``IntelligenceReport``): hoist via attribute
          access, return a dict of all needed fields.
        - dict with ``agent_outputs`` already present: same hoist, in
          place.
        - dict already shaped like the response (e.g. cached JSON
          deserialised by ``model_validate_json``): pass through
          unchanged.
        """
        # Cached-JSON path: dict already shaped like the response and
        # may not carry ``agent_outputs`` at all. Nothing to do.
        if isinstance(data, dict) and "agent_outputs" not in data:
            return data

        agent_outputs: Any
        if isinstance(data, dict):
            agent_outputs = data.get("agent_outputs")
        else:
            agent_outputs = getattr(data, "agent_outputs", None)

        if not isinstance(agent_outputs, dict):
            return data

        sb: Any = None
        arb = agent_outputs.get("arbitrator")
        if isinstance(arb, dict):
            arb_output = arb.get("output")
            if isinstance(arb_output, dict):
                candidate = arb_output.get("score_breakdown")
                if isinstance(candidate, dict):
                    sb = candidate

        ns: Any = None
        news = agent_outputs.get("news_synthesizer")
        if isinstance(news, dict):
            news_output = news.get("output")
            if isinstance(news_output, dict):
                ns = news_output

        # Re-build a plain dict so Pydantic doesn't try to also pull
        # ``score_breakdown`` / ``news_synthesis`` off the ORM via
        # attribute access (they aren't attributes) and so the
        # cached-JSON code path stays symmetric.
        if isinstance(data, dict):
            result = {k: v for k, v in data.items() if k != "agent_outputs"}
        else:
            result = {}
            for name in cls.model_fields:
                if name in ("score_breakdown", "news_synthesis"):
                    continue
                if hasattr(data, name):
                    result[name] = getattr(data, name)
        if sb is not None:
            result["score_breakdown"] = sb
        if ns is not None:
            result["news_synthesis"] = ns
        return result


class AnalysisJobResponse(BaseModel):
    """Status response for a queued or running analysis job."""

    job_id: str
    ticker: str
    status: str  # "queued" | "running" | "complete" | "failed"
    message: str
    estimated_seconds: int = 60


class AlertResponse(BaseModel):
    """User-configured price/conviction alert."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    alert_type: str
    threshold_value: float | None
    is_active: bool
    triggered_count: int
    created_at: datetime


class CreateAlertRequest(BaseModel):
    """
    Request to create a new alert.

    alert_type must be one of: PRICE_ABOVE, PRICE_BELOW,
    CONVICTION_ABOVE, NEW_REPORT, EARNINGS_ANNOUNCEMENT.
    threshold_value is required for PRICE_* and CONVICTION_* types.
    """

    ticker: str = Field(min_length=1, max_length=20)
    alert_type: str = Field(
        pattern=r"^(PRICE_ABOVE|PRICE_BELOW|CONVICTION_ABOVE|NEW_REPORT|EARNINGS_ANNOUNCEMENT)$"
    )
    threshold_value: float | None = None


class WatchlistItemResponse(BaseModel):
    """Watchlist entry with optional company name from join."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    company_name: str | None = None
    added_at: datetime
    notes: str | None


class AddWatchlistRequest(BaseModel):
    """Request to add a ticker to the user's watchlist."""

    ticker: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=500)
