"""The Steam exception hierarchy.

It lives in its own module because both layers need it and neither can own it:
`rate_limit` raises CircuitOpenError but must not import the HTTP client, and `client`
raises SteamUnavailable but imports rate_limit. Defining the hierarchy separately is
what lets CircuitOpenError *be* a SteamUnavailable instead of an unrelated RuntimeError.

That distinction is not cosmetic. When these were separate trees, every
`except SteamError` in the codebase silently missed a tripped circuit breaker -- so a
job logged a traceback instead of backing off, a search failed instead of falling back
to local results, and `--check` reported a hard failure instead of "throttled".
"""
from __future__ import annotations


class SteamError(RuntimeError):
    """Anything that stopped us getting an answer from Steam."""


class SteamNotFound(SteamError):
    """The item does not exist, or currently has no market listings.

    Data, not a fault: callers should record it and move on.
    """


class SteamUnavailable(SteamError):
    """Steam is throttling or down. Retry later; serve cached data meanwhile."""


class CircuitOpenError(SteamUnavailable):
    """The breaker for this endpoint is open, so the call failed fast.

    A subclass of SteamUnavailable because that is exactly what it means to a caller:
    no answer from Steam right now, try later. Carries the seconds until the next probe.
    """

    def __init__(self, endpoint: str, retry_after: float) -> None:
        super().__init__(f"circuit open for {endpoint}, retry in {retry_after:.0f}s")
        self.endpoint = endpoint
        self.retry_after = retry_after


class RateLimitError(SteamUnavailable):
    """Our own limiter refused: no token became available within the timeout.

    Also a SteamUnavailable -- the request did not happen and the caller's options are
    the same, regardless of which side declined to make it.
    """
