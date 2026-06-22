"""
PSX Sentinel — TrendAnalyzer Agent

Computes technical indicators in pure Python, then uses ONE LLM call
to interpret the numbers into a directional signal with reasoning.
The LLM interprets pre-computed numbers — it does not calculate them.
"""

import time

from loguru import logger

from app.agents.base import AgentContext, AgentResult, BaseAgent


class TrendAnalyzer(BaseAgent):
    name = "trend_analyzer"
    max_tokens = 800
    timeout_seconds = 30

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        prices = context.recent_prices
        if len(prices) < 5:
            return AgentResult(
                agent_name=self.name,
                success=True,
                output={
                    "signal": "NEUTRAL",
                    "reasoning": (
                        f"Insufficient price data ({len(prices)} points). "
                        f"At least 5 trading days required for meaningful analysis."
                    ),
                    "technical_summary": {},
                },
                confidence=0.2,
                tokens_used=0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

        technical_summary = self._build_technical_summary(context)

        prompt = self._build_prompt(context.ticker, technical_summary)
        response = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name=self.name,
            analysis_id=context.analysis_id,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        signal, reasoning = self._parse_response(response.content)

        data_points = technical_summary.get("data_points_used", 0)
        if data_points >= 200:
            confidence = 0.85
        elif data_points >= 50:
            confidence = 0.7
        elif data_points >= 20:
            confidence = 0.55
        else:
            confidence = 0.4

        return AgentResult(
            agent_name=self.name,
            success=True,
            output={
                "signal": signal,
                "reasoning": reasoning,
                "technical_summary": technical_summary,
            },
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _calculate_rsi(self, prices: list[float], period: int = 14) -> float:
        """Standard Wilder-smoothed RSI. Returns 50.0 if insufficient data."""
        if len(prices) < period + 1:
            return 50.0

        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calculate_moving_average(
        self, prices: list[float], window: int
    ) -> float | None:
        """Simple moving average over the last `window` prices."""
        if len(prices) < window:
            return None
        return sum(prices[-window:]) / window

    def _build_technical_summary(self, context: AgentContext) -> dict:
        sorted_prices = sorted(
            context.recent_prices, key=lambda p: p.get("date", "")
        )

        closes = [
            p["close"] for p in sorted_prices if p.get("close") is not None
        ]
        volumes = [
            p["volume"] for p in sorted_prices if p.get("volume") is not None
        ]

        if not closes:
            return {"data_points_used": 0}

        current_price = closes[-1]

        ma_20 = self._calculate_moving_average(closes, 20)
        ma_50 = self._calculate_moving_average(closes, 50)

        rsi_14 = self._calculate_rsi(closes, 14)

        def pct_change(n: int) -> float | None:
            if len(closes) <= n:
                return None
            old = closes[-(n + 1)]
            if old == 0:
                return None
            return ((current_price - old) / old) * 100.0

        momentum_1w = pct_change(5)
        momentum_1m = pct_change(21)
        momentum_3m = pct_change(63)

        volume_trend: float | None = None
        if len(volumes) >= 30:
            avg_5 = sum(volumes[-5:]) / 5
            avg_30 = sum(volumes[-30:]) / 30
            if avg_30 > 0:
                volume_trend = round(avg_5 / avg_30, 2)
        elif len(volumes) >= 5:
            avg_5 = sum(volumes[-5:]) / 5
            avg_all = sum(volumes) / len(volumes)
            if avg_all > 0:
                volume_trend = round(avg_5 / avg_all, 2)

        position_52w: float | None = None
        high_52w = max(closes)
        low_52w = min(closes)
        if high_52w > low_52w:
            position_52w = round(
                (current_price - low_52w) / (high_52w - low_52w), 3
            )
        elif high_52w == low_52w:
            position_52w = 0.5

        summary: dict = {
            "current_price": round(current_price, 2),
            "data_points_used": len(closes),
        }

        if ma_20 is not None:
            summary["ma_20"] = round(ma_20, 2)
            summary["price_vs_ma20_pct"] = (
                round(((current_price - ma_20) / ma_20) * 100, 2)
                if ma_20 != 0
                else 0.0
            )
        if ma_50 is not None:
            summary["ma_50"] = round(ma_50, 2)
            summary["price_vs_ma50_pct"] = (
                round(((current_price - ma_50) / ma_50) * 100, 2)
                if ma_50 != 0
                else 0.0
            )

        summary["rsi_14"] = round(rsi_14, 2)

        if momentum_1w is not None:
            summary["momentum_1w"] = round(momentum_1w, 2)
        if momentum_1m is not None:
            summary["momentum_1m"] = round(momentum_1m, 2)
        if momentum_3m is not None:
            summary["momentum_3m"] = round(momentum_3m, 2)

        if volume_trend is not None:
            summary["volume_trend"] = volume_trend

        if position_52w is not None:
            summary["position_in_52w_range"] = position_52w

        return summary

    def _build_prompt(self, ticker: str, ts: dict) -> str:
        lines = [
            f"You are a quantitative technical analyst. Based on these "
            f"indicators for {ticker}, provide a technical_signal "
            f"(one of: STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL) "
            f"and a brief 2-3 sentence rationale.",
            "",
            "Technical Data:",
            f"- Current price: {ts.get('current_price', 'N/A')} PKR",
        ]

        if "ma_20" in ts:
            lines.append(
                f"- 20-day MA: {ts['ma_20']} "
                f"(price is {ts.get('price_vs_ma20_pct', 'N/A')}% vs MA)"
            )
        else:
            lines.append("- 20-day MA: insufficient data")

        if "ma_50" in ts:
            lines.append(
                f"- 50-day MA: {ts['ma_50']} "
                f"(price is {ts.get('price_vs_ma50_pct', 'N/A')}% vs MA)"
            )
        else:
            lines.append("- 50-day MA: insufficient data")

        lines.append(
            f"- RSI(14): {ts.get('rsi_14', 'N/A')} "
            f"(>70 overbought, <30 oversold)"
        )

        m1w = ts.get("momentum_1w", "N/A")
        m1m = ts.get("momentum_1m", "N/A")
        m3m = ts.get("momentum_3m", "N/A")
        lines.append(
            f"- Momentum: 1wk {m1w}%, 1mo {m1m}%, 3mo {m3m}%"
        )

        lines.append(
            f"- Volume trend: {ts.get('volume_trend', 'N/A')}x normal"
        )
        lines.append(
            f"- Position in 52-week range: "
            f"{ts.get('position_in_52w_range', 'N/A')} (0=low, 1=high)"
        )

        lines.extend([
            "",
            "Respond in this exact format:",
            "SIGNAL: <signal>",
            "REASONING: <your reasoning>",
        ])

        return "\n".join(lines)

    def _parse_response(self, content: str) -> tuple[str, str]:
        valid_signals = {
            "STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"
        }
        signal = "NEUTRAL"
        reasoning = content.strip()

        for line in content.split("\n"):
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("SIGNAL:"):
                parsed = stripped[len("SIGNAL:"):].strip().upper()
                if parsed in valid_signals:
                    signal = parsed
                else:
                    logger.warning(
                        f"{self.name}: unrecognized signal '{parsed}', "
                        f"defaulting to NEUTRAL"
                    )
            elif upper.startswith("REASONING:"):
                reasoning = stripped[len("REASONING:"):].strip()

        return signal, reasoning
