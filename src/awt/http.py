"""A small HTTP layer built on the standard library.

Source adapters never call the network directly. They accept a ``Fetcher`` --
``(url, headers) -> Response`` -- which keeps every adapter fully testable
offline and keeps the runtime dependency-free.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from awt.errors import SourceError

USER_AGENT = (
    "agentic-workflow-toolchain/0.1 (+https://github.com/AdilRMallick/agentic-workflow-toolchain)"
)
DEFAULT_TIMEOUT = 20.0
MAX_BYTES = 8 * 1024 * 1024
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class Response:
    url: str
    status: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Fetcher(Protocol):
    """Anything that can turn a URL into a :class:`Response`."""

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Response: ...


def check_url(url: str) -> str:
    """Reject anything that is not a plain http(s) URL.

    Tracker configs can come from a model or a config file, so ``file://`` and
    friends are refused before they reach ``urlopen``.
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise SourceError(
            f"Unsupported URL scheme {parts.scheme!r} in {url!r}.",
            hint="Only http and https URLs can be fetched.",
        )
    if not parts.netloc:
        raise SourceError(
            f"URL {url!r} has no host.",
            hint="Provide an absolute URL, e.g. https://example.com/feed.",
        )
    return url


class UrllibFetcher:
    """Default fetcher: retries idempotent GETs with exponential backoff."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 3,
        backoff: float = 1.0,
        sleep: object = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Response:
        check_url(url)
        merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        merged.update(headers or {})
        last: Exception | None = None

        for attempt in range(self.retries):
            try:
                request = urllib.request.Request(url, headers=merged, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as raw:  # noqa: S310
                    return Response(url=url, status=raw.status, body=raw.read(MAX_BYTES))
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    raise SourceError(
                        f"{url} returned HTTP {exc.code}.",
                        hint=_status_hint(exc.code),
                    ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc

            if attempt < self.retries - 1:
                self._sleep(self.backoff * (2**attempt))  # type: ignore[operator]

        raise SourceError(
            f"Could not fetch {url} after {self.retries} attempts: {last}",
            hint="Check network access and whether the source is rate limiting; retry later.",
        )


def _status_hint(code: int) -> str:
    if code in (401, 403):
        return "The source rejected the request; set GITHUB_TOKEN or check the feed is public."
    if code == 404:
        return "Verify the repository, feed URL, or query in the tracker config."
    return "Inspect the URL in a browser to confirm it is reachable."
