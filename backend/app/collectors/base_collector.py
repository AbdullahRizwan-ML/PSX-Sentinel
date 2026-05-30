"""
PSX Sentinel — Base Collector

All collectors inherit from this class.

Contract:
- Implement collect() with the actual data fetching logic
- Use run_safe() in production — handles PipelineRun audit logging
- Never crash on a single ticker failure — log and continue
- Respect rate limits — use self.sleep(seconds) between requests
"""

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PipelineRun


class BaseCollector(ABC):
    """
    Abstract base for all data collectors.

    Subclasses must:
    1. Set a unique ``name`` class attribute
    2. Implement ``collect(tickers)`` returning a summary dict

    The ``run_safe()`` wrapper creates a PipelineRun audit record,
    catches all exceptions, and always commits the run status to the
    database so the operations dashboard has full visibility.
    """

    name: str = "base_collector"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @abstractmethod
    async def collect(self, tickers: list[str]) -> dict:
        """
        Core collection logic. Implement in each subclass.

        Must return a summary dict:
        {
            "tickers_processed": int,
            "tickers_failed": int,
            "records_inserted": int,
        }
        """
        pass

    async def run_safe(self, tickers: list[str]) -> dict:
        """
        Production wrapper. Creates PipelineRun audit record.

        Flow:
        1. Insert a RUNNING PipelineRun row
        2. Call self.collect(tickers)
        3. On success → mark SUCCESS with counts
        4. On failure → mark FAILED with error_log
        5. Always set completed_at and commit

        Returns the summary dict from collect(), or a zeroed dict on failure.
        """
        run = PipelineRun(
            pipeline_name=self.name,
            status="RUNNING",
        )
        self.db.add(run)
        await self.db.flush()

        start = time.monotonic()
        result = {
            "tickers_processed": 0,
            "tickers_failed": 0,
            "records_inserted": 0,
        }

        try:
            result = await self.collect(tickers)
            run.status = "SUCCESS" if result.get("tickers_failed", 0) == 0 else "PARTIAL"
            run.tickers_processed = result.get("tickers_processed", 0)
            run.tickers_failed = result.get("tickers_failed", 0)
        except Exception as e:
            run.status = "FAILED"
            run.error_log = f"{type(e).__name__}: {e}"
            logger.error(f"{self.name} failed: {e}")
        finally:
            run.completed_at = datetime.now(timezone.utc)
            await self.db.commit()

        elapsed = time.monotonic() - start
        logger.info(
            f"{self.name} complete | "
            f"{result['tickers_processed']} processed | "
            f"{result['tickers_failed']} failed | "
            f"{result.get('records_inserted', 0)} inserted | "
            f"{elapsed:.1f}s"
        )
        return result

    async def sleep(self, seconds: float) -> None:
        """Rate limit sleep. Use between API calls."""
        await asyncio.sleep(seconds)
