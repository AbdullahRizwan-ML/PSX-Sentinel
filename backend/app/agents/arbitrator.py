"""
PSX Sentinel — Arbitrator Agent

Synthesis agent that combines outputs from TrendAnalyzer, NewsSynthesizer,
and FilingSceptic into a final conviction score using confidence-weighted
deterministic scoring, then calls the LLM once for narrative generation.

The conviction score is computed in Python first — the LLM interprets
the pre-computed score and writes bull/bear cases. It does not decide
the number itself. This is the confidence-weighted synthesis pattern:
each upstream agent's contribution is multiplied by its self-reported
confidence, so low-quality data automatically dampens its influence.
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

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        trend = context.trend_signals
        news = context.news_sentiment
        filings = context.filing_flags

        score = self._calculate_score(trend, news, filings)
        signal_label = self._score_to_label(score)

        prompt = self._build_prompt(
            context.ticker,
            context.company_name,
            trend,
            news,
            filings,
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
                "score_breakdown": {
                    "technical_contribution": round(
                        self._technical_contribution(trend), 2
                    ),
                    "news_contribution": round(
                        self._news_contribution(news), 2
                    ),
                    "filing_contribution": round(
                        self._filing_contribution(filings), 2
                    ),
                    "ml_contribution": 0.0,
                },
            },
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _calculate_score(
        self, trend: dict, news: dict, filings: dict
    ) -> float:
        score = 50.0
        score += self._technical_contribution(trend)
        score += self._news_contribution(news)
        score += self._filing_contribution(filings)
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
            f"CALCULATED CONVICTION SCORE: {score:.1f}/100\n\n"
            f"Write a balanced investment brief:\n"
            f"1. BULL_CASE: 2-3 sentences on why this could be a good "
            f"opportunity, grounded in the actual data above\n"
            f"2. BEAR_CASE: 2-3 sentences on the risks and why caution "
            f"is warranted\n"
            f"3. RISK_FACTORS: exactly 3 specific, concrete risks (not "
            f"generic statements like 'market risk')\n\n"
            f"Be honest and balanced. If the data suggests caution, the "
            f"bull_case can be brief and the bear_case can dominate.\n\n"
            f"Respond in this exact format:\n"
            f"BULL_CASE: <text>\n"
            f"BEAR_CASE: <text>\n"
            f"RISK_FACTORS: <risk 1> | <risk 2> | <risk 3>"
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
