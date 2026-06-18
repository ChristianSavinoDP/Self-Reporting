"""GitHub API client: REST + GraphQL, auto rate-limit handling."""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from .utils import log

_GITHUB_API = "https://api.github.com"
_GITHUB_GRAPHQL = "https://api.github.com/graphql"
_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 3


class GitHubClient:
    def __init__(self, token: str):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def get(self, endpoint: str, params: Optional[dict] = None) -> Any:
        url = f"{_GITHUB_API}{endpoint}"
        resp = self._request("GET", url, params=params)
        return resp.json()

    def paginate(self, endpoint: str, params: Optional[dict] = None) -> list:
        """Fetch all pages and return a flat list."""
        results: list = []
        page = 1
        p = {**(params or {}), "per_page": 100}
        while True:
            batch = self.get(endpoint, {**p, "page": page})
            if isinstance(batch, dict) and "items" in batch:
                batch = batch["items"]
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        resp = self._request(
            "POST", _GITHUB_GRAPHQL,
            json={"query": query, "variables": variables or {}},
        )
        payload = resp.json()
        if "errors" in payload:
            raise RuntimeError(f"GraphQL error: {payload['errors']}")
        return payload["data"]

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", _REQUEST_TIMEOUT)
        for attempt in range(_MAX_RETRIES):
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                retry_after = int(resp.headers.get("Retry-After", 60))
                log(f"  Rate limit (403): waiting {retry_after}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(retry_after)
                continue
            self._check_rate(resp)
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"Persistent rate limit after {_MAX_RETRIES} attempts: {url}")

    def _check_rate(self, resp: requests.Response) -> None:
        remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
        if remaining < 5:
            reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(0, reset_at - time.time()) + 2
            log(f"  Rate limit ({remaining} remaining): waiting {wait:.0f}s...")
            time.sleep(wait)
