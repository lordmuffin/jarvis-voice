"""Which executors a deployment can run, from the credentials it holds.

A kind without an executor is not offered to the copilot where that matters (PR comments) and
fails visibly when approved: lab, which has no vault write credential, runs reminders only."""

from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.actions.comments import CommentTokens, PrCommentExecutor, commentable_repos
from jarvis_live.actions.reminders import ReminderExecutor
from jarvis_live.actions.service import Executor
from jarvis_live.actions.vault import VaultExecutor
from jarvis_live.config import Settings
from jarvis_live.dispatch.githubapp import AppTokens
from jarvis_live.notify.gotify import Notifier
from jarvis_live.publish.github import GitHubClient


def build_executors(
    cfg: Settings,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    client: httpx.AsyncClient,
    vault_repo: GitHubClient | None,
    notifier: Notifier,
    tokens: CommentTokens | None,
) -> tuple[dict[str, Executor], frozenset[str]]:
    """The executors by kind, and the repos a PR comment may name."""
    executors: dict[str, Executor] = {
        "reminder.schedule": ReminderExecutor(cfg, sessionmaker, notifier)
    }
    if vault_repo is not None:  # the vault repo's write credential
        vault_writer = VaultExecutor(cfg, sessionmaker, vault_repo)
        executors["task.create"] = vault_writer
        executors["vault.append"] = vault_writer
    if tokens is None and (
        cfg.github_app_id and cfg.github_app_installation_id and cfg.github_app_key_file
    ):
        tokens = AppTokens(
            client,
            api=cfg.github_api,
            app_id=cfg.github_app_id,
            installation_id=cfg.github_app_installation_id,
            key_file=Path(cfg.github_app_key_file),
        )
    commentable: frozenset[str] = frozenset()
    if tokens is not None:  # the GitHub App; only repos whose PRs live on GitHub
        executors["git.pr_comment"] = PrCommentExecutor(cfg, client, tokens)
        commentable = commentable_repos(cfg)
    return executors, commentable
