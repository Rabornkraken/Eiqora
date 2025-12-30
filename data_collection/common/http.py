"""HTTP utilities with basic retry/backoff handling."""

from __future__ import annotations

import time
from typing import Any

import requests

from data_collection.common.config import HttpSettings


class HttpError(RuntimeError):
    """Raised when HTTP calls exhaust retries."""


def build_session(settings: HttpSettings) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": settings.user_agent})
    return session


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    settings: HttpSettings,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    data: Any | None = None,
    json: Any | None = None,
    stream: bool = False,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            response = session.request(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
                json=json,
                timeout=settings.timeout_seconds,
                stream=stream,
            )
            if response.status_code < 400:
                return response
            if response.status_code in {429, 500, 502, 503, 504}:
                retry_after = response.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else settings.backoff_seconds * attempt
                time.sleep(sleep_for)
                continue
            response.raise_for_status()
        except Exception as exc:
            last_error = exc
            time.sleep(settings.backoff_seconds * attempt)

    raise HttpError(f"Failed request after {settings.max_retries} attempts: {url}") from last_error
