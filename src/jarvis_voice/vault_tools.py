"""Vault tool definitions and handlers for the Kai voice agent."""
from __future__ import annotations

import pathlib
import subprocess
from datetime import date

VAULT_PATH = pathlib.Path("/mnt/notes")
MAX_CONTENT_CHARS = 6000


VAULT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": (
                "Read a specific note from Andrew's vault by relative path. "
                "Use for: daily notes, sprint board, project notes, TASKS.md. "
                "Paths are relative to vault root, e.g. '20 Areas/Personal/Sprints.md'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the note within the vault",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": (
                "Search the vault for notes containing a keyword or phrase. "
                "Returns a list of matching file paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term or phrase"},
                    "directory": {
                        "type": "string",
                        "description": "Subdirectory to search within (optional)",
                        "default": "",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sprint_state",
            "description": (
                "Get the current sprint board, daily highlight, and open tasks. "
                "Call this when Andrew asks about priorities, what's next, or what he should work on."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_note",
            "description": (
                "Append text to an existing note in the vault. "
                "Use for capturing voice thoughts into Brain Dump, adding action items to TASKS.md, "
                "or logging to today's daily note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the note within the vault"},
                    "text": {"type": "string", "description": "Text to append to the note"},
                },
                "required": ["path", "text"],
            },
        },
    },
]


def execute_tool(tool_name: str, args: dict) -> str:
    try:
        if tool_name == "read_note":
            return _read_note(args["path"])
        elif tool_name == "search_vault":
            return _search_vault(args["query"], args.get("directory", ""))
        elif tool_name == "get_sprint_state":
            return _get_sprint_state()
        elif tool_name == "append_to_note":
            return _append_to_note(args["path"], args["text"])
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        return f"Tool error ({tool_name}): {e}"


def _read_note(path: str) -> str:
    target = VAULT_PATH / path
    if not target.exists():
        name = pathlib.Path(path).name
        matches = list(VAULT_PATH.rglob(f"*{name}"))
        if matches:
            target = matches[0]
        else:
            return f"Not found: {path}. Try search_vault to locate it."
    content = target.read_text(encoding="utf-8", errors="replace")
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n\n[truncated]"
    return content


def _search_vault(query: str, directory: str = "") -> str:
    search_root = VAULT_PATH / directory if directory else VAULT_PATH
    try:
        result = subprocess.run(
            ["grep", "-rl", "--include=*.md", query, str(search_root)],
            capture_output=True, text=True, timeout=5,
        )
        matches = result.stdout.strip().splitlines()
        if not matches:
            return f"No notes found containing '{query}'"
        rel = [str(pathlib.Path(m).relative_to(VAULT_PATH)) for m in matches[:20]]
        return f"Found {len(matches)} note(s) containing '{query}':\n" + "\n".join(rel)
    except subprocess.TimeoutExpired:
        return "Search timed out — try a more specific query"


def _get_sprint_state() -> str:
    today = date.today().strftime("%Y-%m-%d")
    parts = []
    for path in [
        f"20 Areas/Personal/Daily Notes/{today}.md",
        "20 Areas/Personal/Sprints.md",
        "20 Areas/Personal/TASKS.md",
    ]:
        try:
            content = (VAULT_PATH / path).read_text(encoding="utf-8", errors="replace")
            parts.append(f"=== {path} ===\n{content[:2000]}")
        except FileNotFoundError:
            parts.append(f"=== {path} === [not found]")
    return "\n\n".join(parts)


def _append_to_note(path: str, text: str) -> str:
    target = VAULT_PATH / path
    if not target.exists():
        return f"Not found: {path}. Use search_vault to find the correct path first."
    with open(target, "a", encoding="utf-8") as f:
        f.write(f"\n{text.rstrip()}\n")
    return f"Appended to {path}"


def vault_available() -> bool:
    return (VAULT_PATH / "CLAUDE.md").exists()
