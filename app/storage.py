from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

LOGGER = logging.getLogger(__name__)
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class SupabaseStorageUploader:
    """Upload archives to a private Supabase Storage bucket when explicitly enabled.

    The standard Storage endpoint accepts large files, but an interrupted upload restarts
    from byte zero. The retry loop therefore rewinds the file on every attempt. Archives
    are timestamp-named, so normal operation never overwrites a previous research export.
    """

    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        self.bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "cross-asset-research-exports")
        if not self.url or not self.service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for storage upload")
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
        })

    def _request(self, method: str, url: str, *, allowed: set[int] | None = None, **kwargs: Any) -> requests.Response:
        allowed = allowed or {200, 201}
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                response = self.session.request(method, url, timeout=kwargs.pop("timeout", 45), **kwargs)
                if response.status_code in allowed:
                    return response
                if response.status_code not in _RETRYABLE:
                    response.raise_for_status()
                last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
            if attempt < 5:
                delay = min(60.0, (2 ** attempt) + random.random())
                LOGGER.warning("Supabase Storage request retry in %.1fs", delay)
                time.sleep(delay)
        raise RuntimeError(f"Supabase Storage request failed after retries: {last_error}")

    def ensure_private_bucket(self) -> None:
        bucket_url = f"{self.url}/storage/v1/bucket/{quote(self.bucket)}"
        response = self._request("GET", bucket_url, allowed={200, 404})
        if response.status_code == 200:
            payload = response.json()
            if payload.get("public"):
                raise RuntimeError(f"Refusing upload: storage bucket {self.bucket!r} is public")
            return
        self._request(
            "POST",
            f"{self.url}/storage/v1/bucket",
            allowed={200, 201, 409},
            json={"id": self.bucket, "name": self.bucket, "public": False, "file_size_limit": None},
        )
        # A concurrent creator may have produced the bucket. Re-read and enforce privacy.
        verify = self._request("GET", bucket_url, allowed={200})
        if verify.json().get("public"):
            raise RuntimeError(f"Refusing upload: storage bucket {self.bucket!r} is public")

    def upload(self, path: Path, prefix: str = "archives") -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        self.ensure_private_bucket()
        object_name = f"{prefix.strip('/')}/{path.name}"
        encoded = "/".join(quote(part) for part in object_name.split("/"))
        size = path.stat().st_size
        if size > 6 * 1024 * 1024:
            LOGGER.warning(
                "Uploading %s bytes through the standard Storage endpoint. Supabase recommends resumable uploads above 6 MB; a failed attempt will restart from byte zero.",
                size,
            )
        url = f"{self.url}/storage/v1/object/{quote(self.bucket)}/{encoded}"
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                with path.open("rb") as handle:
                    response = self.session.post(
                        url,
                        headers={"x-upsert": "false", "Content-Type": "application/zip"},
                        data=handle,
                        timeout=600,
                    )
                if response.status_code in {200, 201}:
                    return {"bucket": self.bucket, "object": object_name, "bytes": size}
                if response.status_code not in _RETRYABLE:
                    response.raise_for_status()
                last_error = RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
            if attempt < 5:
                delay = min(60.0, (2 ** attempt) + random.random())
                LOGGER.warning("Archive upload retry in %.1fs", delay)
                time.sleep(delay)
        raise RuntimeError(f"Archive upload failed after retries: {last_error}")
