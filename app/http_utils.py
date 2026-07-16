from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class HttpError(RuntimeError):
    pass


def request(
    method: str,
    url: str,
    *,
    timeout: int = 45,
    max_retries: int = 5,
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504),
    session: requests.Session | None = None,
    **kwargs: Any,
) -> requests.Response:
    client = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, timeout=timeout, **kwargs)
            if response.status_code not in retry_statuses:
                response.raise_for_status()
                return response
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else min(60.0, (2 ** attempt) + random.random())
            last_error = HttpError(f"HTTP {response.status_code}: {response.text[:500]}")
        except requests.HTTPError as exc:
            # Authentication, permission and missing-resource errors are not transient. Retrying
            # them only delays the job and obscures the real cause.
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and status not in retry_statuses:
                raise HttpError(f"HTTP {status} from {url}: {exc.response.text[:500]}") from exc
            last_error = exc
            wait = min(60.0, (2 ** attempt) + random.random())
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            wait = min(60.0, (2 ** attempt) + random.random())
        if attempt < max_retries:
            LOGGER.warning("HTTP retry", extra={"event": "http_retry"})
            time.sleep(wait)
    raise HttpError(f"Request failed after {max_retries + 1} attempts: {url}: {last_error}")


def request_json(*args: Any, **kwargs: Any) -> Any:
    response = request(*args, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise HttpError(f"Expected JSON from {response.url}, received {response.text[:500]}") from exc
