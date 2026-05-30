"""
PSX Sentinel — Agent Base Classes

Defines the contract that all agents in the 4-agent system must follow:

1. AgentContext: immutable data bag populated progressively as agents run.
   Agents 1-3 write their findings into trend_signals, news_sentiment,
   and filing_flags respectively. Agent 4 (Arbitrator) reads all three
   to synthesize the final intelligence report.

2. AgentResult: structured output that every agent returns, ensuring
   consistent logging, error tracking, and orchestrator consumption.

3. BaseAgent: abstract base class with run_safe() wrapper that guarantees
   a result is always returned — even on catastrophic failures.

Agent Pipeline Flow:
    Context → Agent 1 (Trend Analyst) → writes trend_signals
            → Agent 2 (News Analyst)  → writes news_sentiment
            → Agent 3 (Filing Analyst) → writes filing_flags
            → Agent 4 (Arbitrator)    → reads all, produces report
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import date

from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_gateway import CircuitBreakerOpen, LLMGateway


class AgentContext(BaseModel):
    """
    Data passed to every agent. Populated progressively through the pipeline.

    The orchestrator creates a single AgentContext and passes it to each agent
    in sequence. Earlier agents write their outputs into the shared dict fields,
    which later agents can read to build upon.

    arbitrary_types_allowed is required because date is not a standard
    JSON-serializable type in strict Pydantic mode.
    """

    model_config = {"arbitrary_types_allowed": True}

    ticker: str
    company_name: str
    analysis_id: str
    report_date: date

    # Populated by data loading before agents run
    recent_prices: list[dict] = []  # Last 90 days OHLCV
    news_articles: list[dict] = []  # Last 30 days
    announcements: list[dict] = []  # Last 4 quarters

    # Populated by agents 1-3 for Agent 4 (Arbitrator)
    trend_signals: dict = {}
    news_sentiment: dict = {}
    filing_flags: dict = {}


class AgentResult(BaseModel):
    """
    Structured output from every agent.

    The orchestrator collects AgentResults from all four agents and
    assembles them into the final IntelligenceReport. The confidence
    field (0.0-1.0) is self-reported by each agent based on data
    quality and completeness.
    """

    agent_name: str
    success: bool
    output: dict  # Agent-specific findings
    confidence: float  # 0.0 to 1.0, self-reported
    tokens_used: int
    latency_ms: int
    error_message: str | None = None

    def to_summary(self) -> str:
        """One-line summary for orchestrator logging."""
        status = "✓" if self.success else "✗"
        return (
            f"{status} {self.agent_name} | "
            f"confidence={self.confidence:.2f} | "
            f"{self.tokens_used} tokens | "
            f"{self.latency_ms}ms"
            + (
                f" | ERROR: {self.error_message}"
                if self.error_message
                else ""
            )
        )


class BaseAgent(ABC):
    """
    Abstract base class for all PSX Sentinel agents.

    Contract:
    - Subclasses implement run() with their domain-specific logic
    - Use run_safe() in production — it handles all error cases
    - All LLM calls go through self.llm (LLMGateway)
    - Never call Groq/Gemini SDKs directly
    - run() may raise exceptions — they are caught by run_safe()
    - run_safe() ALWAYS returns an AgentResult, even on catastrophic failure

    Subclass Example:
        class TrendAgent(BaseAgent):
            name = "trend_analyst"
            max_tokens = 2000

            async def run(self, context: AgentContext) -> AgentResult:
                response = await self.llm.complete(
                    messages=[...],
                    agent_name=self.name,
                    analysis_id=context.analysis_id,
                    max_tokens=self.max_tokens,
                )
                return AgentResult(
                    agent_name=self.name,
                    success=True,
                    output={"signals": ...},
                    confidence=0.85,
                    tokens_used=response.prompt_tokens + response.completion_tokens,
                    latency_ms=response.latency_ms,
                )
    """

    name: str = "base_agent"
    max_tokens: int = 1500
    timeout_seconds: int = 45

    def __init__(self, llm: LLMGateway, db: AsyncSession) -> None:
        self.llm = llm
        self.db = db

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Core agent logic. Implement this in each subclass.

        May raise exceptions — they are caught by run_safe().
        Should return an AgentResult with success=True on normal completion.
        """
        pass

    async def run_safe(self, context: AgentContext) -> AgentResult:
        """
        Production wrapper that guarantees a result is always returned.

        Handles:
        - CircuitBreakerOpen: returns graceful degradation result
        - asyncio.TimeoutError: logs timeout, returns failure result
        - Any other Exception: logs error, returns failure result

        The orchestrator should always call run_safe() instead of run()
        to ensure the pipeline never crashes due to a single agent failure.
        """
        try:
            result = await asyncio.wait_for(
                self.run(context),
                timeout=self.timeout_seconds + 10,
            )
            logger.info(result.to_summary())
            return result

        except CircuitBreakerOpen as e:
            logger.warning(
                f"{self.name} skipped — circuit breaker open: {e}"
            )
            return AgentResult(
                agent_name=self.name,
                success=False,
                output={},
                confidence=0.0,
                tokens_used=0,
                latency_ms=0,
                error_message=f"Circuit breaker open: {str(e)}",
            )

        except asyncio.TimeoutError:
            logger.error(
                f"{self.name} TIMEOUT after "
                f"{self.timeout_seconds + 10}s for {context.ticker}"
            )
            return AgentResult(
                agent_name=self.name,
                success=False,
                output={},
                confidence=0.0,
                tokens_used=0,
                latency_ms=(self.timeout_seconds + 10) * 1000,
                error_message="Agent execution timed out",
            )

        except Exception as e:
            logger.error(
                f"{self.name} FAILED for {context.ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return AgentResult(
                agent_name=self.name,
                success=False,
                output={},
                confidence=0.0,
                tokens_used=0,
                latency_ms=0,
                error_message=f"{type(e).__name__}: {str(e)[:300]}",
            )
