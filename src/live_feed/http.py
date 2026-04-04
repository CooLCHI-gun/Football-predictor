from __future__ import annotations

from dataclasses import dataclass, field
import json
from time import sleep
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
    "Referer": "https://football.hkjc.com/",
    "Connection": "keep-alive",
}


@dataclass(frozen=True)
class HttpClientConfig:
    timeout_seconds: int = 15
    max_retries: int = 2
    backoff_factor: float = 0.5
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)
    headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BROWSER_HEADERS))


class HttpClient:
    """Requests Session wrapper with retry/backoff and browser-like defaults."""

    def __init__(self, config: HttpClientConfig | None = None) -> None:
        self._config = config or HttpClientConfig()
        self._session = requests.Session()
        self._session.headers.update(self._config.headers)

        retry = Retry(
            total=self._config.max_retries,
            read=self._config.max_retries,
            connect=self._config.max_retries,
            backoff_factor=self._config.backoff_factor,
            status_forcelist=self._config.status_forcelist,
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    @property
    def session(self) -> requests.Session:
        return self._session

    def get_json(
        self,
        url: str,
        *,
        timeout_seconds: int | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> tuple[int, str, Any | None]:
        status_code, text = self.get_text(
            url,
            timeout_seconds=timeout_seconds,
            params=params,
            retries=retries,
            retry_delay_seconds=retry_delay_seconds,
        )
        if status_code <= 0:
            return status_code, text, None

        try:
            payload = json.loads(text)
        except ValueError:
            return status_code, text, None
        return status_code, text, payload

    def get_text(
        self,
        url: str,
        *,
        timeout_seconds: int | None = None,
        params: dict[str, Any] | None = None,
        retries: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> tuple[int, str]:
        timeout = self._config.timeout_seconds if timeout_seconds is None else timeout_seconds
        attempt = 0
        max_attempts = max(1, retries + 1)
        while attempt < max_attempts:
            attempt += 1
            try:
                response = self._session.get(url, params=params, timeout=timeout)
                return response.status_code, response.text
            except requests.RequestException as exc:
                if attempt >= max_attempts:
                    return -1, f"REQUEST_ERROR: {exc}"
                if retry_delay_seconds > 0:
                    sleep(retry_delay_seconds)

        return -1, "REQUEST_ERROR: exhausted retries"
