"""Executing approved ``task.create`` and ``vault.append`` actions.

The vault is only ever written through a pull request on the vault repo (never a push to the
base branch). Approving opens one PR per action on ``jarvis-live/action-<id8>``, cut from the
base branch's current head, and (``actions_vault_merge``) merges it once it is verified to change
exactly the one approved file. Every effect carries ``^jv-<id8>`` (an Obsidian block id), so
running an action again finds it already there instead of writing twice."""

import asyncio
import re
import uuid
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.actions.service import ExecutionError
from jarvis_live.config import Settings
from jarvis_live.db.models import ActionProposal
from jarvis_live.finalize.outbox import latest_final_note
from jarvis_live.publish.github import GitHubClient, GitHubError, PullRequest
from jarvis_live.workitems.template import session_url

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MARKER = re.compile(r"\^jv-[0-9a-f]{8}")


def marker_of(proposal_id: uuid.UUID) -> str:
    return f"^jv-{proposal_id.hex[:8]}"


def insert_task(board: str, line: str, section: str) -> str | None:
    """``board`` with ``line`` added at the end of ``## section``'s list; None if the board has
    no such section (the caller reports that rather than guessing a place)."""
    lines = board.split("\n")
    head = next(
        (i for i, x in enumerate(lines) if re.fullmatch(rf"##\s+{re.escape(section)}\s*", x, re.I)),
        None,
    )
    if head is None:
        return None
    end = next((i for i in range(head + 1, len(lines)) if re.match(r"##\s", lines[i])), len(lines))
    items = [i for i in range(head + 1, end) if re.match(r"- \[.\]", lines[i])]
    if items:
        lines.insert(items[-1] + 1, line)
    else:  # an empty column: after its heading and italic description
        at = head + 1
        while at < end and (not lines[at].strip() or re.fullmatch(r"[*_].*[*_]", lines[at])):
            at += 1
        lines[at:at] = ["", line] if at == head + 1 else [line]
    return "\n".join(lines)


class VaultExecutor:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        github: GitHubClient,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._gh = github
        self._lock = asyncio.Lock()  # one vault write at a time: each reads, then commits

    # --- Executor ---------------------------------------------------------------------------

    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        path = self._target(proposal)
        marker = marker_of(proposal.id)
        branch = f"{self._cfg.publish_branch_prefix}action-{proposal.id.hex[:8]}"
        base = self._cfg.publish_base_branch
        async with self._lock:
            try:
                landed = await self._gh.get_file(path, base)
                if landed is not None and marker in landed.text:
                    return {"landed": "already_present", "path": path, "merged": True}
                await self._gh.ensure_branch(branch, await self._gh.branch_head(base))
                current = await self._gh.get_file(path, branch)
                new = await self._edit(proposal, path, current.text if current else None)
                await self._gh.put_file(
                    path,
                    new,
                    branch=branch,
                    sha=current.sha if current else None,
                    message=self._message(proposal),
                    committer={
                        "name": self._cfg.publish_committer_name,
                        "email": self._cfg.publish_committer_email,
                    },
                )
                pr = await self._gh.open_pr(
                    head=branch, base=base, title=self._title(proposal), body=self._body(proposal)
                )
                pr = await self._gh.get_pr(pr.number)  # the head as it is now
                result: dict[str, Any] = {
                    "path": path,
                    "branch": branch,
                    "pr_number": pr.number,
                    "pr_url": pr.url,
                }
                if not self._cfg.actions_vault_merge:
                    return {**result, "landed": "pr_open", "merged": False}
                await self._merge(pr, path)
                await self._gh.delete_branch(branch)
                return {**result, "landed": "merged", "merged": True}
            except GitHubError as e:
                raise ExecutionError(f"GitHub: {e}") from e

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        """Read-only: did an earlier, interrupted execution land (or open its PR)?"""
        path = self._target(proposal)
        marker = marker_of(proposal.id)
        branch = f"{self._cfg.publish_branch_prefix}action-{proposal.id.hex[:8]}"
        try:
            landed = await self._gh.get_file(path, self._cfg.publish_base_branch)
            if landed is not None and marker in landed.text:
                return {"landed": "already_present", "path": path, "merged": True}
            if not self._cfg.actions_vault_merge and (pr := await self._gh.find_pr(branch)):
                return {
                    "landed": "pr_open",
                    "path": path,
                    "pr_number": pr.number,
                    "pr_url": pr.url,
                    "merged": False,
                }
        except GitHubError:
            return None
        return None

    # --- steps ------------------------------------------------------------------------------

    def _target(self, p: ActionProposal) -> str:
        return self._cfg.actions_work_tracker_path if p.kind == "task.create" else p.payload["path"]

    async def _edit(self, p: ActionProposal, path: str, current: str | None) -> str:
        link = await self._session_link(p)
        marker = marker_of(p.id)
        if p.kind == "vault.append":
            if current is None:
                raise ExecutionError(f"{path} no longer exists in the vault")
            body = _MARKER.sub("", p.payload["text"]).rstrip()
            block = f"{body}{(' — ' + link) if link else ''} {marker}"
            return current.rstrip("\n") + f"\n\n{block}\n"
        if current is None:
            raise ExecutionError(f"the Work Tracker ({path}) does not exist in the vault")
        text = _MARKER.sub("", p.payload["text"]).strip()
        due = (p.payload.get("due") or "").strip()
        if due and not _ISO_DAY.match(due):
            text += f" (due: {due})"
        card = f"- [ ] {text}{(' ' + link) if link else ''}"
        if _ISO_DAY.match(due):
            card += f" 📅 {due}"
        new = insert_task(current, f"{card} {marker}", self._cfg.actions_tracker_section)
        if new is None:
            raise ExecutionError(
                f"the Work Tracker has no '## {self._cfg.actions_tracker_section}' section"
            )
        return new

    async def _session_link(self, p: ActionProposal) -> str:
        """``[[note]] [session](url#t=…)``: the session's note if it has one yet, and the
        session page at the moment it was heard."""
        async with self._sm() as db:
            note = await latest_final_note(db, p.session_id)
        parts = [f"[[{PurePosixPath(note[0]).stem}]]"] if note else []
        t_ms = p.evidence[0]["start_ms"] if p.evidence else None
        if url := session_url(self._cfg, str(p.session_id), t_ms):
            parts.append(f"[session]({url})")
        return " ".join(parts)

    async def _merge(self, pr: PullRequest, path: str) -> None:
        files = await self._gh.pr_files(pr.number)
        if files != [path]:
            raise ExecutionError(
                f"not merged: the pull request changes {', '.join(files) or 'nothing'}, "
                f"expected only {path}; review {pr.url}"
            )
        for attempt in (1, 2):
            try:
                await self._gh.merge(
                    pr.number,
                    sha=pr.head_sha,
                    method=self._cfg.publish_merge_method,
                    title=f"jarvis-live: action (#{pr.number})",
                )
                return
            except GitHubError as e:
                if attempt == 1 and e.status == 405 and pr.mergeable is None:
                    await asyncio.sleep(2)  # GitHub has not computed mergeability yet
                    continue
                raise ExecutionError(
                    f"merge failed: {e}; the pull request is open: {pr.url}"
                ) from e

    # --- text -------------------------------------------------------------------------------

    @staticmethod
    def _what(p: ActionProposal) -> str:
        return "task" if p.kind == "task.create" else "note"

    def _title(self, p: ActionProposal) -> str:
        text = " ".join(str(p.payload["text"]).split())[:60]
        return f"jarvis-live: {self._what(p)}: {text}"

    def _message(self, p: ActionProposal) -> str:
        return f"{self._title(p)}\n\nSession {p.session_id}. Action {p.id}."

    def _body(self, p: ActionProposal) -> str:
        quote = (p.evidence[0]["text"] if p.evidence else "").strip()
        lines = [
            f"Proposed by Jarvis Live from session `{p.session_id}`, "
            f"approved by `{p.approved_by}`.",
            f"Action `{p.id}` (payload sha256 `{p.payload_sha256}`).",
        ]
        return "\n\n".join(lines + ([f"> {quote}"] if quote else []))
