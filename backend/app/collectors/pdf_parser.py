"""
PSX Sentinel — PDF Parser

Downloads and parses PSX quarterly result PDFs from corporate
announcements. Uses pdfplumber for text extraction and regex
patterns for fiscal quarter/year identification.

Only processes QUARTERLY_RESULT announcements that have:
- A valid pdf_url
- pdf_parsed = False (not yet attempted)

Once processed, pdf_parsed is set to True regardless of success
to prevent infinite retry loops on corrupt/unparseable PDFs.
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
    MAX_PDFS_PER_RUN = 20  # Prevent runaway processing
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
        Find unparsed QUARTERLY_RESULT announcements and extract text.

        Steps:
        1. Query DB for unparsed quarterly result announcements
        2. Download each PDF via httpx
        3. Extract text with pdfplumber
        4. Store text in announcement.raw_text
        5. Parse fiscal quarter and year from text
        6. Mark announcement.pdf_parsed = True

        Returns summary dict.
        """
        os.makedirs(self.PDF_DIR, exist_ok=True)

        # Find unparsed quarterly result PDFs
        result = await self.db.execute(
            select(Announcement)
            .where(
                and_(
                    Announcement.category == "QUARTERLY_RESULT",
                    Announcement.pdf_parsed.is_(False),
                    Announcement.pdf_url.isnot(None),
                    Announcement.ticker.in_(tickers),
                )
            )
            .limit(self.MAX_PDFS_PER_RUN)
        )
        announcements = result.scalars().all()

        if not announcements:
            logger.info("No unparsed quarterly result PDFs found")
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
            for ann in announcements:
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

        await self.db.commit()

        return {
            "tickers_processed": processed,
            "tickers_failed": failed,
            "records_inserted": processed,
        }

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

            # Try to extract fiscal quarter and year
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
