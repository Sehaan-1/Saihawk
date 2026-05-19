"""
github_fetcher.py — Saihawk LLM Engine  [v2 — Dynamic Portfolio Mode]
========================================================================
Pulls lightweight metadata for ALL of your original (non-forked),
recently-pushed public repositories via the GitHub REST API.

WHY NO TARGET_REPOS:
    Hardcoding a whitelist is brittle. Push a new project tonight and the
    agent uses it tomorrow — zero config changes required.

WHY METADATA-ONLY (no READMEs):
    Per-repo cost: ~80 chars.  20 repos ≈ 1 600 chars ≈ ~400 tokens.
    Full READMEs for the same repos: ~40 000 chars ≈ ~10 000 tokens.
    The LLM router in prompt_builder.py needs descriptions + languages to
    route intelligently — it does not need full documentation.

Pipeline position:
    github_fetcher.py  ─►  prompt_builder.py  ─►  answerer.py
      (this file)

Environment Variables Required (.env):
    GITHUB_TOKEN     – PAT with 'read:user' and 'public_repo' scopes
    GITHUB_USERNAME  – your GitHub handle (e.g. "Sehaan-1")
    GITHUB_MAX_REPOS – (optional) max repos to include, default 25

What It Extracts Per Repo:
    - Name, description, primary language
    - Topics / tags
    - Star count
    - Last push date

Filters Applied:
    - Forks excluded (fork=False)
    - Empty repos excluded (no description AND no language)
    - Sorted by most recently pushed — your active work surfaces first
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GITHUB_API_BASE  = "https://api.github.com"
_REQUEST_TIMEOUT  = 10.0        # seconds per API call
_RETRY_ATTEMPTS   = 3           # retries on transient failures
_RETRY_BACKOFF    = 2.0         # exponential backoff base (seconds)
_RATE_LIMIT_PAUSE = 60.0        # pause (seconds) when rate-limited
_DEFAULT_MAX_REPOS = 25         # cap on repos included in context


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class RepoContext:
    """Lightweight metadata snapshot of a single GitHub repository."""
    slug: str                              # owner/repo
    name: str                              # display name
    description: str                       # repo description or empty string
    language: str                          # primary language or "Not specified"
    topics: List[str] = field(default_factory=list)
    stars: int = 0
    last_pushed: str = ""                  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# Internal HTTP Helper
# ---------------------------------------------------------------------------

def _get_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict,
    params: Optional[dict],
    label: str,
) -> Optional[httpx.Response]:
    """
    GET with exponential-backoff retry.

    Returns the Response on 200, None on 404, retries on everything else.
    Never raises — failures are logged and the caller handles None.
    """
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = client.get(
                url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT
            )
            logger.debug(
                f"[GitHubFetcher] {label} — HTTP {response.status_code} "
                f"(attempt {attempt}/{_RETRY_ATTEMPTS})"
            )

            if response.status_code == 200:
                return response

            if response.status_code == 404:
                logger.warning(f"[GitHubFetcher] {label} — 404. Skipping.")
                return None

            if response.status_code in (403, 429):
                reset_ts = response.headers.get("X-RateLimit-Reset")
                wait = (
                    max(0, int(reset_ts) - int(time.time())) + 5
                    if reset_ts else _RATE_LIMIT_PAUSE
                )
                logger.warning(
                    f"[GitHubFetcher] {label} — Rate limited. "
                    f"Sleeping {wait}s before retry."
                )
                time.sleep(wait)
                continue

            logger.warning(
                f"[GitHubFetcher] {label} — HTTP {response.status_code}. "
                f"Retrying in {_RETRY_BACKOFF ** attempt:.1f}s."
            )
            time.sleep(_RETRY_BACKOFF ** attempt)

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                f"[GitHubFetcher] {label} — Network error (attempt {attempt}): "
                f"{exc}. Retrying in {_RETRY_BACKOFF ** attempt:.1f}s."
            )
            time.sleep(_RETRY_BACKOFF ** attempt)

    logger.error(
        f"[GitHubFetcher] {label} — All {_RETRY_ATTEMPTS} attempts exhausted."
    )
    return None


# ---------------------------------------------------------------------------
# Portfolio Fetcher — All Non-Forked Repos
# ---------------------------------------------------------------------------

def _fetch_all_repos(
    client: httpx.Client,
    headers: dict,
    username: str,
    max_repos: int,
) -> List[RepoContext]:
    """
    Pull all non-forked public repos for `username`, sorted by most recently
    pushed. Uses pagination to handle accounts with many repos.

    Returns a list capped at `max_repos` entries.
    """
    results: List[RepoContext] = []
    page = 1

    while len(results) < max_repos:
        label = f"repos/{username}?page={page}"
        response = _get_with_retry(
            client=client,
            url=f"{_GITHUB_API_BASE}/users/{username}/repos",
            headers=headers,
            params={
                "type"    : "owner",      # exclude collaborator repos
                "sort"    : "pushed",     # most recently active first
                "direction": "desc",
                "per_page": 100,          # max allowed by GitHub API
                "page"    : page,
            },
            label=label,
        )

        if response is None:
            logger.warning(f"[GitHubFetcher] Failed to fetch page {page}. Stopping.")
            break

        batch = response.json()
        if not batch:
            # No more pages
            break

        for repo in batch:
            # --- Filter: skip forks ---
            if repo.get("fork", False):
                continue

            # --- Filter: skip truly empty repos (no description AND no language) ---
            description = (repo.get("description") or "").strip()
            language    = repo.get("language") or ""
            if not description and not language:
                logger.debug(
                    f"[GitHubFetcher] Skipping empty repo: {repo.get('name')}"
                )
                continue

            ctx = RepoContext(
                slug        = repo.get("full_name", f"{username}/{repo.get('name', '')}"),
                name        = repo.get("name", ""),
                description = description,
                language    = language or "Not specified",
                topics      = repo.get("topics", []),
                stars       = repo.get("stargazers_count", 0),
                last_pushed = (repo.get("pushed_at") or "")[:10],
            )
            results.append(ctx)

            if len(results) >= max_repos:
                break

        # Check if there are more pages
        link_header = response.headers.get("Link", "")
        if 'rel="next"' not in link_header:
            break

        page += 1

    logger.info(
        f"[GitHubFetcher] Fetched {len(results)} qualifying repos "
        f"(cap={max_repos}, page={page})."
    )
    return results


# ---------------------------------------------------------------------------
# Context String Formatter
# ---------------------------------------------------------------------------

def _format_context_string(repos: List[RepoContext], username: str) -> str:
    """
    Convert repo list into a compact, token-efficient context string.

    Format per repo (approx 80–120 chars):
        [N] repo-name | Language | "Description" | topics: a,b | ★ stars | pushed YYYY-MM-DD
    """
    if not repos:
        return (
            "CANDIDATE'S ACTIVE GITHUB PORTFOLIO: No qualifying repositories found. "
            "Ground all answers in the baseline stack only: "
            "Python, Java, C++, Docker, AWS, GCP, ML, DevOps."
        )

    lines: List[str] = [
        f"CANDIDATE'S ACTIVE GITHUB PORTFOLIO  ({username})",
        f"Repositories listed: {len(repos)}  |  Sorted by: most recently pushed",
        "-" * 70,
    ]

    for i, repo in enumerate(repos, start=1):
        topics_str = ", ".join(repo.topics) if repo.topics else "-"
        lines.append(
            f"[{i:02d}] {repo.name}"
            f" | {repo.language}"
            f" | \"{repo.description or 'No description'}\""
            f" | topics: {topics_str}"
            f" | stars: {repo.stars}"
            f" | pushed: {repo.last_pushed}"
        )

    lines.append("-" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_github_context(
    token: Optional[str]    = None,
    username: Optional[str] = None,
    max_repos: Optional[int] = None,
) -> str:
    """
    Main entry point. Fetches all non-forked, recently pushed public repos
    for the configured GitHub user and returns a compact metadata context
    string ready for injection into prompt_builder.build_prompt().

    Parameters
    ----------
    token     : GitHub PAT. Defaults to GITHUB_TOKEN env var.
    username  : GitHub handle. Defaults to GITHUB_USERNAME env var.
    max_repos : Max repos to include. Defaults to GITHUB_MAX_REPOS env var
                or 25 if not set.

    Returns
    -------
    str
        Compact `live_github_context` string for prompt injection.

    Raises
    ------
    EnvironmentError
        If token or username cannot be resolved.
    """
    token    = token    or os.getenv("GITHUB_TOKEN",    "")
    username = username or os.getenv("GITHUB_USERNAME", "")
    max_repos = max_repos or int(os.getenv("GITHUB_MAX_REPOS", str(_DEFAULT_MAX_REPOS)))

    if not token:
        raise EnvironmentError(
            "[GitHubFetcher] GITHUB_TOKEN is not set. Add it to your .env file."
        )
    if not username:
        raise EnvironmentError(
            "[GitHubFetcher] GITHUB_USERNAME is not set. Add it to your .env file."
        )

    logger.info(
        f"[GitHubFetcher] Fetching portfolio for user='{username}' "
        f"(max_repos={max_repos}) …"
    )

    headers = {
        "Authorization"       : f"Bearer {token}",
        "Accept"              : "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with httpx.Client() as client:
        repos = _fetch_all_repos(client, headers, username, max_repos)

    context = _format_context_string(repos, username)

    logger.info(
        f"[GitHubFetcher] Context compiled: "
        f"{len(repos)} repos, {len(context)} chars."
    )
    return context


# ---------------------------------------------------------------------------
# CLI Smoke Test  (python -m src.llm.github_fetcher)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    print("=" * 70)
    print("  GitHubFetcher v2 — Dynamic Portfolio Mode — Smoke Test")
    print("=" * 70)
    try:
        ctx = fetch_github_context()
        # encode to cp1252-safe output for Windows terminals
        safe = ctx.encode("ascii", errors="replace").decode("ascii")
        print(safe)
        print(f"\n[OK] {len(ctx)} chars generated. No TARGET_REPOS needed.")
        sys.exit(0)
    except EnvironmentError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
