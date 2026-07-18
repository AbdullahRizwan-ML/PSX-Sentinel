"""
PSX Sentinel — Arbitrator Agent

Synthesis agent that combines outputs from TrendAnalyzer, NewsSynthesizer,
FilingSceptic, AND a confidence-gated ML price-direction signal into a
final conviction score using deterministic scoring, then calls the LLM
once for narrative generation.

The conviction score is computed in Python first — the LLM interprets
the pre-computed score and writes bull/bear cases. It does not decide
the number itself. This is the confidence-weighted synthesis pattern:
each upstream agent's contribution is multiplied by its self-reported
confidence, so low-quality data automatically dampens its influence.

Score formula (max absolute swing in parens):
        50  (base)
      ± 20  technical_contribution = SIGNAL_MAP[signal] * trend_confidence
      ± 15  news_contribution      = SENTIMENT_MAP[sentiment] * news_confidence
      -  5..-30  filing_contribution = SEVERITY_PENALTY[severity] when red flags
      ±  5  ml_contribution         = ML_MAGNITUDE * direction, but only
            when the ML model's top-class predict_proba > ML_GATE
            (0.55). Below the gate, OR if the model is unavailable, OR
            if there isn't enough history to build features, this term
            contributes 0.0 — same "honest zero" treatment as
            filing_contribution when no filing data exists.
      ± 10  fundamentals_contribution (Phase 5 Session 8) —
            deterministic value tilt from peer-percentile ranks over
            the ACTIVE universe (~10 tickers):
                tilt_pe    = (0.5 − pct_rank(P/E))            × 2
                tilt_yield = (pct_rank(dividend_yield) − 0.5) × 2
                contribution = clamp(5·tilt_pe + 5·tilt_yield, −10, +10)
            (cheaper-than-peers P/E and higher-than-peers yield tilt
            positive; each metric is worth up to ±5 and an
            excluded/missing metric contributes 0 — NOT renormalized,
            so less evidence means a smaller possible swing).
            Known-bad source data is EXCLUDED, not treated as real:
            dividend_yield of exactly 0.0 (PSX Terminal serves literal
            0.0 when it has no dividend data — documented for
            LUCK/MARI, both of which do pay dividends) and NULLs are
            per-ticker exclusions logged in the score breakdown.
            CAVEAT: a peer ranking over a 10-ticker universe is
            statistically weak by construction — it is a relative
            tilt within this small universe, not a valuation model.
      ± 10  flow_contribution (Phase 5 Session 8) — deterministic
            sector-level FIPI/LIPI institutional-flow regime:
                ratio = Σ net / Σ gross over the last ≤10 flow trading
                        days for the ticker's mapped NCCPL sector(s)
                        (variant: FIPI + local-institutional LIPI, REG
                        market; net PKR / (buy + |sell|) PKR)
                contribution = clamp(ratio / FLOW_SCALE, −1, +1) × 10
            FLOW_SCALE = 0.125 ≈ the 90th percentile of |ratio| over
            the full 2021-06→2026-07 archive (pooled across the three
            mapped sector groups), so a historically-extreme flow
            regime maps to the full ±10 and a median day to ~±4.
            STALENESS-GATED: if the flow data used ends more than
            FLOW_STALE_DAYS (14) calendar days before report_date the
            term contributes 0.0 with reason "stale_flow_data" — an
            honest "we didn't use this because it's too old", visibly
            distinct from a real near-zero flow reading. Unmappable
            sectors (ENGROH's "Investment Companies" has no named
            NCCPL sector) are an honest zero too, never silently
            proxied by NCCPL's "All other Sectors" catch-all.

    Max possible envelope is now 50+20+15+5+10+10 = 110 and
    50−20−15−30−5−10−10 = −40, so the final clamp to [0, 100] in
    _calculate_score is load-bearing, not just defensive.

Why ML gets only 5 (not the originally-planned 15):
    The trained XGBoost model scored +6pp over a 3-class random
    baseline in Phase 3 Session 2 — a real but very thin edge, and
    structurally FLAT-blind. Giving it 15% would let a low-conviction
    technical signal dominate. 5%, plus the 0.55 confidence gate,
    matches the Session 2 recommendation: small voice, only speaks
    when relatively sure, never dominant. See docs/BUILD_LOG.md
    Phase 3 Session 2 for the full evaluation that drove this choice.

Why the two Session 8 terms get 10 each:
    Both are deterministic calculations on real but weak evidence — a
    10-ticker peer rank and a sector-level (not per-ticker) flow
    regime whose historical correlation with forward 5-day sector
    returns is only ~+0.05 (see the Session 8 exploration in
    docs/BUILD_LOG.md). 10 points keeps each individually smaller
    than the technical term and jointly unable to dominate it, while
    still letting fundamentals/flows move a score visibly when they
    genuinely diverge from neutral.
"""

import time
from datetime import date

from loguru import logger

from app.agents.base import AgentContext, AgentResult, BaseAgent


class Arbitrator(BaseAgent):
    name = "arbitrator"
    max_tokens = 1200
    timeout_seconds = 40

    SIGNAL_MAP = {
        "STRONG_BUY": 20,
        "BUY": 10,
        "NEUTRAL": 0,
        "SELL": -10,
        "STRONG_SELL": -20,
    }

    SENTIMENT_MAP = {
        "BULLISH": 15,
        "NEUTRAL": 0,
        "BEARISH": -15,
    }

    SEVERITY_PENALTY = {
        "LOW": -5,
        "MEDIUM": -15,
        "HIGH": -30,
    }

    SCORE_LABELS = [
        (90, "STRONG_BUY"),
        (65, "BUY"),
        (35, "NEUTRAL"),
        (11, "SELL"),
    ]

    # ML signal weighting. Max absolute swing in points (5 == 5% of the
    # 0-100 conviction range), and the minimum max_prob the gate
    # requires. Direction comes from the predicted class:
    # UP -> +1, DOWN -> -1, FLAT -> 0.
    ML_MAGNITUDE: float = 5.0
    ML_GATE: float = 0.55
    ML_DIRECTION = {"UP": 1, "DOWN": -1, "FLAT": 0}

    # ── Phase 5 Session 8: fundamentals value tilt ──────────────────
    # Each metric (P/E, dividend yield) is worth up to ±5 points; both
    # extreme in the same direction give the full ±10. An excluded or
    # missing metric contributes 0 — deliberately NOT renormalized
    # over the remaining metrics, so a ticker with less usable
    # evidence gets a smaller possible swing, not an amplified one.
    FUND_METRIC_MAGNITUDE: float = 5.0
    # A metric needs at least this many valid values across the active
    # universe (including the analyzed ticker) before a percentile
    # rank means anything at all. With 10 active tickers this is only
    # a guard against future data erosion, not a live constraint.
    FUND_MIN_RANKED: int = 4

    # ── Phase 5 Session 8: sector FIPI/LIPI flow regime ─────────────
    FLOW_MAGNITUDE: float = 10.0
    # |imbalance ratio| that maps to the full ±10. 0.125 ≈ the 90th
    # percentile of the 10-day |Σnet/Σgross| ratio over the full
    # 2021-06 → 2026-07 institutional_flows archive, pooled across the
    # three mapped sector groups (measured 0.1258 in the Session 8
    # exploration — see docs/BUILD_LOG.md). A median flow day
    # (|ratio| ≈ 0.047) therefore lands near ±3.8.
    FLOW_SCALE: float = 0.125
    # Fewer flow trading days than this → honest zero (can't call a
    # "regime" on a couple of days of data).
    FLOW_MIN_DAYS: int = 5
    # Staleness gate, MANDATORY: if the newest flow row used is more
    # than this many calendar days older than report_date, the term is
    # 0.0 with reason "stale_flow_data". 14 days ≈ tolerance for one
    # missed manual weekly refresh plus a holiday cluster (the
    # institutional_flows table is refreshed by hand via the browser
    # pane — automated collection is Cloudflare-blocked, see
    # docs/KNOWN_ISSUES.md "Problem B"), while still guaranteeing a
    # dead pipeline degrades to silence rather than scoring tickers
    # on month-old flow regimes.
    FLOW_STALE_DAYS: int = 14

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        trend = context.trend_signals
        news = context.news_sentiment
        filings = context.filing_flags
        ml_signal = context.ml_signal or {}

        # Phase 5 Session 8 — the two deterministic terms are computed
        # ONCE here (each returns (points, detail_dict)) and threaded
        # through the score, the breakdown, and the narrative prompt,
        # so the number the user sees, the audit detail, and what the
        # LLM was told can never drift apart.
        fund = self._fundamentals_contribution(
            context.ticker, context.peer_fundamentals or {}
        )
        flow = self._flow_contribution(
            context.sector_flows or {}, context.report_date
        )

        score = self._calculate_score(
            trend, news, filings, ml_signal, fund[0], flow[0]
        )
        signal_label = self._score_to_label(score)

        prompt = self._build_prompt(
            context.ticker,
            context.company_name,
            trend,
            news,
            filings,
            ml_signal,
            fund,
            flow,
            score,
        )

        response = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name=self.name,
            analysis_id=context.analysis_id,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        parsed = self._parse_response(response.content)

        data_sources = 0
        if trend.get("confidence", 0) > 0.3:
            data_sources += 1
        if news.get("confidence", 0) > 0.3:
            data_sources += 1
        if filings.get("confidence", 0) > 0.3:
            data_sources += 1

        if data_sources >= 3:
            confidence = 0.85
        elif data_sources >= 2:
            confidence = 0.7
        elif data_sources >= 1:
            confidence = 0.55
        else:
            confidence = 0.35

        return AgentResult(
            agent_name=self.name,
            success=True,
            output={
                "conviction_score": round(score, 1),
                "technical_signal": signal_label,
                "bull_case": parsed["bull_case"],
                "bear_case": parsed["bear_case"],
                "risk_factors": parsed["risk_factors"],
                "score_breakdown": self._build_score_breakdown(
                    trend, news, filings, ml_signal, fund, flow
                ),
            },
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _calculate_score(
        self,
        trend: dict,
        news: dict,
        filings: dict,
        ml_signal: dict,
        fundamentals_points: float = 0.0,
        flow_points: float = 0.0,
    ) -> float:
        score = 50.0
        score += self._technical_contribution(trend)
        score += self._news_contribution(news)
        score += self._filing_contribution(filings)
        score += self._ml_contribution(ml_signal)
        score += fundamentals_points
        score += flow_points
        return max(0.0, min(100.0, score))

    def _technical_contribution(self, trend: dict) -> float:
        confidence = trend.get("confidence", 0.5)
        signal = trend.get("signal", "NEUTRAL")
        return self.SIGNAL_MAP.get(signal, 0) * confidence

    def _news_contribution(self, news: dict) -> float:
        confidence = news.get("confidence", 0.5)
        sentiment = news.get("sentiment", "NEUTRAL")
        return self.SENTIMENT_MAP.get(sentiment, 0) * confidence

    def _filing_contribution(self, filings: dict) -> float:
        red_flags = filings.get("red_flags", [])
        if not red_flags:
            return 0.0
        severity = filings.get("severity", "LOW")
        return self.SEVERITY_PENALTY.get(severity, -5)

    def _ml_contribution(self, ml_signal: dict) -> float:
        """
        Confidence-gated technical-ML signal. Contributes 0.0 unless
        the model is available, had enough history, AND its top class
        cleared ML_GATE. FLAT predictions also map to 0 even when the
        gate passes — the model never predicted FLAT in test, but we
        wire it through the ML_DIRECTION map honestly anyway.
        """
        if not ml_signal or not ml_signal.get("gate_passed"):
            return 0.0
        direction = self.ML_DIRECTION.get(
            ml_signal.get("predicted_class", "FLAT"), 0
        )
        return direction * self.ML_MAGNITUDE

    @staticmethod
    def _percentile_rank(value: float, values: list[float]) -> float:
        """
        Average-rank percentile of `value` within `values` (which
        includes it), in [0, 1]. 0 = lowest, 1 = highest; ties share
        their average rank. Callers must guarantee len(values) >= 2.
        """
        less = sum(1 for v in values if v < value)
        equal = sum(1 for v in values if v == value)
        avg_rank = less + (equal + 1) / 2.0  # 1-based average rank
        return (avg_rank - 1.0) / (len(values) - 1.0)

    def _fundamentals_contribution(
        self, ticker: str, peer_fundamentals: dict
    ) -> tuple[float, dict]:
        """
        Deterministic value tilt vs the active peer universe.

        EXACT FORMULA (also stated in the module docstring and
        docs/BUILD_LOG.md so it is auditable outside this file):

            tilt_pe    = (0.5 − pct_rank(P/E among valid peers)) × 2
            tilt_yield = (pct_rank(yield among valid peers) − 0.5) × 2
            contribution = clamp(5·tilt_pe + 5·tilt_yield, −10, +10)

        where pct_rank is the average-rank percentile in [0, 1], so a
        cheaper-than-peers P/E and a higher-than-peers dividend yield
        both tilt positive, each metric is worth up to ±5, and an
        excluded metric contributes exactly 0 (no renormalization —
        less evidence means a smaller possible swing).

        Data-validity rules (known-bad data is EXCLUDED, not used):
          - P/E: valid iff present and > 0.
          - dividend_yield: valid iff present and > 0.0. PSX Terminal
            serves a literal 0.0 when it has no dividend data — LUCK
            and MARI both pay dividends yet are served 0.0 (documented
            in docs/KNOWN_ISSUES.md) — so an exact 0.0 from this
            source means "no data", never "true zero yield". The rule
            is source-behavioral, not a ticker hardcode.

        Every exclusion is recorded per metric in the returned detail
        dict, which is persisted in the score breakdown — the same
        "distinguish real zero from no/bad data" discipline as the
        filing term.

        CAVEAT (by construction): this is a peer ranking over a
        ~10-ticker universe — statistically weak, a relative tilt
        within this small universe, not a valuation model. Flagged in
        the persisted detail so no consumer can mistake it for more.
        """
        detail: dict = {
            "used": False,
            "skip_reason": None,
            "metrics": {},
            "combined_points": 0.0,
            "metric_magnitude_points": self.FUND_METRIC_MAGNITUDE,
            "peer_universe_size": len(peer_fundamentals),
            "caveat": (
                "Peer-percentile ranking over a ~10-ticker universe — "
                "statistically weak by construction; a relative tilt "
                "within this small universe, not a valuation model."
            ),
        }

        own = peer_fundamentals.get(ticker)
        if not own:
            detail["skip_reason"] = "no_fundamentals_row"
            return 0.0, detail

        def valid_pe(v: object) -> bool:
            return isinstance(v, (int, float)) and v > 0

        def valid_yield(v: object) -> bool:
            # exact 0.0 excluded: PSX Terminal serves literal 0.0 for
            # "no dividend data" (documented for LUCK/MARI).
            return isinstance(v, (int, float)) and v > 0.0

        specs = [
            # (metric key, validity fn, higher_is_better, excluded-reason)
            ("pe_ratio", valid_pe, False,
             "missing_or_nonpositive_at_source"),
            ("dividend_yield", valid_yield, True,
             "missing_or_zero_at_source (PSX Terminal serves literal "
             "0.0 when it has no dividend data — documented for "
             "LUCK/MARI in KNOWN_ISSUES)"),
        ]

        total_points = 0.0
        any_used = False
        for key, is_valid, higher_better, bad_reason in specs:
            values = [
                f[key] for f in peer_fundamentals.values()
                if is_valid(f.get(key))
            ]
            own_value = own.get(key)
            metric: dict = {
                "used": False,
                "value": own_value,
                "n_ranked": len(values),
                "percentile": None,
                "tilt": None,
                "reason": None,
            }
            if not is_valid(own_value):
                metric["reason"] = bad_reason
            elif len(values) < self.FUND_MIN_RANKED:
                metric["reason"] = (
                    f"insufficient_peers (need >= "
                    f"{self.FUND_MIN_RANKED} valid values, have "
                    f"{len(values)})"
                )
            else:
                pct = self._percentile_rank(own_value, values)
                tilt = (
                    (pct - 0.5) * 2 if higher_better else (0.5 - pct) * 2
                )
                metric.update(
                    used=True,
                    percentile=round(pct, 4),
                    tilt=round(tilt, 4),
                )
                total_points += tilt * self.FUND_METRIC_MAGNITUDE
                any_used = True
            detail["metrics"][key] = metric

        if not any_used:
            detail["skip_reason"] = "no_usable_metrics"
            return 0.0, detail

        contribution = max(-10.0, min(10.0, total_points))
        detail["used"] = True
        detail["combined_points"] = round(contribution, 2)
        return contribution, detail

    def _flow_contribution(
        self, sector_flows: dict, report_date: date
    ) -> tuple[float, dict]:
        """
        Deterministic sector-level FIPI/LIPI institutional-flow regime
        term.

        EXACT FORMULA (also stated in the module docstring and
        docs/BUILD_LOG.md):

            ratio = Σ net_value / Σ gross_value over the last ≤10 flow
                    trading days for the ticker's mapped NCCPL
                    sector(s)
            contribution = clamp(ratio / 0.125, −1, +1) × 10

        net/gross come pre-aggregated from the orchestrator's variant:
        FIPI + local-institutional LIPI (all non-retail client types),
        REG market, sector-wise datasets. gross = buy + |sell|, so the
        ratio is a flow-imbalance fraction in [−1, 1]. FLOW_SCALE
        0.125 ≈ the historical p90 of |ratio| (see constant comment).

        HONEST-ZERO paths, each with a distinct machine-readable
        reason persisted in the detail dict (a real near-zero flow
        reading must never look the same as "we didn't use this"):
          - no_flow_context            legacy caller, context absent
          - sector_not_covered_by_nccpl  e.g. ENGROH's "Investment
            Companies" has no named NCCPL sector; the "All other
            Sectors" catch-all is deliberately NOT used as a proxy
          - insufficient_flow_history  fewer than FLOW_MIN_DAYS days
          - stale_flow_data            MANDATORY staleness gate: the
            newest flow row used is > FLOW_STALE_DAYS (14) calendar
            days older than report_date. Threshold reasoning: the
            table is refreshed manually (~weekly); 14 days absorbs
            one missed refresh + holidays but silences a dead feed.
          - zero_gross_turnover        degenerate denominator
        """
        detail: dict = {
            "used": False,
            "skip_reason": None,
            "sector": sector_flows.get("sector"),
            "nccpl_sectors": sector_flows.get("nccpl_sectors"),
            "variant": sector_flows.get("variant"),
            "latest_flow_date": sector_flows.get("latest_flow_date"),
            "window_days": None,
            "window_start": None,
            "window_end": None,
            "net_value_pkr": None,
            "gross_value_pkr": None,
            "imbalance_ratio": None,
            "scale": self.FLOW_SCALE,
            "magnitude_points": self.FLOW_MAGNITUDE,
            "staleness_days": None,
            "stale_threshold_days": self.FLOW_STALE_DAYS,
        }

        if not sector_flows:
            detail["skip_reason"] = "no_flow_context"
            return 0.0, detail

        if not sector_flows.get("nccpl_sectors"):
            detail["skip_reason"] = "sector_not_covered_by_nccpl"
            return 0.0, detail

        daily = sector_flows.get("daily") or []
        if len(daily) < self.FLOW_MIN_DAYS:
            detail["skip_reason"] = (
                f"insufficient_flow_history (have {len(daily)} days, "
                f"need >= {self.FLOW_MIN_DAYS})"
            )
            return 0.0, detail

        window_end = max(d["date"] for d in daily)
        window_start = min(d["date"] for d in daily)
        staleness_days = (
            report_date - date.fromisoformat(window_end)
        ).days
        detail.update(
            window_days=len(daily),
            window_start=window_start,
            window_end=window_end,
            staleness_days=staleness_days,
        )

        if staleness_days > self.FLOW_STALE_DAYS:
            detail["skip_reason"] = (
                f"stale_flow_data (newest flow row {window_end} is "
                f"{staleness_days} days before report_date "
                f"{report_date}; threshold {self.FLOW_STALE_DAYS})"
            )
            logger.warning(
                f"flow_contribution: stale data, not used — newest "
                f"row {window_end}, {staleness_days}d old "
                f"(threshold {self.FLOW_STALE_DAYS}d)"
            )
            return 0.0, detail

        net = sum(d["net_value"] for d in daily)
        gross = sum(d["gross_value"] for d in daily)
        detail.update(
            net_value_pkr=round(net, 2),
            gross_value_pkr=round(gross, 2),
        )
        if gross <= 0:
            detail["skip_reason"] = "zero_gross_turnover"
            return 0.0, detail

        ratio = net / gross
        scaled = max(-1.0, min(1.0, ratio / self.FLOW_SCALE))
        contribution = scaled * self.FLOW_MAGNITUDE
        detail.update(
            used=True,
            imbalance_ratio=round(ratio, 4),
        )
        return contribution, detail

    def _build_score_breakdown(
        self,
        trend: dict,
        news: dict,
        filings: dict,
        ml_signal: dict,
        fund: tuple[float, dict],
        flow: tuple[float, dict],
    ) -> dict:
        """
        Score-breakdown dict persisted on the IntelligenceReport.
        Includes enough ML context that a human can tell "real bullish
        signal" from "model unavailable" from "below confidence
        threshold" — analogous to how news_contribution=0 could mean
        "real neutral" or "no articles" (open issue documented in
        docs/KNOWN_ISSUES.md), here we surface the *reason* explicitly.
        """
        breakdown: dict = {
            "technical_contribution": round(
                self._technical_contribution(trend), 2
            ),
            "news_contribution": round(self._news_contribution(news), 2),
            "filing_contribution": round(
                self._filing_contribution(filings), 2
            ),
            "ml_contribution": round(self._ml_contribution(ml_signal), 2),
            # Phase 5 Session 8 — deterministic tilts, computed once
            # in run() and passed in as (points, detail) tuples. The
            # detail blocks are always present (even at 0.0) so a
            # consumer can tell a real neutral reading from an
            # excluded/stale/unmapped honest zero.
            "fundamentals_contribution": round(fund[0], 2),
            "flow_contribution": round(flow[0], 2),
            "fundamentals_detail": fund[1],
            "flow_detail": flow[1],
        }

        # ML detail block — always present, even when 0, so consumers
        # don't have to guess WHY the term was zeroed out.
        ml_detail: dict = {
            "gate_passed": bool(ml_signal.get("gate_passed", False)),
            "skip_reason": ml_signal.get("skip_reason"),
            "predicted_class": ml_signal.get("predicted_class"),
            "max_prob": ml_signal.get("max_prob"),
            "probabilities": ml_signal.get("probabilities"),
            "confidence_threshold": ml_signal.get(
                "confidence_threshold", self.ML_GATE
            ),
            "as_of_date": ml_signal.get("as_of_date"),
            "magnitude_points": self.ML_MAGNITUDE,
            "model_caveat": (
                "Technical-only XGBoost; test accuracy ~39% vs ~33% "
                "random baseline. Never predicts FLAT. Treat as a "
                "minor input, not a primary driver."
            ),
        }
        breakdown["ml_detail"] = ml_detail
        return breakdown

    def _score_to_label(self, score: float) -> str:
        for threshold, label in self.SCORE_LABELS:
            if score >= threshold:
                return label
        return "STRONG_SELL"

    def _build_prompt(
        self,
        ticker: str,
        company_name: str,
        trend: dict,
        news: dict,
        filings: dict,
        ml_signal: dict,
        fund: tuple[float, dict],
        flow: tuple[float, dict],
        score: float,
    ) -> str:
        tech_signal = trend.get("signal", "NEUTRAL")
        tech_confidence = trend.get("confidence", 0)
        tech_reasoning = trend.get(
            "reasoning", "No technical analysis available."
        )

        news_label = news.get("sentiment", "NEUTRAL")
        news_confidence = news.get("confidence", 0)
        news_summary = news.get(
            "narrative_summary", "No news analysis available."
        )

        red_flags = filings.get("red_flags", [])
        red_flags_str = (
            ", ".join(red_flags) if red_flags else "None identified"
        )
        filing_analysis = filings.get(
            "filing_analysis", "No filing data available."
        )

        ml_block = self._render_ml_block(ml_signal)
        tilts_block = self._render_tilts_block(fund, flow)

        return (
            f"You are the Chief Investment Strategist synthesizing "
            f"research from three specialist analysts on "
            f"{ticker} ({company_name}).\n\n"
            f"TECHNICAL ANALYSIS:\n"
            f"Signal: {tech_signal}, "
            f"Confidence: {tech_confidence:.2f}\n"
            f"{tech_reasoning}\n\n"
            f"NEWS SENTIMENT:\n"
            f"Sentiment: {news_label}, "
            f"Confidence: {news_confidence:.2f}\n"
            f"{news_summary}\n\n"
            f"FILING REVIEW:\n"
            f"Red Flags: {red_flags_str}\n"
            f"{filing_analysis}\n\n"
            f"{ml_block}\n"
            f"{tilts_block}\n"
            f"CALCULATED CONVICTION SCORE: {score:.1f}/100\n\n"
            f"Write a balanced investment brief:\n"
            f"1. BULL_CASE: 2-3 sentences on why this could be a good "
            f"opportunity, grounded in the actual data above\n"
            f"2. BEAR_CASE: 2-3 sentences on the risks and why caution "
            f"is warranted\n"
            f"3. RISK_FACTORS: exactly 3 specific, concrete risks (not "
            f"generic statements like 'market risk')\n\n"
            f"Be honest and balanced. If the data suggests caution, the "
            f"bull_case can be brief and the bear_case can dominate. "
            f"The ML PRICE-DIRECTION MODEL above is a weak technical "
            f"signal — historically correct only ~6 percentage points "
            f"more often than chance on a 3-class problem, and it "
            f"never predicts FLAT. Treat it as a minor input that can "
            f"reinforce or qualify the other analysts' views, NEVER as "
            f"a strong conviction driver on its own.\n\n"
            f"Respond in this exact format:\n"
            f"BULL_CASE: <text>\n"
            f"BEAR_CASE: <text>\n"
            f"RISK_FACTORS: <risk 1> | <risk 2> | <risk 3>"
        )

    def _render_ml_block(self, ml_signal: dict) -> str:
        """
        Render the ML price-direction signal as a clearly-labeled
        prompt section. Always emits the section header so the model
        sees a consistent prompt shape across tickers, even when the
        signal is missing or zeroed out.
        """
        if not ml_signal or not ml_signal.get("available"):
            reason = (ml_signal or {}).get(
                "skip_reason", "not_available"
            )
            return (
                "ML PRICE-DIRECTION MODEL (WEAK SIGNAL — see caveat below):\n"
                f"No usable prediction this run (reason: {reason}).\n"
            )

        predicted = ml_signal.get("predicted_class", "?")
        max_prob = ml_signal.get("max_prob", 0.0) or 0.0
        probs = ml_signal.get("probabilities") or {}
        threshold = ml_signal.get(
            "confidence_threshold", self.ML_GATE
        )
        as_of = ml_signal.get("as_of_date", "?")
        gate_passed = ml_signal.get("gate_passed", False)

        probs_str = ", ".join(
            f"{cls}={probs.get(cls, 0.0):.2f}"
            for cls in ("DOWN", "FLAT", "UP")
        )

        gate_line = (
            f"Confidence gate (>{threshold:.2f}): "
            f"{'PASSED' if gate_passed else 'FAILED — contributing 0 to score'}"
        )

        return (
            "ML PRICE-DIRECTION MODEL (WEAK SIGNAL — see caveat below):\n"
            f"5-day-ahead predicted class: {predicted} "
            f"(p={max_prob:.2f})\n"
            f"All class probabilities: {probs_str}\n"
            f"{gate_line}\n"
            f"As of trading day: {as_of}\n"
        )

    def _render_tilts_block(
        self, fund: tuple[float, dict], flow: tuple[float, dict]
    ) -> str:
        """
        Render the two deterministic Session 8 tilts for the narrative
        prompt. Always emitted (consistent prompt shape), always
        honest: when a term is an honest zero the LLM is told the
        machine-readable reason rather than being left to invent one.
        These are already inside the calculated score — the LLM
        interprets them, it does not re-apply them.
        """
        fund_points, fund_detail = fund
        flow_points, flow_detail = flow

        if fund_detail.get("used"):
            metrics = fund_detail.get("metrics", {})
            parts = []
            for key, label in (
                ("pe_ratio", "P/E"),
                ("dividend_yield", "dividend yield"),
            ):
                m = metrics.get(key, {})
                if m.get("used"):
                    parts.append(
                        f"{label} percentile {m['percentile']:.2f} "
                        f"vs peers"
                    )
                else:
                    parts.append(f"{label} excluded (bad/missing data)")
            fund_line = (
                f"{fund_points:+.1f} points ({'; '.join(parts)}; "
                f"small ~10-ticker peer universe — weak evidence)"
            )
        else:
            fund_line = (
                f"0.0 points (not used: "
                f"{fund_detail.get('skip_reason')})"
            )

        if flow_detail.get("used"):
            flow_line = (
                f"{flow_points:+.1f} points (sector "
                f"{flow_detail.get('nccpl_sectors')}, "
                f"{flow_detail.get('window_days')}-day foreign + "
                f"local-institutional net/gross imbalance "
                f"{flow_detail.get('imbalance_ratio'):+.3f}, data "
                f"through {flow_detail.get('window_end')})"
            )
        else:
            flow_line = (
                f"0.0 points (not used: "
                f"{flow_detail.get('skip_reason')})"
            )

        return (
            "DETERMINISTIC SCORE TILTS (already included in the "
            "calculated score below — interpret, do not re-apply):\n"
            f"Fundamentals value tilt vs peer universe: {fund_line}\n"
            f"Sector institutional-flow regime: {flow_line}\n"
        )

    def _parse_response(self, content: str) -> dict:
        result: dict = {
            "bull_case": (
                "Unable to generate bull case from available data."
            ),
            "bear_case": (
                "Unable to generate bear case from available data."
            ),
            "risk_factors": [
                "Limited data availability",
                "Market volatility",
                "Incomplete analysis coverage",
            ],
        }

        for line in content.split("\n"):
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("BULL_CASE:"):
                val = stripped[len("BULL_CASE:"):].strip()
                if val:
                    result["bull_case"] = val

            elif upper.startswith("BEAR_CASE:"):
                val = stripped[len("BEAR_CASE:"):].strip()
                if val:
                    result["bear_case"] = val

            elif upper.startswith("RISK_FACTORS:"):
                raw = stripped[len("RISK_FACTORS:"):].strip()
                factors = [f.strip() for f in raw.split("|") if f.strip()]
                if factors:
                    result["risk_factors"] = factors

        return result
