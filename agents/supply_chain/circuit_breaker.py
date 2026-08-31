"""Circuit breaker for supply chain guardrails (Phase D — resilience).

Wraps a guardrail validation call so that repeated failures (or repeated
rejections above a threshold) open the circuit and short-circuit subsequent
calls, preventing cascading failures against flaky downstream dependencies
(LLM provider, inventory DB, approval service).

States: CLOSED (normal) -> OPEN (tripped, fast-fail) -> HALF_OPEN (probe) ->
CLOSED again if the probe succeeds.

Thread-safe via asyncio.Lock so it can be shared across coroutines.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    # Seconds the circuit stays OPEN before moving to HALF_OPEN.
    recovery_timeout_seconds: float = 30.0
    # Successes in HALF_OPEN needed to close the circuit.
    half_open_success_threshold: int = 2


@dataclass
class _Bucket:
    failures: int = 0
    opened_at: float = 0.0
    half_open_successes: int = 0


class CircuitBreaker:
    """Async-safe circuit breaker."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self._cfg = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._bucket = _Bucket()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    async def allow(self) -> bool:
        """Return True if the call should be allowed through.

        Handles OPEN -> HALF_OPEN transition on recovery timeout.
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._bucket.opened_at >= self._cfg.recovery_timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._bucket.half_open_successes = 0
                    return True
                return False
            return True

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._bucket.half_open_successes += 1
                if self._bucket.half_open_successes >= self._cfg.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._bucket = _Bucket()
            else:
                self._bucket.failures = 0

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                # refresh opened_at so it stays open
                self._bucket.opened_at = time.monotonic()
                return
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._bucket.opened_at = time.monotonic()
                return
            # CLOSED
            self._bucket.failures += 1
            if self._bucket.failures >= self._cfg.failure_threshold:
                self._state = CircuitState.OPEN
                self._bucket.opened_at = time.monotonic()

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._bucket = _Bucket()


__all__ = ["CircuitBreaker", "CircuitState", "CircuitBreakerConfig"]
