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
_MAX_RATE_LIMIT_WAIT = 60  # cap a single rate-limit sleep so it fails fast


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

    def graphql_rate_limit(self) -> dict:
        """Return the GraphQL budget {limit, remaining, reset (epoch)}.

        Uses the REST /rate_limit endpoint, which is free: it does not consume
        the GraphQL budget the way a GraphQL `rateLimit` query would.
        """
        data = self.get("/rate_limit")
        gql = (data.get("resources") or {}).get("graphql") or {}
        return {
            "limit": gql.get("limit", 5000),
            "remaining": gql.get("remaining", 0),
            "reset": gql.get("reset", 0),
        }

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
        # GitHub reports GraphQL rate limits as HTTP 200 with an errors array
        # (type RATE_LIMITED), not as a 403, so _request never sees them. Retry
        # here, waiting for the rate-limit window to reset between attempts.
        for attempt in range(_MAX_RETRIES):
            resp = self._request(
                "POST", _GITHUB_GRAPHQL,
                json={"query": query, "variables": variables or {}},
            )
            payload = resp.json()
            errors = payload.get("errors")
            if errors:
                if self._is_rate_limited(errors) and attempt < _MAX_RETRIES - 1:
                    self._wait_for_graphql_reset(resp)
                    continue
                raise RuntimeError(f"GraphQL error: {errors}")
            return payload["data"]
        raise RuntimeError(f"Persistent GraphQL rate limit after {_MAX_RETRIES} attempts")

    @staticmethod
    def _is_rate_limited(errors: list) -> bool:
        return any(
            (e.get("type") == "RATE_LIMITED")
            or ("rate limit" in (e.get("message") or "").lower())
            for e in errors
        )

    def _wait_for_graphql_reset(self, resp: requests.Response) -> None:
        """Sleep until the GraphQL rate-limit window resets (capped)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            wait = int(retry_after) + 1
        else:
            reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(0.0, reset_at - time.time()) + 2
        wait = min(wait, _MAX_RATE_LIMIT_WAIT)
        log(f"  GraphQL rate limit: waiting {wait:.0f}s...")
        time.sleep(wait)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", _REQUEST_TIMEOUT)
        for attempt in range(_MAX_RETRIES):
            resp = self._session.request(method, url, **kwargs)
            # Primary (403 + "rate limit") and secondary (429, or 403 with a
            # Retry-After header) rate limits both warrant a wait and retry.
            is_rate_limited = (
                resp.status_code == 429
                or (resp.status_code == 403
                    and ("rate limit" in resp.text.lower() or resp.headers.get("Retry-After")))
            )
            if is_rate_limited:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = int(retry_after) + 1
                else:
                    reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
                    wait = max(0.0, reset_at - time.time()) + 2
                wait = min(wait, _MAX_RATE_LIMIT_WAIT)
                log(f"  Rate limit ({resp.status_code}): waiting {wait:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(wait)
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
