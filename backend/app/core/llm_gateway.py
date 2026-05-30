"""
PSX Sentinel — LLM Gateway

THE SINGLE POINT OF CONTACT for all LLM interactions in the system.
No agent, no service, no route handler ever calls Groq or Gemini directly.
Every call goes through LLMGateway.complete() which provides:

1. Automatic failover: Groq (primary) → Gemini (fallback)
2. Circuit breaker: prevents cascading failures when LLMs are down
3. Audit logging: every call is recorded in the LLMCall table
4. Cost tracking: token counts and latency are measured per call
5. Timeout protection: no call blocks forever

Architecture:
    Agent → LLMGateway.complete() → Groq API (primary)
                                  ↘ Gemini API (fallback)
                                  → LLMCall audit table (always)
"""

import asyncio
import time
import uuid

import google.generativeai as genai
from groq import AsyncGroq
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import RedisClient
from app.db.models import LLMCall


class LLMResponse(BaseModel):
    """Structured response from any LLM call through the gateway."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost_usd: float = 0.0


class CircuitBreakerOpen(Exception):
    """
    Raised when an agent has exceeded the failure threshold.

    The circuit breaker auto-resets after 5 minutes (300s TTL in Redis).
    Agents receiving this exception should return a graceful degradation
    result via their run_safe() wrapper.
    """

    pass


class LLMGateway:
    """
    Central LLM orchestration gateway.

    Every agent receives an LLMGateway instance and calls self.llm.complete()
    for all LLM interactions. This ensures consistent failover, logging,
    and cost tracking across the entire system.

    Circuit breaker threshold is 3 consecutive failures per agent.
    After 3 failures, the circuit opens and all subsequent calls for
    that agent are rejected until the 5-minute TTL expires.
    """

    CIRCUIT_BREAKER_THRESHOLD: int = 3
    PRIMARY_MODEL: str = "llama-3.3-70b-versatile"
    FALLBACK_MODEL: str = "gemini-2.0-flash"

    def __init__(self, db: AsyncSession, redis: RedisClient) -> None:
        self.db = db
        self.redis = redis
        self.groq = AsyncGroq(api_key=get_settings().GROQ_API_KEY)
        if get_settings().GEMINI_API_KEY:
            genai.configure(api_key=get_settings().GEMINI_API_KEY)

    async def complete(
        self,
        messages: list[dict],
        agent_name: str,
        analysis_id: str | None = None,
        max_tokens: int = 1500,
        timeout_seconds: int = 45,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """
        Execute an LLM completion through the gateway.

        Flow:
        1. Check circuit breaker — raise CircuitBreakerOpen if tripped
        2. Try Groq (primary) with timeout
        3. On Groq failure — try Gemini (fallback)
        4. On both failures — increment circuit breaker, raise RuntimeError
        5. On success — reset circuit breaker
        6. ALWAYS log to LLMCall audit table regardless of outcome

        Args:
            messages: OpenAI-format message list [{role, content}, ...]
            agent_name: Identifier for the calling agent (used in logging/CB)
            analysis_id: UUID of the IntelligenceReport this call belongs to
            max_tokens: Maximum tokens in the completion response
            timeout_seconds: Timeout for each individual LLM call
            temperature: Sampling temperature (0.0 = deterministic)

        Returns:
            LLMResponse with content, token counts, and latency

        Raises:
            CircuitBreakerOpen: if the agent has too many recent failures
            RuntimeError: if both Groq and Gemini fail
        """
        # 1. Circuit breaker check
        failures = await self.redis.get_circuit_breaker_failures(agent_name)
        if failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            logger.warning(
                f"Circuit breaker OPEN for {agent_name} "
                f"({failures} failures)"
            )
            raise CircuitBreakerOpen(
                f"Agent {agent_name} circuit breaker is open. "
                f"Will reset in ~5 minutes."
            )

        start_time = time.monotonic()
        model_used = self.PRIMARY_MODEL
        content = ""
        prompt_tokens = 0
        completion_tokens = 0
        status = "SUCCESS"
        error_message = None

        # 2. Try Groq (primary)
        try:
            response = await asyncio.wait_for(
                self.groq.chat.completions.create(
                    model=self.PRIMARY_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=timeout_seconds,
            )
            content = response.choices[0].message.content
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            await self.redis.reset_circuit_breaker(agent_name)
            logger.debug(
                f"Groq success: {agent_name} | "
                f"{prompt_tokens}+{completion_tokens} tokens | "
                f"{int((time.monotonic() - start_time) * 1000)}ms"
            )

        except Exception as groq_error:
            logger.warning(
                f"Groq failed for {agent_name}: {groq_error}. "
                f"Trying Gemini fallback..."
            )

            # 3. Gemini fallback
            try:
                if not get_settings().GEMINI_API_KEY:
                    raise RuntimeError("No Gemini API key configured")

                model_used = self.FALLBACK_MODEL
                gemini_model = genai.GenerativeModel("gemini-2.0-flash")

                # Convert OpenAI-style messages to Gemini plain-text format
                prompt_text = "\n".join(
                    [
                        f"{m['role'].upper()}: {m['content']}"
                        for m in messages
                    ]
                )

                gemini_response = await asyncio.wait_for(
                    asyncio.to_thread(
                        gemini_model.generate_content, prompt_text
                    ),
                    timeout=timeout_seconds,
                )
                content = gemini_response.text
                prompt_tokens = 0  # Gemini free tier does not expose counts
                completion_tokens = 0
                await self.redis.reset_circuit_breaker(agent_name)
                logger.info(f"Gemini fallback success for {agent_name}")

            except Exception as gemini_error:
                # 4. Both failed — increment circuit breaker
                failure_count = (
                    await self.redis.increment_circuit_breaker(agent_name)
                )
                status = "FAILURE"
                error_message = (
                    f"Groq: {str(groq_error)[:200]} | "
                    f"Gemini: {str(gemini_error)[:200]}"
                )
                logger.error(
                    f"BOTH LLMs failed for {agent_name}. "
                    f"Circuit breaker count: {failure_count}. "
                    f"Error: {error_message}"
                )

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # 6. Always audit log — regardless of success or failure
        await self._log_call(
            analysis_id=analysis_id,
            agent_name=agent_name,
            model=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )

        if status == "FAILURE":
            raise RuntimeError(
                f"LLM gateway failed for agent '{agent_name}'. "
                f"Both Groq and Gemini unavailable."
            )

        return LLMResponse(
            content=content,
            model=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    async def _log_call(
        self,
        analysis_id: str | None,
        agent_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str,
        error_message: str | None,
    ) -> None:
        """
        Write an audit record to the LLMCall table in PostgreSQL.

        This method NEVER raises — failures are logged but do not propagate.
        Audit logging must never disrupt the primary LLM call flow.
        """
        try:
            call = LLMCall(
                analysis_id=(
                    uuid.UUID(analysis_id) if analysis_id else None
                ),
                agent_name=agent_name,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                cost_usd=0.0,
                status=status,
                error_message=error_message,
            )
            self.db.add(call)
            await self.db.flush()
        except Exception as e:
            logger.error(f"Failed to write LLM audit log: {e}")
