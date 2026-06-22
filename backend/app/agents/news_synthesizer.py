"""
PSX Sentinel — NewsSynthesizer Agent

Analyzes news sentiment and narrative consensus for a company.
Skips the LLM call entirely when there are no news articles,
saving tokens and preventing hallucination from empty context.

The LLM prompt explicitly instructs relevance filtering to address
the known issue where keyword matching produces false-positive
ticker associations (e.g., general "petroleum" headlines → PPL/PSO).
"""

import time

from loguru import logger

from app.agents.base import AgentContext, AgentResult, BaseAgent


class NewsSynthesizer(BaseAgent):
    name = "news_synthesizer"
    max_tokens = 1000
    timeout_seconds = 30

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        articles = context.news_articles

        if not articles:
            return AgentResult(
                agent_name=self.name,
                success=True,
                output={
                    "sentiment": "NEUTRAL",
                    "uniformity": "N/A",
                    "relevant_articles": 0,
                    "article_count": 0,
                    "narrative_summary": (
                        f"No recent news coverage found for {context.ticker}."
                    ),
                },
                confidence=0.3,
                tokens_used=0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

        news_text = self._build_news_block(articles)

        prompt = self._build_prompt(
            context.ticker, context.company_name, news_text, len(articles)
        )

        response = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name=self.name,
            analysis_id=context.analysis_id,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        parsed = self._parse_response(
            response.content, context.ticker, len(articles)
        )

        relevant = parsed["relevant_articles"]
        if relevant == 0:
            confidence = 0.3
        elif relevant <= 2:
            confidence = 0.5
        elif relevant <= 5:
            confidence = 0.65
        else:
            confidence = 0.8

        return AgentResult(
            agent_name=self.name,
            success=True,
            output=parsed,
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _build_news_block(self, articles: list[dict]) -> str:
        sorted_articles = sorted(
            articles,
            key=lambda a: a.get("published_at", ""),
            reverse=True,
        )[:10]

        lines: list[str] = []
        total_chars = 0
        for article in sorted_articles:
            headline = article.get("headline", "No headline")
            summary = article.get("summary", "")
            source = article.get("source", "unknown")
            published = str(article.get("published_at", ""))[:10]

            entry = f"[{published}] ({source}) {headline}"
            if summary:
                entry += f"\n  {summary[:200]}"

            if total_chars + len(entry) > 3000:
                break

            lines.append(entry)
            total_chars += len(entry)

        return "\n\n".join(lines)

    def _build_prompt(
        self,
        ticker: str,
        company_name: str,
        news_text: str,
        total: int,
    ) -> str:
        return (
            f"You are a financial news analyst reviewing recent coverage "
            f"of {ticker} ({company_name}).\n\n"
            f"IMPORTANT: Some headlines may be about general market topics "
            f"(oil prices, currency rates, budget news) rather than this "
            f"specific company. Evaluate whether each headline is genuinely "
            f"about {company_name}'s business or just a tangential keyword "
            f"match, and weight your analysis accordingly.\n\n"
            f"Recent headlines and summaries ({total} articles):\n"
            f"{news_text}\n\n"
            f"Respond in this exact format:\n"
            f"SENTIMENT: <BULLISH/BEARISH/NEUTRAL>\n"
            f"UNIFORMITY: <HIGH/MEDIUM/LOW>\n"
            f"RELEVANT_ARTICLES: <count of articles genuinely about "
            f"this company>\n"
            f"SUMMARY: <2-3 sentence summary>"
        )

    def _parse_response(
        self, content: str, ticker: str, total_articles: int
    ) -> dict:
        result: dict = {
            "sentiment": "NEUTRAL",
            "uniformity": "MEDIUM",
            "relevant_articles": 0,
            "article_count": total_articles,
            "narrative_summary": content.strip(),
        }

        valid_sentiments = {"BULLISH", "BEARISH", "NEUTRAL"}
        valid_uniformity = {"HIGH", "MEDIUM", "LOW"}

        for line in content.split("\n"):
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("SENTIMENT:"):
                val = stripped[len("SENTIMENT:"):].strip().upper()
                if val in valid_sentiments:
                    result["sentiment"] = val
                else:
                    logger.warning(
                        f"{self.name}: unrecognized sentiment '{val}', "
                        f"defaulting to NEUTRAL"
                    )

            elif upper.startswith("UNIFORMITY:"):
                val = stripped[len("UNIFORMITY:"):].strip().upper()
                if val in valid_uniformity:
                    result["uniformity"] = val

            elif upper.startswith("RELEVANT_ARTICLES:"):
                val = stripped[len("RELEVANT_ARTICLES:"):].strip()
                try:
                    count = int(val)
                    result["relevant_articles"] = min(count, total_articles)
                except ValueError:
                    logger.warning(
                        f"{self.name}: could not parse "
                        f"relevant_articles '{val}'"
                    )

            elif upper.startswith("SUMMARY:"):
                result["narrative_summary"] = (
                    stripped[len("SUMMARY:"):].strip()
                )

        if result["relevant_articles"] == 0 and total_articles > 0:
            result["narrative_summary"] = (
                f"Articles found but none specifically relevant "
                f"to {ticker}. {result['narrative_summary']}"
            )

        return result
