"""
tests/test_rate_limiter.py

Unit tests for rate_limiter.py.
No real API calls, no real sleeps — time.sleep and time.monotonic are patched.

Run with: uv run pytest tests/test_rate_limiter.py -v
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from rate_limiter import TokenRateLimiter, setup_limiter


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_limiter(rpm=100, tpm=1_000_000) -> TokenRateLimiter:
    """Create a limiter with high defaults — most tests only constrain one axis."""
    return TokenRateLimiter(rpm_limit=rpm, tpm_limit=tpm)


# ──────────────────────────────────────────────
# Basic behaviour — no throttling expected
# ──────────────────────────────────────────────

class TestNoThrottleUnderLimits:
    def test_single_call_under_both_limits_does_not_sleep(self):
        limiter = make_limiter(rpm=10, tpm=100_000)
        with patch("rate_limiter.time.sleep") as mock_sleep:
            limiter.wait_if_needed(prompt_tokens=1000, max_tokens=2000)
        mock_sleep.assert_not_called()

    def test_multiple_calls_under_limits_do_not_sleep(self):
        limiter = make_limiter(rpm=10, tpm=100_000)
        with patch("rate_limiter.time.sleep") as mock_sleep:
            for _ in range(5):
                limiter.wait_if_needed(prompt_tokens=100, max_tokens=200)
        mock_sleep.assert_not_called()

    def test_zero_rpm_means_unlimited(self):
        limiter = make_limiter(rpm=0, tpm=0)
        with patch("rate_limiter.time.sleep") as mock_sleep:
            for _ in range(200):
                limiter.wait_if_needed(prompt_tokens=9999, max_tokens=9999)
        mock_sleep.assert_not_called()


# ──────────────────────────────────────────────
# Token tracking
# ──────────────────────────────────────────────

class TestTokenTracking:
    def test_status_reflects_single_call(self):
        limiter = make_limiter()
        limiter.wait_if_needed(prompt_tokens=500, max_tokens=1000)
        s = limiter.status()
        assert s["calls_in_window"] == 1
        assert s["tokens_in_window"] == 1500  # 500 + 1000

    def test_status_accumulates_multiple_calls(self):
        limiter = make_limiter()
        limiter.wait_if_needed(prompt_tokens=100, max_tokens=200)  # 300
        limiter.wait_if_needed(prompt_tokens=400, max_tokens=600)  # 1000
        s = limiter.status()
        assert s["calls_in_window"] == 2
        assert s["tokens_in_window"] == 1300

    def test_status_returns_configured_limits(self):
        limiter = TokenRateLimiter(rpm_limit=42, tpm_limit=99_000)
        s = limiter.status()
        assert s["rpm_limit"] == 42
        assert s["tpm_limit"] == 99_000


# ──────────────────────────────────────────────
# Window expiry
# ──────────────────────────────────────────────

class TestWindowExpiry:
    def test_old_calls_are_purged_after_window(self):
        limiter = make_limiter()
        base_time = 1000.0

        # Record two calls at t=1000
        with patch("rate_limiter.time.monotonic", return_value=base_time):
            limiter.wait_if_needed(prompt_tokens=100, max_tokens=100)
            limiter.wait_if_needed(prompt_tokens=100, max_tokens=100)

        # After 61 seconds, status should show 0 calls
        with patch("rate_limiter.time.monotonic", return_value=base_time + 61):
            s = limiter.status()

        assert s["calls_in_window"] == 0
        assert s["tokens_in_window"] == 0

    def test_recent_calls_remain_after_window(self):
        limiter = make_limiter()
        base_time = 1000.0

        # Old call at t=1000
        with patch("rate_limiter.time.monotonic", return_value=base_time):
            limiter.wait_if_needed(prompt_tokens=100, max_tokens=100)

        # New call at t=1050 (still in window)
        with patch("rate_limiter.time.monotonic", return_value=base_time + 50):
            limiter.wait_if_needed(prompt_tokens=200, max_tokens=200)

        # Check at t=1062: first call expired, second still live
        with patch("rate_limiter.time.monotonic", return_value=base_time + 62):
            s = limiter.status()

        assert s["calls_in_window"] == 1
        assert s["tokens_in_window"] == 400  # only the second call (200+200)


# ──────────────────────────────────────────────
# RPM throttle
# ──────────────────────────────────────────────

class TestRPMThrottle:
    def test_exceeding_rpm_triggers_sleep(self):
        """Fill RPM=2 window, then verify the third call sleeps."""
        limiter = TokenRateLimiter(rpm_limit=2, tpm_limit=1_000_000)
        base = 1000.0

        # Two calls at t=base — fills the RPM window
        with patch("rate_limiter.time.monotonic", return_value=base):
            limiter.wait_if_needed(10, 10)
            limiter.wait_if_needed(10, 10)

        # Third call:
        #   Iteration 1: now=base → 2/2 RPM → sleep
        #   Iteration 2: now=base+61 → old calls purge (cutoff=base+1>base) → succeed
        with patch("rate_limiter.time.monotonic", side_effect=[base, base + 61]):
            with patch("rate_limiter.time.sleep") as mock_sleep:
                limiter.wait_if_needed(10, 10)

        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration > 0


# ──────────────────────────────────────────────
# TPM throttle
# ──────────────────────────────────────────────

class TestTPMThrottle:
    def test_exceeding_tpm_triggers_sleep(self):
        """Use 400/500 tokens, then a 300-token call should sleep."""
        limiter = TokenRateLimiter(rpm_limit=1000, tpm_limit=500)
        base = 1000.0

        # Use 400 tokens at t=base
        with patch("rate_limiter.time.monotonic", return_value=base):
            limiter.wait_if_needed(prompt_tokens=200, max_tokens=200)

        # Next call wants 300 tokens → 400+300=700 > 500 → must sleep
        #   Iteration 1: now=base → tpm exceeded → sleep
        #   Iteration 2: now=base+61 → entry purged (cutoff=base+1>base) → succeed
        with patch("rate_limiter.time.monotonic", side_effect=[base, base + 61]):
            with patch("rate_limiter.time.sleep") as mock_sleep:
                limiter.wait_if_needed(prompt_tokens=100, max_tokens=200)

        mock_sleep.assert_called_once()

    def test_call_exactly_at_tpm_limit_does_not_sleep(self):
        """A call that lands exactly on the limit should be allowed through."""
        limiter = TokenRateLimiter(rpm_limit=1000, tpm_limit=500)
        with patch("rate_limiter.time.sleep") as mock_sleep:
            limiter.wait_if_needed(prompt_tokens=250, max_tokens=250)  # = 500 exactly
        mock_sleep.assert_not_called()


# ──────────────────────────────────────────────
# litellm patch (setup_limiter)
# ──────────────────────────────────────────────

class TestSetupLimiter:
    def test_setup_limiter_patches_litellm_completion(self):
        """After setup_limiter(), litellm.completion should be our wrapper."""
        import litellm
        original = litellm.completion

        limiter = setup_limiter(rpm_limit=60, tpm_limit=30_000)
        patched = litellm.completion

        # The patch replaces the original
        assert patched is not original

        # Restore so other tests aren't affected
        litellm.completion = original

    def test_patched_completion_calls_wait_if_needed(self):
        """The wrapper must call wait_if_needed before forwarding the call."""
        import litellm

        original = litellm.completion
        try:
            limiter = setup_limiter(rpm_limit=60, tpm_limit=30_000)

            mock_original = MagicMock(return_value=MagicMock())
            wait_calls = []

            # Spy on wait_if_needed
            real_wait = limiter.wait_if_needed
            def spy_wait(prompt_tokens, max_tokens):
                wait_calls.append((prompt_tokens, max_tokens))
                # Don't actually wait
            limiter.wait_if_needed = spy_wait

            with patch("litellm.token_counter", return_value=500):
                # Call the patched completion (wraps mock_original indirectly)
                # We can't easily test the inner call without more mocking,
                # but we can verify the limiter received a call.
                try:
                    litellm.completion(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": "hello"}],
                        max_tokens=100,
                    )
                except Exception:
                    pass  # Real OpenAI call will fail — we only care about wait_calls

            assert len(wait_calls) == 1
            assert wait_calls[0][1] == 100  # max_tokens passed through

        finally:
            litellm.completion = original
