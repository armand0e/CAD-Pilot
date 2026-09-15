from __future__ import annotations

import urllib.error

import pytest

from cad1000.hf import with_retries


def test_retries_transient_then_succeeds() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return "ok"

    assert with_retries(flaky, attempts=5, backoff_s=1.0, sleep=sleeps.append) == "ok"
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_non_transient_errors_are_not_retried() -> None:
    calls: list[int] = []

    def forbidden() -> None:
        calls.append(1)
        raise urllib.error.HTTPError("u", 404, "not found", {}, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError):
        with_retries(forbidden, attempts=5, sleep=lambda _s: None)
    assert len(calls) == 1


def test_gives_up_after_attempts() -> None:
    def always() -> None:
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        with_retries(always, attempts=3, sleep=lambda _s: None)
