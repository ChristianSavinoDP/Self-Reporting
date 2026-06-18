"""Collect GitHub PR and review metrics for a single user."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# Require this multiple of the estimated request count in remaining GraphQL
# points before starting, since a single batched query can cost more than 1.
_BUDGET_SAFETY_FACTOR = 3

from .client import GitHubClient
from .utils import log


class InsufficientRateLimitError(RuntimeError):
    """Raised when the GraphQL budget is too low to finish the run."""


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------

_BOT_LOGINS = {
    "coderabbitai",
    "copilot-pull-request-reviewer",
    "github-actions",
    "github-actions[bot]",
    "dependabot",
    "dependabot[bot]",
    "renovate",
    "renovate[bot]",
    "snyk-bot",
    "sonarcloud[bot]",
    "codecov[bot]",
    "speakeasy-api[bot]",
}

def _is_bot(login: str) -> bool:
    return login.lower() in _BOT_LOGINS or login.lower().endswith("[bot]")


# Per-PR field selection, shared by every aliased pullRequest in a batch.
_PR_THREADS_FRAGMENT = """
fragment PRThreads on PullRequest {
  merged
  mergedAt
  reviewThreads(first: 100) {
    nodes {
      isResolved
      isOutdated
      resolvedBy { login }
      comments(first: 100) {
        nodes {
          author { login }
          body
          createdAt
          reactions(first: 10) {
            nodes {
              user { login }
            }
          }
        }
      }
    }
  }
}
"""

# How many PRs to request per GraphQL call. Batching collapses dozens of
# per-PR round trips into a handful, which is the main lever on both wall
# time and rate-limit pressure. 10 PRs x 100 threads x 100 comments stays
# well under GitHub's 500k-node query ceiling.
_THREADS_BATCH_SIZE = 10


def _build_threads_query(count: int) -> str:
    """Build a batched query aliasing `count` pullRequests under one repository."""
    var_decls = ", ".join(f"$n{i}: Int!" for i in range(count))
    aliases = "\n    ".join(
        f"pr{i}: pullRequest(number: $n{i}) {{ ...PRThreads }}" for i in range(count)
    )
    return (
        _PR_THREADS_FRAGMENT
        + f"\nquery($owner: String!, $repo: String!, {var_decls}) {{\n"
        f"  repository(owner: $owner, name: $repo) {{\n    {aliases}\n  }}\n}}\n"
    )

_REVIEWS_GIVEN_QUERY = """
query($q: String!, $after: String, $login: String!) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        repository { nameWithOwner }
        author { login }
        reviews(author: $login, first: 50) {
          nodes {
            state
            submittedAt
            body
            comments { totalCount }
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PRStats:
    opened: int = 0
    merged: int = 0
    closed_unmerged: int = 0
    avg_merge_hours: Optional[float] = None
    avg_description_length: float = 0.0
    empty_descriptions: int = 0
    avg_files_changed: float = 0.0
    avg_lines_changed: float = 0.0


@dataclass
class PRSample:
    number: int
    repo: str
    title: str
    body: str
    state: str   # "merged" | "closed" | "open"
    created_at: str = ""
    merged_at: str = ""
    is_draft: bool = False


@dataclass
class ThreadDetail:
    pr_number: int
    repo: str
    is_resolved: bool
    is_outdated: bool
    was_merged: bool
    reviewer: str
    comment_preview: str
    user_replied: bool
    user_reacted: bool = False
    user_reply_preview: str = ""
    resolved_by: str = ""
    created_at: str = ""


@dataclass
class ReviewStats:
    threads_received: int = 0
    resolved: int = 0
    ignored: int = 0
    reacted_only: int = 0
    replied_not_resolved: int = 0
    outdated: int = 0
    open_pr_unresolved: int = 0
    threads: list[ThreadDetail] = field(default_factory=list)

    @property
    def resolution_rate(self) -> float:
        closed = self.threads_received - self.open_pr_unresolved
        return self.resolved / closed if closed else 0.0

    @property
    def ignore_rate(self) -> float:
        return self.ignored / self.threads_received if self.threads_received else 0.0


@dataclass
class ReviewGiven:
    pr_number: int
    repo: str
    pr_author: str
    pr_title: str
    state: str        # APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED
    comments_count: int
    is_bot_pr: bool
    body: str = ""


@dataclass
class SelfThread:
    pr_number: int
    repo: str
    comment_preview: str
    replies_count: int = 0
    created_at: str = ""


@dataclass
class ReviewerActivity:
    human_prs_reviewed: int = 0
    human_approvals: int = 0
    human_changes_requested: int = 0
    human_comments: int = 0
    bot_prs_reviewed: int = 0
    bot_approvals: int = 0
    bot_changes_requested: int = 0
    bot_comments: int = 0
    reviews: list[ReviewGiven] = field(default_factory=list)

    @property
    def total_prs_reviewed(self) -> int:
        return self.human_prs_reviewed + self.bot_prs_reviewed


@dataclass
class UserData:
    login: str
    name: str = ""
    avatar_url: str = ""
    repos_analyzed: list[str] = field(default_factory=list)
    pull_requests: PRStats = field(default_factory=PRStats)
    reviews: ReviewStats = field(default_factory=ReviewStats)
    reviewer_activity: ReviewerActivity = field(default_factory=ReviewerActivity)
    pr_samples: list[PRSample] = field(default_factory=list)
    self_threads: list[SelfThread] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_cli_token() -> str:
    import subprocess
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if not a or not b:
        return None
    start, end = min(a, b), max(a, b)
    total_seconds = 0.0
    current = start
    while current < end:
        if current.weekday() < 5:
            next_day = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            segment_end = min(end, next_day)
            total_seconds += (segment_end - current).total_seconds()
        current = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return total_seconds / 3600


def _avg(lst: list) -> float:
    return sum(lst) / len(lst) if lst else 0.0


def _owner_repo_from_url(url: str) -> tuple[str, str] | None:
    parts = url.rstrip("/").rsplit("/", 2)
    return (parts[-2], parts[-1]) if len(parts) >= 3 else None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def collect_metrics(config: dict) -> UserData:
    """Collect GitHub metrics for the single configured user."""
    token = os.environ.get("GITHUB_TOKEN") or _gh_cli_token()
    if not token:
        raise ValueError(
            "GITHUB_TOKEN not found. Set it with:\n"
            "  1. export GITHUB_TOKEN=ghp_xxx\n"
            "  2. gh auth login  (GitHub CLI)\n"
            "  3. .env file with GITHUB_TOKEN=ghp_xxx"
        )

    client = GitHubClient(token)
    login = config["user"]

    dr = config.get("date_range", {})
    start_dt = _dt(dr["start"] + "T00:00:00Z") if dr.get("start") else None
    end_dt   = _dt(dr["end"]   + "T23:59:59Z") if dr.get("end")   else None

    info = client.get(f"/users/{login}")
    user = UserData(
        login=login,
        name=info.get("name") or login,
        avatar_url=info.get("avatar_url", ""),
    )

    org = os.environ.get("GITHUB_ORG", "")

    log(f"\nAnalyzing @{login}...")

    if org:
        log("  pull requests (full org)...")
        pr_map = _prs_org(client, org, login, user, start_dt, end_dt)

        total_prs = sum(len(v) for v in pr_map.values())
        _preflight_graphql_budget(client, pr_map)
        log(f"  review threads ({total_prs} PRs in {len(pr_map)} repos)...")
        for (owner, repo), pr_numbers in pr_map.items():
            _review_threads(client, owner, repo, login, user, pr_numbers)

        user.repos_analyzed = sorted(f"{o}/{r}" for o, r in pr_map)
    else:
        for repo_cfg in config.get("repositories", []):
            owner, repo = repo_cfg["owner"], repo_cfg["repo"]
            repo_full = f"{owner}/{repo}"
            log(f"  [{repo_full}] pull requests...")
            pr_numbers = _prs_repo(client, owner, repo, login, user, start_dt, end_dt)

            log(f"  [{repo_full}] review threads ({len(pr_numbers)} PRs)...")
            _review_threads(client, owner, repo, login, user, pr_numbers)

            user.repos_analyzed.append(repo_full)

    log("  reviews given...")
    _collect_reviews_given(client, org or "", login, user, start_dt, end_dt)

    return user


# ---------------------------------------------------------------------------
# PR collectors
# ---------------------------------------------------------------------------

def _prs_org(
    client: GitHubClient,
    org: str, login: str, user: UserData,
    start_dt: Optional[datetime], end_dt: Optional[datetime],
) -> dict[tuple[str, str], list[int]]:
    q = f"org:{org} type:pr author:{login}"
    if start_dt:
        q += f" created:>={start_dt.date()}"
    items = client.paginate("/search/issues", {"q": q})
    return _process_prs(client, items, user, end_dt)


def _prs_repo(
    client: GitHubClient,
    owner: str, repo: str, login: str, user: UserData,
    start_dt: Optional[datetime], end_dt: Optional[datetime],
) -> list[int]:
    q = f"repo:{owner}/{repo} type:pr author:{login}"
    if start_dt:
        q += f" created:>={start_dt.date()}"
    items = client.paginate("/search/issues", {"q": q})
    pr_map = _process_prs(client, items, user, end_dt)
    return pr_map.get((owner, repo), [])


def _process_prs(
    client: GitHubClient,
    items: list, user: UserData,
    end_dt: Optional[datetime],
) -> dict[tuple[str, str], list[int]]:
    s = user.pull_requests
    merge_times, desc_lengths, files_list, lines_list = [], [], [], []
    pr_map: dict[tuple[str, str], list[int]] = {}
    empty_desc = 0

    for item in items:
        created = _dt(item.get("created_at"))
        if end_dt and created and created > end_dt:
            continue

        parsed = _owner_repo_from_url(item.get("repository_url", ""))
        if not parsed:
            continue
        owner, repo = parsed
        pr_num = item["number"]

        pr_meta = item.get("pull_request") or {}
        merged_at = _dt(pr_meta.get("merged_at"))
        state = item.get("state", "open")

        # A PR closed while still a draft was never a real submission: skip it
        # entirely so it counts toward neither opened nor closed_unmerged.
        if state == "closed" and not merged_at and item.get("draft", False):
            continue

        pr_map.setdefault((owner, repo), []).append(pr_num)
        s.opened += 1

        if merged_at:
            s.merged += 1
            t = _hours(created, merged_at)
            if t is not None:
                merge_times.append(t)
        elif state == "closed":
            s.closed_unmerged += 1

        body = (item.get("body") or "").strip()
        desc_lengths.append(len(body))
        if not body:
            empty_desc += 1

        if len(user.pr_samples) < 30:
            pr_state = "merged" if merged_at else state
            user.pr_samples.append(PRSample(
                number=pr_num,
                repo=f"{owner}/{repo}",
                title=item.get("title", ""),
                body=body[:600],
                state=pr_state,
                created_at=item.get("created_at", ""),
                merged_at=pr_meta.get("merged_at") or "",
                is_draft=item.get("draft", False),
            ))

        try:
            detail = client.get(f"/repos/{owner}/{repo}/pulls/{pr_num}")
            files_list.append(detail.get("changed_files", 0))
            lines_list.append(detail.get("additions", 0) + detail.get("deletions", 0))
        except Exception:
            pass

    if merge_times:
        s.avg_merge_hours = _avg(merge_times)
    if desc_lengths:
        s.avg_description_length = _avg(desc_lengths)
    s.empty_descriptions += empty_desc
    if files_list:
        s.avg_files_changed = _avg(files_list)
        s.avg_lines_changed = _avg(lines_list)

    return pr_map


# ---------------------------------------------------------------------------
# Review threads
# ---------------------------------------------------------------------------

def _estimate_thread_requests(pr_map: dict) -> int:
    """GraphQL requests needed to fetch every PR's review threads (batched)."""
    return sum(
        (len(pr_numbers) + _THREADS_BATCH_SIZE - 1) // _THREADS_BATCH_SIZE
        for pr_numbers in pr_map.values()
    )


def _preflight_graphql_budget(client: GitHubClient, pr_map: dict) -> None:
    """Abort before fetching threads if the GraphQL budget is clearly too low.

    Each batched threads request costs roughly 1 point, but a complex one can
    cost more, so we require a margin over the bare estimate. Failing here is
    better than running halfway and silently dropping PRs at the rate limit.
    """
    needed = _estimate_thread_requests(pr_map)
    if needed == 0:
        return

    try:
        budget = client.graphql_rate_limit()
    except Exception as e:
        log(f"  Warning: could not check GraphQL rate limit: {e}")
        return

    remaining = budget["remaining"]
    required = needed * _BUDGET_SAFETY_FACTOR
    if remaining >= required:
        return

    reset_at = budget.get("reset", 0)
    when = (
        datetime.fromtimestamp(reset_at, tz=timezone.utc).strftime("%H:%M UTC")
        if reset_at else "unknown"
    )
    raise InsufficientRateLimitError(
        f"GraphQL rate limit too low: {remaining} points left, need about "
        f"{required} for {needed} batched request(s). Resets at {when}. "
        f"Wait for the reset, then re-run."
    )


def _review_threads(
    client: GitHubClient,
    owner: str, repo: str, login: str,
    user: UserData, pr_numbers: list[int],
) -> None:
    for start in range(0, len(pr_numbers), _THREADS_BATCH_SIZE):
        batch = pr_numbers[start:start + _THREADS_BATCH_SIZE]
        query = _build_threads_query(len(batch))
        variables = {"owner": owner, "repo": repo}
        variables.update({f"n{i}": num for i, num in enumerate(batch)})

        try:
            data = client.graphql(query, variables)
        except Exception as e:
            log(f"    Warning: could not fetch threads for {owner}/{repo} "
                f"PRs {batch[0]}-{batch[-1]}: {e}")
            continue

        repository = data.get("repository") or {}
        for i, pr_num in enumerate(batch):
            pr_data = repository.get(f"pr{i}") or {}
            _process_pr_threads(pr_data, owner, repo, login, user, pr_num)


def _process_pr_threads(
    pr_data: dict, owner: str, repo: str, login: str,
    user: UserData, pr_num: int,
) -> None:
    s = user.reviews
    was_merged = pr_data.get("merged", False)
    merged_at = _dt(pr_data.get("mergedAt"))
    threads = (pr_data.get("reviewThreads") or {}).get("nodes") or []

    for thread in threads:
        comments = (thread.get("comments") or {}).get("nodes") or []
        if not comments:
            continue

        first_author = (comments[0].get("author") or {}).get("login", "")
        if first_author == login:
            # Self-started thread: collect as proactive communication
            replies = [c for c in comments[1:] if (c.get("author") or {}).get("login") != login]
            user.self_threads.append(SelfThread(
                pr_number=pr_num,
                repo=f"{owner}/{repo}",
                comment_preview=comments[0].get("body", "")[:200],
                replies_count=len(replies),
                created_at=comments[0].get("createdAt", ""),
            ))
            continue

        first_comment_at = _dt(comments[0].get("createdAt"))
        if was_merged and merged_at and first_comment_at and first_comment_at > merged_at:
            continue

        is_resolved = thread.get("isResolved", False)
        is_outdated = thread.get("isOutdated", False)

        user_reply = ""
        for c in comments[1:]:
            if (c.get("author") or {}).get("login") == login:
                user_reply = (c.get("body") or "")[:200]
                break
        user_replied = bool(user_reply)

        user_reacted = False
        for c in comments:
            reactions = (c.get("reactions") or {}).get("nodes") or []
            for reaction in reactions:
                if (reaction.get("user") or {}).get("login") == login:
                    user_reacted = True
                    break
            if user_reacted:
                break

        if _is_bot(first_author) and not user_replied and not user_reacted and not is_resolved:
            continue

        s.threads_received += 1

        if is_resolved:
            s.resolved += 1
        elif is_outdated:
            s.outdated += 1
        elif was_merged:
            if user_reacted and not user_replied:
                s.reacted_only += 1
            else:
                s.ignored += 1
        elif user_replied:
            s.replied_not_resolved += 1
        else:
            s.open_pr_unresolved += 1

        s.threads.append(ThreadDetail(
            pr_number=pr_num,
            repo=f"{owner}/{repo}",
            is_resolved=is_resolved,
            is_outdated=is_outdated,
            was_merged=was_merged,
            reviewer=first_author,
            comment_preview=comments[0].get("body", "")[:200],
            user_replied=user_replied,
            user_reacted=user_reacted,
            user_reply_preview=user_reply,
            resolved_by=(thread.get("resolvedBy") or {}).get("login", ""),
            created_at=comments[0].get("createdAt", ""),
        ))


# ---------------------------------------------------------------------------
# Reviews given
# ---------------------------------------------------------------------------

def _collect_reviews_given(
    client: GitHubClient,
    org: str, login: str, user: UserData,
    start_dt: Optional[datetime], end_dt: Optional[datetime],
) -> None:
    s = user.reviewer_activity
    seen_prs: set[tuple[str, int]] = set()

    q = f"type:pr reviewed-by:{login} -author:{login}"
    if org:
        q += f" org:{org}"
    if start_dt:
        q += f" updated:>={start_dt.date()}"
    if end_dt:
        q += f" updated:<={end_dt.date()}"

    cursor = None
    page = 0
    while True:
        try:
            data = client.graphql(
                _REVIEWS_GIVEN_QUERY,
                {"q": q, "after": cursor, "login": login},
            )
        except Exception as e:
            raise RuntimeError(f"Could not fetch reviews given by @{login}: {e}") from e

        search  = data.get("search") or {}
        nodes   = search.get("nodes") or []
        info    = search.get("pageInfo") or {}
        page += 1
        if page > 1:
            log(f"    page {page} ({len(seen_prs)} PRs accumulated)...")

        for pr in nodes:
            if not pr:
                continue
            repo      = (pr.get("repository") or {}).get("nameWithOwner", "")
            pr_number = pr.get("number", 0)
            pr_author = (pr.get("author") or {}).get("login", "")

            if pr_author == login:
                continue
            if org and not repo.startswith(f"{org}/"):
                continue

            is_bot_pr = _is_bot(pr_author)
            reviews   = (pr.get("reviews") or {}).get("nodes") or []

            for review in reviews:
                state      = review.get("state", "")
                comments_n = (review.get("comments") or {}).get("totalCount", 0)
                submitted  = _dt(review.get("submittedAt"))

                if state == "PENDING":
                    continue
                if start_dt and submitted and submitted < start_dt:
                    continue
                if end_dt and submitted and submitted > end_dt:
                    continue

                rg = ReviewGiven(
                    pr_number=pr_number,
                    repo=repo,
                    pr_author=pr_author,
                    pr_title=pr.get("title", ""),
                    state=state,
                    comments_count=comments_n,
                    is_bot_pr=is_bot_pr,
                    body=(review.get("body") or "")[:300],
                )
                s.reviews.append(rg)

                pr_key   = (repo, pr_number)
                is_new_pr = pr_key not in seen_prs
                seen_prs.add(pr_key)

                if is_bot_pr:
                    if is_new_pr:
                        s.bot_prs_reviewed += 1
                    if state == "APPROVED":
                        s.bot_approvals += 1
                    elif state == "CHANGES_REQUESTED":
                        s.bot_changes_requested += 1
                    else:
                        s.bot_comments += 1
                else:
                    if is_new_pr:
                        s.human_prs_reviewed += 1
                    if state == "APPROVED":
                        s.human_approvals += 1
                    elif state == "CHANGES_REQUESTED":
                        s.human_changes_requested += 1
                    else:
                        s.human_comments += 1

        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
