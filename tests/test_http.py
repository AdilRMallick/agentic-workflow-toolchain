"""The HTTP layer: URL safety and retry behaviour, without touching the network."""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from awt.errors import SourceError
from awt.http import Response, UrllibFetcher, check_url


class TestCheckUrl:
    @pytest.mark.parametrize("url", ["https://example.com/feed", "http://example.com/feed"])
    def test_accepts_http_and_https(self, url: str) -> None:
        assert check_url(url) == url

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://example.com", "data:text/plain,x"]
    )
    def test_rejects_other_schemes(self, url: str) -> None:
        with pytest.raises(SourceError) as excinfo:
            check_url(url)
        assert "http" in excinfo.value.hint

    def test_rejects_a_relative_url(self) -> None:
        with pytest.raises(SourceError):
            check_url("https:///no-host")


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def patch_urlopen(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> list[str]:
    """Replace urlopen with a scripted sequence of results or exceptions."""
    seen: list[str] = []
    remaining = list(outcomes)

    class FakeRaw:
        status = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self, _limit: int | None = None) -> bytes:
            return self._body

        def __enter__(self) -> FakeRaw:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float | None = None) -> FakeRaw:
        seen.append(request.full_url)
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeRaw(outcome)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


class TestUrllibFetcher:
    def test_returns_the_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [b"hello"])
        response = UrllibFetcher()("https://example.com/feed")
        assert isinstance(response, Response)
        assert response.text == "hello"

    def test_sends_a_user_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, str] = {}

        def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
            captured.update(request.headers)
            raise urllib.error.URLError("stop here")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(SourceError):
            UrllibFetcher(retries=1)("https://example.com/feed")
        assert any("agentic-workflow-toolchain" in value for value in captured.values())

    def test_retries_transient_failures_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_urlopen(monkeypatch, [urllib.error.URLError("reset"), b"ok"])
        sleep = FakeSleep()
        assert UrllibFetcher(sleep=sleep)("https://example.com/f").text == "ok"
        assert sleep.delays == [1.0]

    def test_backs_off_exponentially(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch_urlopen(monkeypatch, [urllib.error.URLError("x")] * 3)
        sleep = FakeSleep()
        with pytest.raises(SourceError):
            UrllibFetcher(retries=3, sleep=sleep)("https://example.com/f")
        assert sleep.delays == [1.0, 2.0]

    def test_retries_a_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rate_limited = urllib.error.HTTPError("https://e/f", 429, "Too Many", {}, None)  # type: ignore[arg-type]
        patch_urlopen(monkeypatch, [rate_limited, b"ok"])
        assert UrllibFetcher(sleep=FakeSleep())("https://example.com/f").text == "ok"

    def test_does_not_retry_a_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        missing = urllib.error.HTTPError("https://e/f", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        seen = patch_urlopen(monkeypatch, [missing, b"never reached"])

        with pytest.raises(SourceError) as excinfo:
            UrllibFetcher(sleep=FakeSleep())("https://example.com/f")
        assert len(seen) == 1
        assert "Verify the repository" in excinfo.value.hint

    def test_auth_failures_suggest_a_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forbidden = urllib.error.HTTPError("https://e/f", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
        patch_urlopen(monkeypatch, [forbidden])
        with pytest.raises(SourceError) as excinfo:
            UrllibFetcher(sleep=FakeSleep())("https://example.com/f")
        assert "GITHUB_TOKEN" in excinfo.value.hint

    def test_exhausted_retries_report_the_attempt_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_urlopen(monkeypatch, [TimeoutError("slow")] * 2)
        with pytest.raises(SourceError) as excinfo:
            UrllibFetcher(retries=2, sleep=FakeSleep())("https://example.com/f")
        assert "after 2 attempts" in excinfo.value.message

    def test_a_bad_scheme_never_reaches_the_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = patch_urlopen(monkeypatch, [b"unreachable"])
        with pytest.raises(SourceError):
            UrllibFetcher()("file:///etc/passwd")
        assert seen == []
