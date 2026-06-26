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

Why ML gets only 5 (not the originally-planned 15):
    The trained XGBoost model scored +6pp over a 3-class random
    baseline in Phase 3 Session 2 — a real but very thin edge, and
    structurally FLAT-blind. Giving it 15% would let a low-conviction
    technical signal dominate. 5%, plus the 0.55 confidence gate,
    matches the Session 2 recommendation: small voice, only speaks
    when relatively sure, never dominant. See docs/BUILD_LOG.md
    Phase 3 Session 2 for the full evaluation that drove this choice.
"""

import time

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

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        trend = context.trend_signals
        news = context.news_sentiment
        filings = context.filing_flags
        ml_signal = context.ml_signal or {}

        score = self._calculate_score(trend, news, filings, ml_signal)
        signal_label = self._score_to_label(score)

        prompt = self._build_prompt(
            context.ticker,
            context.company_name,
            trend,
            news,
            filings,
            ml_signal,
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
                    trend, news, filings, ml_signal
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
    ) -> float:
        score = 50.0
        score += self._technical_contribution(trend)
        score += self._news_contribution(news)
        score += self._filing_contribution(filings)
        score += self._ml_contribution(ml_signal)
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

    def _build_score_breakdown(
        self,
        trend: dict,
        news: dict,
        filings: dict,
        ml_signal: dict,
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
