"""
PSX Sentinel — PDF Parser

Downloads and parses PSX announcement PDFs. Uses pdfplumber for text
extraction and regex patterns for fiscal quarter/year identification.

Processes ALL announcement categories (broadened in Phase 5 Session 7 —
originally QUARTERLY_RESULT only) that have:
- A valid pdf_url
- pdf_parsed = False (not yet attempted)

Why all categories: the risk-relevant substance FilingSceptic needs
(CEO/board changes, resignations, buy-backs, EOGM resolutions,
clarifications of media reports) lives mostly under MATERIAL_INFO /
OTHER, and the mirror's category mapping is keyword-based and imperfect
anyway (e.g. "Board Meeting for Agenda Other than Financial Results"
maps to QUARTERLY_RESULT via the "financial results" keyword).

Reality check from the live corpus (2026-07-18, 83 PDFs swept): ~61%
have an extractable text layer; the other ~39% are image-only scans
(mostly "Disclosure of Interest" notices). Image-only PDFs yield empty
text from pdfplumber — those rows keep raw_text NULL and downstream
consumers (FilingSceptic) fall back to title-only analysis. No OCR is
attempted, per the no-invented-data rule.

Once processed, pdf_parsed is set to True regardless of success
to prevent infinite retry loops on corrupt/unparseable/image-only PDFs.
"""

import io
import os
import re
from datetime import datetime

import httpx
import pdfplumber
from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base_collector import BaseCollector
from app.db.models import Announcement


class PDFParser(BaseCollector):
    """
    Downloads quarterly result PDFs and extracts text content.

    Extracted text is stored in Announcement.raw_text for downstream
    agent analysis. Fiscal quarter and year are parsed from the text
    using regex patterns common in Pakistani financial reports.
    """

    name = "pdf_parser"

    PDF_DIR = "data/announcements"
    # The PSX Terminal mirror caps at ~10 announcements/ticker, so the
    # pending backlog is bounded at ~110 rows for the 11-company table.
    # 100/run drains a full backlog in one nightly pass while still
    # capping a pathological queue. (Was 20 when only QUARTERLY_RESULT
    # rows were eligible.)
    MAX_PDFS_PER_RUN = 100
    # Commit progress every N announcements. A full backlog takes
    # minutes of downloads (2s politeness sleep per PDF); a single
    # end-of-run commit leaves the Neon connection idle that whole time
    # and its proxy kills it (seen live 2026-07-18: InterfaceError
    # "connection is closed" at the final commit — the entire run's
    # raw_text was rolled back). Batched commits keep the connection
    # warm and make progress durable, so a mid-run failure costs at
    # most the current batch.
    COMMIT_EVERY = 10
    MAX_PAGES = 10  # Only parse first 10 pages per PDF
    MAX_TEXT_LENGTH = 50_000  # Cap stored text at 50K chars

    # Month-to-quarter mapping for Pakistani fiscal calendars
    # Pakistan fiscal year: July-June (most companies)
    # Some companies use Jan-Dec calendar year
    QUARTER_MAP = {
        "first": 1, "1st": 1,
        "second": 2, "2nd": 2,
        "third": 3, "3rd": 3,
        "fourth": 4, "4th": 4, "final": 4,
        "march": 3, "june": 2,
        "september": 3, "december": 4,
    }

    async def collect(self, tickers: list[str]) -> dict:
        """
        Find unparsed announcements with a pdf_url and extract text.

        Steps:
        1. Query DB for unparsed announcements (any category)
        2. Download each PDF via httpx
        3. Extract text with pdfplumber
        4. Store text in announcement.raw_text
        5. Parse fiscal quarter and year from text
        6. Mark announcement.pdf_parsed = True

        Returns summary dict.
        """
        os.makedirs(self.PDF_DIR, exist_ok=True)

        # Find unparsed announcement PDFs (all categories — see module
        # docstring for why this is not QUARTERLY_RESULT-only anymore)
        result = await self.db.execute(
            select(Announcement)
            .where(
                and_(
                    Announcement.pdf_parsed.is_(False),
                    Announcement.pdf_url.isnot(None),
                    Announcement.ticker.in_(tickers),
                )
            )
            .limit(self.MAX_PDFS_PER_RUN)
        )
        announcements = result.scalars().all()

        if not announcements:
            logger.info("No unparsed announcement PDFs found")
            return {
                "tickers_processed": 0,
                "tickers_failed": 0,
                "records_inserted": 0,
            }

        logger.info(
            f"Found {len(announcements)} unparsed quarterly result PDFs"
        )

        processed = 0
        failed = 0

        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            for i, ann in enumerate(announcements, 1):
                try:
                    success = await self._parse_announcement(client, ann)
                    if success:
                        processed += 1
                    else:
                        failed += 1
                    await self.sleep(2.0)
                except Exception as e:
                    logger.error(
                        f"PDF parse failed for {ann.ticker} "
                        f"announcement {ann.id}: "
                        f"{type(e).__name__}: {e}"
                    )
                    # Mark as attempted so we don't retry endlessly
                    ann.pdf_parsed = True
                    failed += 1

                if i % self.COMMIT_EVERY == 0:
                    await self._commit_batch()

        await self._commit_batch()

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": processed,
        }

    async def _commit_batch(self) -> None:
        """
        Commit accumulated updates, rolling back (and losing only this
        batch) if a row is unstorable. Without the rollback, one bad
        row leaves the session in PendingRollbackError and silently
        kills every subsequent batch commit in the run — seen live
        2026-07-18 before raw_text NUL-sanitization existed.
        """
        try:
            await self.db.commit()
        except Exception as e:
            logger.error(
                f"PDF batch commit failed — rolling back this batch "
                f"and continuing: {type(e).__name__}: {e}"
            )
            await self.db.rollback()

    async def _parse_announcement(
        self, client: httpx.AsyncClient, ann: Announcement
    ) -> bool:
        """
        Download and parse one PDF announcement.

        Returns True if text was successfully extracted,
        False otherwise. Always sets pdf_parsed = True.
        """
        # ── Download PDF ──────────────────────────────────────────────────
        try:
            resp = await client.get(ann.pdf_url)
            if resp.status_code != 200:
                logger.warning(
                    f"PDF download failed for {ann.ticker}: "
                    f"HTTP {resp.status_code} from {ann.pdf_url}"
                )
                ann.pdf_parsed = True
                return False
            pdf_bytes = resp.content
        except Exception as e:
            logger.warning(
                f"PDF download error for {ann.ticker}: "
                f"{type(e).__name__}: {e}"
            )
            ann.pdf_parsed = True
            return False

        # Verify we got a PDF (not an HTML error page)
        if not pdf_bytes[:5].startswith(b"%PDF"):
            logger.warning(
                f"Response is not a PDF for {ann.ticker} "
                f"(starts with {pdf_bytes[:20]!r})"
            )
            ann.pdf_parsed = True
            return False

        # ── Save to local filesystem ──────────────────────────────────────
        ticker_dir = os.path.join(self.PDF_DIR, ann.ticker)
        os.makedirs(ticker_dir, exist_ok=True)

        safe_title = re.sub(r"[^\w\s-]", "", ann.title[:50]).strip()
        filename = f"{ann.ticker}_{safe_title}_{ann.id}.pdf"
        filepath = os.path.join(ticker_dir, filename)

        try:
            with open(filepath, "wb") as f:
                f.write(pdf_bytes)
            ann.pdf_local_path = filepath
        except Exception as e:
            logger.warning(f"Could not save PDF to disk: {e}")
            # Continue anyway — we can still extract text from bytes

        # ── Extract text with pdfplumber ──────────────────────────────────
        try:
            text_parts: list[str] = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages[: self.MAX_PAGES]:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)

            full_text = "\n".join(text_parts)
            # PostgreSQL text columns reject NUL bytes, and pdfplumber
            # can emit \x00 from odd embedded fonts (seen live
            # 2026-07-18: MCB's quarterly accounts →
            # CharacterNotInRepertoireError on commit). Strip them.
            full_text = full_text.replace("\x00", "")

            if not full_text.strip():
                logger.warning(
                    f"PDF text extraction returned empty for "
                    f"{ann.ticker} announcement {ann.id}"
                )
                ann.pdf_parsed = True
                return False

            # Store extracted text (capped)
            ann.raw_text = full_text[: self.MAX_TEXT_LENGTH]
            ann.pdf_parsed = True

            # Try to extract fiscal quarter and year — but only for
            # quarterly-result documents. Running the period regexes on
            # notices/disclosures would happily latch onto any stray
            # year-like number and pollute fiscal_quarter/fiscal_year.
            quarter, year = None, None
            if ann.category == "QUARTERLY_RESULT":
                quarter, year = self._extract_period(full_text)
                if quarter is not None:
                    ann.fiscal_quarter = quarter
                if year is not None:
                    ann.fiscal_year = year

            logger.info(
                f"Parsed PDF for {ann.ticker}: "
                f"{len(full_text)} chars extracted, "
                f"Q{quarter or '?'} FY{year or '?'}"
            )
            return True

        except Exception as e:
            logger.error(
                f"pdfplumber error for {ann.ticker}: "
                f"{type(e).__name__}: {e}"
            )
            ann.pdf_parsed = True  # Don't retry corrupt PDFs
            return False

    def _extract_period(
        self, text: str
    ) -> tuple[int | None, int | None]:
        """
        Extract fiscal quarter and year from PDF text using regex.

        Handles common patterns in Pakistani financial reports:
        - "First Quarter ended September 30, 2024"
        - "Q1 FY2024"
        - "Half Yearly Report June 2024"
        - "Condensed Interim Financial Statements for the
           Quarter ended March 31, 2024"
        """
        quarter = None
        year = None

        text_lower = text.lower()

        # ── Quarter extraction ────────────────────────────────────────────
        quarter_patterns = [
            r"(?:first|1st)\s+quarter",
            r"(?:second|2nd)\s+quarter",
            r"(?:third|3rd)\s+quarter",
            r"(?:fourth|4th|final)\s+quarter",
            r"Q([1-4])\s*(?:FY|CY)?",
            r"quarter\s+ended?\s+(?:march|june|september|december)",
            r"half[\s-]+year",
        ]

        for pattern in quarter_patterns:
            match = re.search(pattern, text_lower)
            if match:
                matched_text = match.group(0).lower()
                for key, val in self.QUARTER_MAP.items():
                    if key in matched_text:
                        quarter = val
                        break
                # Handle "Q1", "Q2" etc.
                if quarter is None and match.lastindex:
                    try:
                        quarter = int(match.group(1))
                    except (IndexError, ValueError):
                        pass
                # Handle "half year" as Q2
                if quarter is None and "half" in matched_text:
                    quarter = 2
                if quarter is not None:
                    break

        # ── Year extraction ───────────────────────────────────────────────
        year_match = re.search(
            r"(?:FY|CY|year\s+ended?)\s*(\d{4})",
            text,
            re.IGNORECASE,
        )
        if year_match:
            year = int(year_match.group(1))
        else:
            # Fallback: find a 4-digit year in the 2020-2039 range
            year_match = re.search(r"\b(202[0-9]|203[0-9])\b", text)
            if year_match:
                year = int(year_match.group(1))

        return quarter, year
