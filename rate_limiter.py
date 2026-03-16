"""
rate_limiter.py

Sliding-window rate limiter for OpenAI API calls.

Tracks both RPM (requests per minute) and TPM (tokens per minute) over a
rolling 60-second window. Before every litellm.completion call it checks
whether adding the next request would exceed either limit. If so it sleeps
only as long as needed for the oldest entry to fall out of the window, then
retries — it never sleeps a full minute unnecessarily.

Usage:
    # Call ONCE in main.py before crew.kickoff():
    from rate_limiter import setup_limiter
    setup_limiter(rpm_limit=config.llm_rpm, tpm_limit=config.llm_tpm)

Design:
    - Works by monkey-patching litellm.completion, so every CrewAI →
      LiteLLM → OpenAI call is automatically throttled with no changes
      needed in agents, tasks, or tools.
    - Thread-safe: uses a threading.Lock so concurrent agent calls (if any)
      do not race on the shared window state.
    - Token counting: uses litellm.token_counter() on the prompt before the
      call to estimate prompt_tokens. Adds max_tokens (the reserved output
      budget) to get the total that OpenAI counts against TPM.
    - 0 for either limit means "unlimited" — the check is skipped for that
      dimension.
"""

import time
import threading
from collections import deque
from typing import Deque, Tuple

import litellm


class TokenRateLimiter:
    """
    Sliding-window rate limiter for RPM + TPM.

    Internals:
        _call_times  — deque of monotonic timestamps, one per completed call
        _token_usage — deque of (timestamp, tokens) pairs

    Both deques are purged of entries older than WINDOW seconds on every
    check, so memory is bounded to at most rpm_limit entries.
    """

    WINDOW: int = 62  # seconds in the sliding window

    def __init__(self, rpm_limit: int, tpm_limit: int) -> None:
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self._lock = threading.Lock()
        self._call_times: Deque[float] = deque()
        self._token_usage: Deque[Tuple[float, int]] = deque()

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _purge(self, now: float) -> None:
        """Drop entries older than WINDOW seconds."""
        cutoff = now - self.WINDOW
        while self._call_times and self._call_times[0] < cutoff:
            self._call_times.popleft()
        while self._token_usage and self._token_usage[0][0] < cutoff:
            self._token_usage.popleft()

    def _tokens_in_window(self) -> int:
        return sum(t for _, t in self._token_usage)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def wait_if_needed(self, prompt_tokens: int, max_tokens: int) -> None:
        """
        Block the calling thread until making an API call is safe.

        Args:
            prompt_tokens: Estimated tokens in the prompt (input side).
            max_tokens:    Max output tokens reserved for this request.
                           OpenAI counts (prompt_tokens + max_tokens) against TPM.
        """
        request_tokens = prompt_tokens + max_tokens

        with self._lock:
            while True:
                now = time.monotonic()
                self._purge(now)

                calls_in_window = len(self._call_times)
                tokens_in_window = self._tokens_in_window()

                rpm_ok = self.rpm_limit == 0 or calls_in_window < self.rpm_limit
                tpm_ok = (
                    self.tpm_limit == 0
                    or (tokens_in_window + request_tokens) <= self.tpm_limit
                )

                if rpm_ok and tpm_ok:
                    # Green light — record this call and return immediately
                    self._call_times.append(now)
                    self._token_usage.append((now, request_tokens))
                    return

                # Compute minimum wait so the bottleneck entry exits the window
                waits = []
                if not rpm_ok and self._call_times:
                    waits.append(
                        self.WINDOW - (now - self._call_times[0]) + 0.1)
                if not tpm_ok and self._token_usage:
                    waits.append(
                        self.WINDOW - (now - self._token_usage[0][0]) + 0.1)

                sleep_sec = max(0.1, min(max(waits or [1.0]), self.WINDOW))

                reasons = []
                if not rpm_ok:
                    reasons.append(f"RPM {calls_in_window}/{self.rpm_limit}")
                if not tpm_ok:
                    reasons.append(
                        f"TPM {tokens_in_window + request_tokens:,}/{self.tpm_limit:,}"
                    )
                print(
                    f"[RateLimiter] Pausing {sleep_sec:.1f}s "
                    f"({' | '.join(reasons)}) — will retry automatically"
                )
                time.sleep(sleep_sec)

    def status(self) -> dict:
        """Return current window usage. Useful for logging and tests."""
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            return {
                "calls_in_window": len(self._call_times),
                "tokens_in_window": self._tokens_in_window(),
                "rpm_limit": self.rpm_limit,
                "tpm_limit": self.tpm_limit,
            }


# ──────────────────────────────────────────────
# Module-level singleton + litellm integration
# ──────────────────────────────────────────────

_limiter: TokenRateLimiter | None = None


def setup_limiter(rpm_limit: int, tpm_limit: int) -> TokenRateLimiter:
    """
    Create the global rate limiter and patch litellm.completion so every
    CrewAI → LiteLLM → OpenAI call is automatically throttled.

    Call this ONCE in main.py, before crew.kickoff().

    Args:
        rpm_limit: Max requests per minute (0 = unlimited).
        tpm_limit: Max tokens per minute (0 = unlimited).
    """
    global _limiter
    _limiter = TokenRateLimiter(rpm_limit=rpm_limit, tpm_limit=tpm_limit)
    _patch_litellm(_limiter)
    print(
        f"[RateLimiter] Active — RPM limit: {rpm_limit}, "
        f"TPM limit: {tpm_limit:,}"
    )
    return _limiter


def _patch_litellm(limiter: TokenRateLimiter) -> None:
    """
    Wrap litellm.completion to inject throttle checks before every API call.

    litellm.completion is the single synchronous call site used by CrewAI
    in Process.sequential mode. Patching it here means zero changes are
    needed in agents.py, tasks.py, or any tool.
    """
    _original_completion = litellm.completion

    def _guarded_completion(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "gpt-4o")
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])
        max_tokens = kwargs.get("max_tokens") or 4096

        # Count prompt tokens; fall back conservatively on any error
        try:
            prompt_tokens = litellm.token_counter(
                model=model, messages=messages)
        except Exception:
            prompt_tokens = 2048

        limiter.wait_if_needed(
            prompt_tokens=prompt_tokens, max_tokens=max_tokens)
        return _original_completion(*args, **kwargs)

    litellm.completion = _guarded_completion
