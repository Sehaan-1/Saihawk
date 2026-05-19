"""
github_fetcher.py
GitHub Context Fetcher — Data Ingestion Agent.

Hits the GitHub REST API to pull live repository states for your profile.
Compiles a master context string that the prompt_builder injects per application.
Runs once at pipeline startup so the entire job loop uses a fresh snapshot.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from loguru import logger


# Repositories to include in context. Only these will be analysed.
_DEFAULT_TARGET_REPOS: list[str] = [
    "Tender-Royal-Pulse",
    "xai-fraud-detection",
]


def get_live_github_context(
    token: str | None = None,
    username: str | None = None,
    target_repos: list[str] | None = None,
) -> str:
    """
    Pull live repository metadata from GitHub and compile a context string.

    Args:
        token:        GitHub Personal Access Token (PAT). Falls back to
                      GITHUB_TOKEN env var if not provided.
        username:     GitHub username. Falls back to GITHUB_USERNAME env var.
        target_repos: List of repository names to include. Falls back to
                      TARGET_REPOS env var (comma-separated), then defaults.

    Returns:
        A formatted multi-line string summarising your GitHub portfolio,
        ready for injection into the LLM prompt.
    """
    token = token or os.getenv("GITHUB_TOKEN", "")
    username = username or os.getenv("GITHUB_USERNAME", "Sehaan-1")

    if target_repos is None:
        env_repos = os.getenv("TARGET_REPOS", "")
        target_repos = [r.strip() for r in env_repos.split(",") if r.strip()] or _DEFAULT_TARGET_REPOS

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        logger.warning("No GITHUB_TOKEN set. API rate limits will apply (60 req/hr).")

    logger.info(f"Fetching GitHub context for @{username}, repos: {target_repos}")

    context_lines: list[str] = [
        f"Live GitHub Portfolio — @{username}\n",
        "=" * 55,
    ]

    for repo_name in target_repos:
        repo_data = _fetch_repo(username, repo_name, headers)
        if repo_data is None:
            context_lines.append(f"\n⚠️  Could not fetch repo: {repo_name}")
            continue

        readme = _fetch_readme(username, repo_name, headers)
        languages = _fetch_languages(username, repo_name, headers)

        context_lines.extend([
            f"\n📁 Project: {repo_data.get('name', repo_name)}",
            f"   Description: {repo_data.get('description') or 'No description.'}",
            f"   Primary Language: {repo_data.get('language') or 'Multiple'}",
            f"   Stars: {repo_data.get('stargazers_count', 0)}  |  "
            f"Forks: {repo_data.get('forks_count', 0)}",
            f"   All Languages: {', '.join(languages) if languages else 'N/A'}",
            f"   README Summary: {_truncate(readme, 400)}",
            "-" * 55,
        ])

    full_context = "\n".join(context_lines)
    logger.debug(f"GitHub context compiled ({len(full_context)} chars).")
    return full_context


# ---------------------------------------------------------------------------
# Internal API helpers
# ---------------------------------------------------------------------------

def _fetch_repo(username: str, repo_name: str, headers: dict) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{username}/{repo_name}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"Failed to fetch repo {repo_name}: {exc}")
        return None


def _fetch_readme(username: str, repo_name: str, headers: dict) -> str:
    """Fetch and decode the README content (first 1500 chars) for context."""
    import base64
    url = f"https://api.github.com/repos/{username}/{repo_name}/readme"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        content_b64: str = resp.json().get("content", "")
        decoded = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
        # Strip markdown headers and clean up whitespace
        cleaned = " ".join(decoded.replace("#", "").split())
        return cleaned[:1500]
    except Exception:
        return "README not available."


def _fetch_languages(username: str, repo_name: str, headers: dict) -> list[str]:
    """Return a list of programming languages used in the repo."""
    url = f"https://api.github.com/repos/{username}/{repo_name}/languages"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return list(resp.json().keys())
    except Exception:
        return []


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text
