"""Jira Cloud REST API v3 client: Basic auth, rate limit handling."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

from .utils import log as _default_log


class JiraClient:
    """Reusable Jira Cloud client. Accepts an optional log callback."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        log: Callable[[str], None] = _default_log,
    ):
        self._root = base_url.rstrip("/")
        self._base = self._root + "/rest/api/3"
        self._log = log
        self._session = requests.Session()
        self._session.auth = (email, token)
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def get(self, endpoint: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base}{endpoint}"
        resp = self._request("GET", url, params=params)
        return resp.json()

    def paginate_jql(self, jql: str, fields: Optional[list[str]] = None) -> list:
        """Fetch all Jira issues matching JQL. Returns flat list of issue dicts."""
        results: list = []
        next_page_token: Optional[str] = None
        url = f"{self._base}/search/jql"

        while True:
            body: dict = {
                "jql": jql,
                "maxResults": 50,
                "fields": fields or ["*navigable"],
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token

            resp = self._request("POST", url, json=body)
            data = resp.json()

            issues = data.get("issues", [])
            results.extend(issues)

            next_page_token = data.get("nextPageToken")
            if not issues or not next_page_token:
                break

        return results

    def get_remote_links(self, issue_key: str) -> list:
        """Fetch remote links (GitHub integration, etc.) for an issue."""
        try:
            resp = self._request("GET", f"{self._base}/issue/{issue_key}/remotelink")
            return resp.json()
        except Exception:
            return []

    def get_dev_prs(self, issue_id: str) -> list[str]:
        """Fetch GitHub PRs linked via the development panel (GitHub for Jira automation)."""
        if not issue_id:
            return []
        try:
            url = f"{self._root}/rest/dev-status/1.0/issue/detail"
            params = {"issueId": issue_id, "applicationType": "GitHub", "dataType": "pullrequest"}
            resp = self._request("GET", url, params=params)
            data = resp.json()
            urls: list[str] = []
            for detail in data.get("detail", []):
                for pr in detail.get("pullRequests", []):
                    pr_url = pr.get("url", "")
                    if pr_url:
                        urls.append(pr_url)
            return urls
        except Exception:
            return []

    def get_changelog(self, issue_key: str) -> dict:
        """Fetch full changelog for an issue. Returns {"histories": [...]}."""
        all_histories: list = []
        start_at = 0

        while True:
            params = {"startAt": start_at, "maxResults": 100}
            resp = self._request("GET", f"{self._base}/issue/{issue_key}/changelog", params=params)
            page = resp.json()

            values = page.get("values", [])
            all_histories.extend(values)

            if page.get("isLast", True) or not values:
                break
            start_at += len(values)

        return {"histories": all_histories}

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        for attempt in range(3):
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10)) + 1
                self._log(f"  Jira rate limit: waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp


def create_jira_client(base_url: str, email: str, token: str) -> JiraClient:
    """Create a JiraClient wired to the project's log function."""
    return JiraClient(base_url, email, token, log=_default_log)
