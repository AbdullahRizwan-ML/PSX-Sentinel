"""
PSX Sentinel — FilingSceptic Agent

Red-team analysis of company filings. Acts as a skeptical auditor
looking for red flags in quarterly results and corporate disclosures.

Currently returns a no-data result because PSX announcement scraping
(PUCARS/JS-rendered portal) is not yet functional. The agent is honest
about this limitation rather than fabricating analysis from nothing.
When filing data becomes available via Playwright scraping, the full
LLM analysis path is ready to activate.
"""

import time

from loguru import logger

from app.agents.base import AgentContext, AgentResult, BaseAgent


class FilingSceptic(BaseAgent):
    name = "filing_skeptic"
    max_tokens = 1000
    timeout_seconds = 35

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        filings = [
            a
            for a in context.announcements
            if a.get("raw_text") and len(a["raw_text"].strip()) > 0
        ]

        if not filings:
            return AgentResult(
                agent_name=self.name,
                success=True,
                output={
                    "red_flags": [],
                    "severity": "NONE",
                    "filing_analysis": (
                        "No corporate filings available for analysis. "
                        "This agent requires PUCARS announcement scraping "
                        "(planned for a future phase) to perform "
                        "filing-based risk assessment."
                    ),
                    "data_availability": "NONE",
                    "filings_reviewed": 0,
                },
                confidence=0.2,
                tokens_used=0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

        filing_text = self._build_filing_block(filings)

        prompt = self._build_prompt(context.ticker, filing_text)

        response = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name=self.name,
            analysis_id=context.analysis_id,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        parsed = self._parse_response(response.content)
        parsed["filings_reviewed"] = len(filings)
        parsed["data_availability"] = "AVAILABLE"

        if parsed["red_flags"]:
            confidence = 0.75
        else:
            confidence = 0.6

        return AgentResult(
            agent_name=self.name,
            success=True,
            output=parsed,
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _build_filing_block(self, filings: list[dict]) -> str:
        sorted_filings = sorted(
            filings,
            key=lambda f: f.get("announced_at", ""),
            reverse=True,
        )

        most_recent = sorted_filings[0]
        text = most_recent.get("raw_text", "")
        title = most_recent.get("title", "Unknown filing")
        category = most_recent.get("category", "UNKNOWN")
        quarter = most_recent.get("fiscal_quarter", "?")
        year = most_recent.get("fiscal_year", "?")

        header = (
            f"Filing: {title} (Category: {category}, Q{quarter} {year})"
        )

        if len(text) > 4000:
            text = text[:4000] + "\n[... truncated]"

        return f"{header}\n\n{text}"

    def _build_prompt(self, ticker: str, filing_text: str) -> str:
        return (
            f"You are a skeptical financial auditor reviewing a quarterly "
            f"result filing for {ticker}. Your job is to find problems, "
            f"not validate the positive narrative. Read this filing excerpt "
            f"and identify any red flags:\n\n"
            f"{filing_text}\n\n"
            f"Look specifically for:\n"
            f"- Profit growth without matching cash flow growth\n"
            f"- Rising receivables or inventory "
            f"(potential channel stuffing)\n"
            f"- Vague language around risks or one-time items\n"
            f"- Any qualification or going-concern language\n\n"
            f"Respond in this exact format:\n"
            f"RED_FLAGS: <comma-separated list, or NONE if no concerns>\n"
            f"SEVERITY: <LOW/MEDIUM/HIGH>\n"
            f"ANALYSIS: <2-3 sentence summary of your findings>"
        )

    def _parse_response(self, content: str) -> dict:
        result: dict = {
            "red_flags": [],
            "severity": "LOW",
            "filing_analysis": content.strip(),
        }

        valid_severities = {"LOW", "MEDIUM", "HIGH"}

        for line in content.split("\n"):
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("RED_FLAGS:"):
                val = stripped[len("RED_FLAGS:"):].strip()
                if val.upper() == "NONE":
                    result["red_flags"] = []
                else:
                    flags = [
                        f.strip() for f in val.split(",") if f.strip()
                    ]
                    result["red_flags"] = flags

            elif upper.startswith("SEVERITY:"):
                val = stripped[len("SEVERITY:"):].strip().upper()
                if val in valid_severities:
                    result["severity"] = val
                else:
                    logger.warning(
                        f"{self.name}: unrecognized severity '{val}', "
                        f"defaulting to LOW"
                    )

            elif upper.startswith("ANALYSIS:"):
                result["filing_analysis"] = (
                    stripped[len("ANALYSIS:"):].strip()
                )

        return result
