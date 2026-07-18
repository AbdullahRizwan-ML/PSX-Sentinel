"""
PSX Sentinel — FilingSceptic Agent

Red-team analysis of company disclosures. Acts as a skeptical auditor
looking for red flags in recent corporate announcements.

Phase 5 Session 7: wired to real data for the first time. Announcements
are mirrored from PSX Terminal (title/date/category/pdf_url, Phase 5
Session 2) and the PDFParser collector extracts document text into
Announcement.raw_text where the PDF has a text layer (~61% of the live
corpus at wiring time; the rest are image-only scans — mostly
"Disclosure of Interest" notices). This agent reviews the most recent
announcements as a single batch, per announcement in one of two modes:

  - full_text:  extracted PDF text (excerpted, budget-capped) is shown
                to the LLM alongside the title
  - title_only: image-only/missing PDFs fall back to the bare title,
                and the LLM is explicitly told not to speculate about
                document contents it cannot see
  (- text_omitted: text exists but the prompt budget was already spent
                on more recent documents — rare, logged honestly)

The per-announcement mode is recorded in the agent output ("reviewed")
so the visibility level behind every analysis is auditable later, not
hidden.

When no announcements exist at all, the agent returns a low-confidence
no-data result WITHOUT calling the LLM (project hard rule: agents never
fabricate analysis from nothing).
"""

import time

from loguru import logger

from app.agents.base import AgentContext, AgentResult, BaseAgent


class FilingSceptic(BaseAgent):
    name = "filing_skeptic"
    max_tokens = 1000
    timeout_seconds = 35

    # How many of the most recent announcements to review. The PSX
    # Terminal mirror serves ~10 per ticker, so this covers the whole
    # rolling window today while bounding the prompt if a deeper source
    # (PUCARS) ever lands.
    MAX_FILINGS = 10
    # Per-announcement extracted-text excerpt cap (chars) and the total
    # text budget across the batch. Most recent documents are served
    # first, so the budget favors recency.
    PER_FILING_TEXT_CAP = 1500
    TOTAL_TEXT_BUDGET = 9000

    async def run(self, context: AgentContext) -> AgentResult:
        start = time.monotonic()

        announcements = context.announcements or []

        if not announcements:
            return AgentResult(
                agent_name=self.name,
                success=True,
                output={
                    "red_flags": [],
                    "severity": "NONE",
                    "filing_analysis": (
                        "No corporate announcements available for "
                        "analysis — the announcement mirror has no rows "
                        "for this ticker, so no filing-based risk "
                        "assessment is possible."
                    ),
                    "data_availability": "NONE",
                    "filings_reviewed": 0,
                    "full_text_count": 0,
                    "title_only_count": 0,
                    "reviewed": [],
                },
                confidence=0.2,
                tokens_used=0,
                latency_ms=int((time.monotonic() - start) * 1000),
            )

        filings = sorted(
            announcements,
            key=lambda f: f.get("announced_at", ""),
            reverse=True,
        )[: self.MAX_FILINGS]

        filing_block, reviewed = self._build_filing_block(filings)
        full_text_count = sum(
            1 for r in reviewed if r["mode"] == "full_text"
        )
        title_only_count = sum(
            1 for r in reviewed if r["mode"] == "title_only"
        )

        prompt = self._build_prompt(
            context.ticker,
            context.company_name,
            filing_block,
            n_filings=len(reviewed),
            n_full_text=full_text_count,
            n_title_only=len(reviewed) - full_text_count,
        )

        response = await self.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name=self.name,
            analysis_id=context.analysis_id,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
        )

        parsed = self._parse_response(response.content)
        parsed["filings_reviewed"] = len(reviewed)
        parsed["full_text_count"] = full_text_count
        parsed["title_only_count"] = title_only_count
        parsed["reviewed"] = reviewed
        if full_text_count == 0:
            parsed["data_availability"] = "TITLES_ONLY"
        elif title_only_count == 0:
            parsed["data_availability"] = "FULL_TEXT"
        else:
            parsed["data_availability"] = "PARTIAL_TEXT"

        if parsed["red_flags"]:
            confidence = 0.75
        else:
            confidence = 0.6
        if full_text_count == 0:
            # Every document was title-only: the LLM judged headlines,
            # not filings. Honest cap — visibly weaker evidence.
            confidence = min(confidence, 0.45)

        return AgentResult(
            agent_name=self.name,
            success=True,
            output=parsed,
            confidence=confidence,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    def _build_filing_block(
        self, filings: list[dict]
    ) -> tuple[str, list[dict]]:
        """
        Render the batch of announcements for the prompt and return the
        parallel per-announcement review log.

        Text excerpts are whitespace-collapsed, capped per filing, and
        drawn from a shared budget that most-recent documents consume
        first. Mode per announcement:
          full_text    — excerpt included in the prompt
          title_only   — no extractable text exists (image-only PDF or
                         no PDF at all)
          text_omitted — text exists but the shared budget was already
                         spent on more recent documents
        """
        lines: list[str] = []
        reviewed: list[dict] = []
        budget = self.TOTAL_TEXT_BUDGET

        for i, f in enumerate(filings, 1):
            title = (f.get("title") or "Untitled announcement").strip()
            announced = (f.get("announced_at") or "")[:10]
            category = f.get("category") or "OTHER"
            raw = (f.get("raw_text") or "").strip()

            entry = f"[{i}] {announced} ({category}) {title}"
            if raw and budget > 200:
                excerpt = " ".join(raw.split())
                cap = min(self.PER_FILING_TEXT_CAP, budget)
                if len(excerpt) > cap:
                    excerpt = excerpt[:cap] + " [...truncated]"
                budget -= len(excerpt)
                mode = "full_text"
                entry += (
                    f"\n    DOCUMENT TEXT (extracted from PDF): {excerpt}"
                )
            elif raw:
                mode = "text_omitted"
                entry += (
                    "\n    [TEXT OMITTED — prompt budget spent on more "
                    "recent documents; judge from the title only]"
                )
            else:
                mode = "title_only"
                entry += (
                    "\n    [TITLE ONLY — no readable document text "
                    "(image-only or missing PDF)]"
                )

            lines.append(entry)
            reviewed.append(
                {
                    "date": announced,
                    "category": category,
                    "title": title[:120],
                    "mode": mode,
                }
            )

        return "\n\n".join(lines), reviewed

    def _build_prompt(
        self,
        ticker: str,
        company_name: str,
        filing_block: str,
        n_filings: int,
        n_full_text: int,
        n_title_only: int,
    ) -> str:
        return (
            f"You are a skeptical financial auditor reviewing the most "
            f"recent corporate disclosures of {ticker} ({company_name}), "
            f"listed on the Pakistan Stock Exchange. Your job is to find "
            f"genuine problems, not to validate a positive narrative — "
            f"but equally, do NOT manufacture concerns out of routine "
            f"corporate housekeeping.\n\n"
            f"THE DISCLOSURES ({n_filings} most recent; {n_full_text} "
            f"with document text, {n_title_only} title-only):\n\n"
            f"{filing_block}\n\n"
            f"Look for genuine red flags such as:\n"
            f"- Related-party transactions on unusual terms\n"
            f"- Going-concern language, audit qualifications, or "
            f"auditor changes/resignations\n"
            f"- Sudden or unexplained departures of the CEO, CFO, or "
            f"board members\n"
            f"- Regulatory penalties, investigations, or court actions\n"
            f"- Debt defaults, restructurings, or covenant breaches\n"
            f"- Heavy insider selling disclosed by directors/executives\n"
            f"- Delayed financial results or missed reporting deadlines\n\n"
            f"Rules:\n"
            f"- Items marked TITLE ONLY have no readable document text. "
            f"Judge only what the title itself states; never speculate "
            f"about what the underlying document might contain.\n"
            f"- Routine disclosures are NOT red flags: dividend credits, "
            f"share buy-back progress reports, board meeting notices, "
            f"corporate briefing sessions, employee share allotments, "
            f"and standard 'Disclosure of Interest' filings are normal "
            f"PSX housekeeping unless the text itself reveals something "
            f"unusual.\n"
            f"- If nothing is genuinely concerning, say RED_FLAGS: NONE.\n\n"
            f"Severity guide: LOW = worth monitoring but minor; "
            f"MEDIUM = material concern an investor should investigate; "
            f"HIGH = serious threat to the investment case.\n\n"
            f"Respond in this exact format:\n"
            f"RED_FLAGS: <comma-separated short flag names, or NONE if "
            f"no concerns>\n"
            f"SEVERITY: <LOW/MEDIUM/HIGH>\n"
            f"ANALYSIS: <2-4 sentence summary of your findings, citing "
            f"the specific disclosures involved>"
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
