"""FastAPI capture endpoints.

Two ways to land a voice note in the vault:

- ``POST /api/v1/capture/voice`` — JSON payload with a pre-transcribed
  ``transcript`` (client-side STT).
- ``POST /api/v1/capture/audio`` — multipart upload of a raw audio file;
  the server runs faster-whisper and writes the transcript.

Both paths share auth, frontmatter, and vault output via ``write_voice_note``.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import logging
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

import json as _json
import threading as _threading
import time as _time

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis_voice import transcribe as transcribe_mod
from jarvis_voice.vault import VAULT_INBOX, voice_note_basename, write_voice_note

log = logging.getLogger(__name__)

# Default API key — matches DEFAULT_VAULT_KEY in VoiceChatViewModel.kt.
# Override with JARVIS_CAPTURE_KEY env var to rotate the key on both sides.
_DEFAULT_API_KEY = "0WBpWVdLsieaJPpTI7JEjKBZZMd2G-9WWZM2Iiq_wMo"

# Vault root — two levels up from the Voice Notes inbox subfolder.
# Override with VAULT_ROOT env var if the directory layout differs.
VAULT_ROOT = pathlib.Path(
    os.environ.get("VAULT_ROOT", str(pathlib.Path(VAULT_INBOX).parents[1]))
)
_VAULT_MAX_CHARS = 6000

app = FastAPI(title="Jarvis Capture API", version="1.0")


def _transcode_to_mp3(src_path: str, dst_path: str) -> bool:
    """Transcode *src_path* to MP3 at *dst_path*. Returns True on success."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-codec:a", "libmp3lame", "-qscale:a", "4", dst_path,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except FileNotFoundError as err:
        log.warning("mp3 transcode failed for %s: ffmpeg not installed (%s)", src_path, err)
        return False
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        tail = stderr[-1] if stderr else ""
        log.warning("mp3 transcode failed for %s: %s", src_path, tail)
        return False

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/manifest.json", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(_STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/capture", include_in_schema=False)
def capture_page() -> HTMLResponse:
    page = (_STATIC_DIR / "capture.html").read_text(encoding="utf-8")
    key = os.environ.get("JARVIS_CAPTURE_KEY", "")
    page = page.replace("__JARVIS_KEY__", _html.escape(key, quote=True))
    return HTMLResponse(page)


class VoiceCapture(BaseModel):
    transcript: str
    timestamp: str
    device: str
    duration_seconds: int | None = None
    model: str | None = None
    confidence: float | None = None
    intent_hint: str | None = None


def verify_key(x_jarvis_key: str = Header(...)) -> str:
    expected = os.environ.get("JARVIS_CAPTURE_KEY", _DEFAULT_API_KEY)
    if x_jarvis_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_jarvis_key


def _parse_timestamp(value: str | None) -> _dt.datetime:
    if not value:
        return _dt.datetime.now()
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _dt.datetime.now()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/capture/voice", response_model=None)
def capture_voice(payload: VoiceCapture, _: str = Depends(verify_key)) -> dict[str, str]:
    ts = _parse_timestamp(payload.timestamp)

    extra = {
        "intent_hint": payload.intent_hint,
        "confidence": payload.confidence,
    }
    extra = {k: v for k, v in extra.items() if v is not None}

    path = write_voice_note(
        transcript=payload.transcript,
        duration_seconds=payload.duration_seconds or 0,
        source=payload.device,
        timestamp=ts,
        model=payload.model,
        extra_frontmatter=extra or None,
    )
    return {
        "id": str(uuid.uuid4()),
        "routed_to": f"00 Inbox/Voice Notes/{os.path.basename(path)}",
        "status": "received",
    }


@app.post("/api/v1/capture/audio", response_model=None)
def capture_audio(
    file: UploadFile = File(...),
    device: str = Form(...),
    timestamp: str | None = Form(None),
    intent_hint: str | None = Form(None),
    confidence: float | None = Form(None),
    _: str = Depends(verify_key),
) -> dict[str, object]:
    ts = _parse_timestamp(timestamp)

    suffix = pathlib.Path(file.filename or "").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        shutil.copyfileobj(file.file, tmp)

    try:
        try:
            transcript, duration, transcribe_seconds = transcribe_mod.transcribe(tmp_path)
        except transcribe_mod.TranscribeError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

        if not transcript:
            raise HTTPException(status_code=422, detail="No speech detected in audio")

        extra = {
            "intent_hint": intent_hint,
            "confidence": confidence,
        }
        extra = {k: v for k, v in extra.items() if v is not None}

        stem = voice_note_basename(ts, device)
        os.makedirs(VAULT_INBOX, exist_ok=True)
        mp3_target = os.path.join(VAULT_INBOX, f"{stem}.mp3")
        audio_file = f"{stem}.mp3" if _transcode_to_mp3(tmp_path, mp3_target) else None

        path = write_voice_note(
            transcript=transcript,
            duration_seconds=duration,
            source=device,
            timestamp=ts,
            model=f"whisper-{transcribe_mod.WHISPER_MODEL}",
            extra_frontmatter=extra or None,
            audio_file=audio_file,
            transcribe_seconds=transcribe_seconds,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

    return {
        "id": str(uuid.uuid4()),
        "routed_to": f"00 Inbox/Voice Notes/{os.path.basename(path)}",
        "status": "received",
        "transcript": transcript,
        "transcribe_seconds": round(float(transcribe_seconds), 1),
    }


# ── Vault tool endpoints (used by Android Kai voice tool calls) ───────────────

def _strip_html(html_text: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = _html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


class AppendPayload(BaseModel):
    path: str
    text: str


class WritePayload(BaseModel):
    path: str
    content: str


class ExecPayload(BaseModel):
    command: str
    working_dir: str = "/home/lordmuffin"


@app.get("/api/v1/vault/note")
def vault_read_note(path: str, _: str = Depends(verify_key)) -> dict:
    """Read a note from the vault by relative path."""
    target = VAULT_ROOT / path
    if not target.exists():
        name = pathlib.Path(path).name
        matches = list(VAULT_ROOT.rglob(f"*{name}"))
        if matches:
            target = matches[0]
        else:
            raise HTTPException(status_code=404, detail=f"Not found: {path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    if len(content) > _VAULT_MAX_CHARS:
        content = content[:_VAULT_MAX_CHARS] + "\n\n[truncated]"
    return {"path": str(target.relative_to(VAULT_ROOT)), "content": content}


@app.get("/api/v1/vault/search")
def vault_search(
    query: str,
    directory: str = "",
    _: str = Depends(verify_key),
) -> dict:
    """Search vault notes for a keyword. Returns list of relative paths."""
    search_root = VAULT_ROOT / directory if directory else VAULT_ROOT
    try:
        result = subprocess.run(
            ["grep", "-rl", "--include=*.md", query, str(search_root)],
            capture_output=True, text=True, timeout=5,
        )
        matches = result.stdout.strip().splitlines()
        rel = [str(pathlib.Path(m).relative_to(VAULT_ROOT)) for m in matches[:20]]
        return {"query": query, "count": len(matches), "matches": rel}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")


@app.get("/api/v1/vault/sprint-state")
def vault_sprint_state(_: str = Depends(verify_key)) -> dict:
    """Return today's daily note, sprint board, and TASKS.md concatenated."""
    import datetime as dt
    today = dt.date.today().strftime("%Y-%m-%d")
    paths = [
        f"20 Areas/Personal/Daily Notes/{today}.md",
        "20 Areas/Personal/Sprints.md",
        "20 Areas/Personal/TASKS.md",
    ]
    parts: list[dict] = []
    for p in paths:
        try:
            content = (VAULT_ROOT / p).read_text(encoding="utf-8", errors="replace")
            parts.append({"path": p, "content": content[:2000]})
        except FileNotFoundError:
            parts.append({"path": p, "content": "[not found]"})
    return {"date": today, "sections": parts}


@app.post("/api/v1/vault/note/append")
def vault_append_note(payload: AppendPayload, _: str = Depends(verify_key)) -> dict:
    """Append text to an existing vault note."""
    target = VAULT_ROOT / payload.path
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {payload.path}")
    with open(target, "a", encoding="utf-8") as f:
        f.write(f"\n{payload.text.rstrip()}\n")
    return {"status": "appended", "path": payload.path}


@app.post("/api/v1/vault/note/write")
def vault_write_note(payload: WritePayload, _: str = Depends(verify_key)) -> dict:
    """Create or overwrite a vault note. Creates parent directories as needed."""
    target = VAULT_ROOT / payload.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    log.info("vault_write_note: wrote %d chars to %s", len(payload.content), payload.path)
    return {"status": "written", "path": payload.path}


@app.get("/api/v1/web/fetch")
def web_fetch(url: str, _: str = Depends(verify_key)) -> dict:
    """Fetch a URL and return its text content (HTML stripped) for research."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Kai/1.0; +https://jarvis.apj.dev)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(1_000_000).decode("utf-8", errors="replace")
        text = _strip_html(raw) if "html" in content_type.lower() else raw
        return {"url": url, "content": text[:6000]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc


@app.post("/api/v1/system/exec")
def system_exec(payload: ExecPayload, _: str = Depends(verify_key)) -> dict:
    """Run a shell command on the homelab server and return its output."""
    try:
        result = subprocess.run(
            payload.command,
            shell=True,
            executable="/bin/bash",
            cwd=payload.working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:1000],
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Command timed out after 30s")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Push notification scheduler ───────────────────────────────────────────────

_NOTIFS: dict[str, dict] = {}
_NOTIFS_LOCK = _threading.Lock()
_NOTIFS_FILE = pathlib.Path("/tmp/jarvis_notifications.json")


def _notifs_load() -> None:
    if _NOTIFS_FILE.exists():
        try:
            _NOTIFS.update(_json.loads(_NOTIFS_FILE.read_text()))
        except Exception:
            pass


def _notifs_save() -> None:
    try:
        _NOTIFS_FILE.write_text(_json.dumps(_NOTIFS))
    except Exception:
        pass


_notifs_load()


class NotifSchedulePayload(BaseModel):
    title: str
    body: str = ""
    delay_minutes: float


@app.post("/api/v1/notify/schedule")
def notify_schedule(payload: NotifSchedulePayload, _: str = Depends(verify_key)) -> dict:
    """Schedule a push notification to fire after delay_minutes."""
    nid = str(uuid.uuid4())
    fires_at = _time.time() + payload.delay_minutes * 60
    with _NOTIFS_LOCK:
        _NOTIFS[nid] = {
            "id":         nid,
            "title":      payload.title,
            "body":       payload.body,
            "fires_at":   fires_at,
            "delivered":  False,
            "created_at": _time.time(),
        }
        _notifs_save()
    return {"id": nid, "fires_at": fires_at}


@app.get("/api/v1/notify/pending")
def notify_pending(_: str = Depends(verify_key)) -> dict:
    """Return all notifications due now; mark them delivered."""
    now = _time.time()
    due: list[dict] = []
    with _NOTIFS_LOCK:
        for n in list(_NOTIFS.values()):
            if not n["delivered"] and n["fires_at"] <= now:
                n["delivered"] = True
                due.append(dict(n))
        if due:
            _notifs_save()
    return {"notifications": due}


@app.delete("/api/v1/notify/{notif_id}")
def notify_cancel(notif_id: str, _: str = Depends(verify_key)) -> dict:
    """Cancel a scheduled notification."""
    with _NOTIFS_LOCK:
        n = _NOTIFS.pop(notif_id, None)
        if n:
            _notifs_save()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"id": notif_id, "status": "cancelled"}


@app.get("/api/v1/notify")
def notify_list(_: str = Depends(verify_key)) -> dict:
    """List all pending (undelivered) scheduled notifications."""
    with _NOTIFS_LOCK:
        pending = [n for n in _NOTIFS.values() if not n["delivered"]]
    return {"notifications": sorted(pending, key=lambda n: n["fires_at"])}


# ── Agent task queue ───────────────────────────────────────────────────────────

_LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://192.168.1.93:4000")
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = _threading.Lock()
_TASKS_FILE = pathlib.Path("/tmp/jarvis_agent_tasks.json")


def _tasks_load() -> None:
    if _TASKS_FILE.exists():
        try:
            data = _json.loads(_TASKS_FILE.read_text())
            _TASKS.update(data)
        except Exception:
            pass


def _tasks_save() -> None:
    try:
        _TASKS_FILE.write_text(_json.dumps(_TASKS))
    except Exception:
        pass


_tasks_load()


class AgentTaskCreate(BaseModel):
    name: str = ""
    prompt: str
    model: str = "local-default"
    system: str = ""


_AGENT_SYSTEM = (
    "/no_think\n\n"
    "You are Kai, an AI assistant running on the Jarvis homelab server (192.168.1.155). "
    "You have tools available — use them to complete tasks autonomously end-to-end. "
    "Do NOT say you lack access to external systems; use the tools provided.\n\n"
    "Git workspace tools (clone repos, edit files, commit, open PRs):\n"
    "  git_clone   — clone a GitHub repo by slug (e.g. lordmuffin/jarvis-voice)\n"
    "  git_write   — write/create a file in a cloned workspace\n"
    "  git_status  — check working tree status\n"
    "  git_commit  — stage all changes and commit (optionally on a new branch)\n"
    "  git_pr      — push branch and create a pull request\n\n"
    "Vault tools (Obsidian knowledge base at /home/lordmuffin/Notes):\n"
    "  vault_read   — read a note by vault-relative path\n"
    "  vault_search — keyword search across all notes\n"
    "  vault_append — append text to an existing note\n"
    "  vault_write  — create or overwrite a note\n\n"
    "Web + system:\n"
    "  web_fetch    — fetch a URL and return stripped text\n"
    "  system_exec  — run a shell command on the server\n\n"
    "COMPLETION RULE: You MUST call task_done when the work is fully finished. "
    "Do NOT stop calling tools and return a text answer — that signals incomplete work. "
    "Every task ends with task_done(summary='...', pr_url='...'). "
    "If you hit a blocker you cannot resolve, call task_done with what was done and what is blocked."
)

_AGENT_TOOLS: list[dict] = [
    {"type": "function", "function": {
        "name": "git_clone",
        "description": "Clone (or update) a GitHub repo into a local workspace. Must be done before git_write/git_commit/git_pr.",
        "parameters": {"type": "object", "required": ["repo"], "properties": {
            "repo":   {"type": "string", "description": "GitHub slug, e.g. lordmuffin/jarvis-voice"},
            "branch": {"type": "string", "description": "Branch to checkout after clone (optional)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "git_write",
        "description": "Write or create a file in a cloned workspace repo.",
        "parameters": {"type": "object", "required": ["repo", "path", "content"], "properties": {
            "repo":    {"type": "string", "description": "GitHub slug"},
            "path":    {"type": "string", "description": "File path relative to repo root"},
            "content": {"type": "string", "description": "Full file content"},
        }},
    }},
    {"type": "function", "function": {
        "name": "git_status",
        "description": "Return git status and diff stat for a workspace repo.",
        "parameters": {"type": "object", "required": ["repo"], "properties": {
            "repo": {"type": "string", "description": "GitHub slug"},
        }},
    }},
    {"type": "function", "function": {
        "name": "git_commit",
        "description": "Stage all changes and commit. Optionally create a new branch first.",
        "parameters": {"type": "object", "required": ["repo", "message"], "properties": {
            "repo":    {"type": "string", "description": "GitHub slug"},
            "message": {"type": "string", "description": "Commit message"},
            "branch":  {"type": "string", "description": "Create/switch to this branch before committing (optional)"},
            "push":    {"type": "boolean", "description": "Push to origin after commit (default true)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "git_pr",
        "description": "Push current branch and open a pull request. Returns PR URL or compare URL.",
        "parameters": {"type": "object", "required": ["repo", "title"], "properties": {
            "repo":  {"type": "string", "description": "GitHub slug"},
            "title": {"type": "string", "description": "PR title"},
            "body":  {"type": "string", "description": "PR description (markdown)"},
            "base":  {"type": "string", "description": "Target branch (default: main)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "vault_read",
        "description": "Read a note from the Obsidian vault by vault-relative path.",
        "parameters": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string", "description": "Vault-relative path, e.g. 20 Areas/Personal/Sprints.md"},
        }},
    }},
    {"type": "function", "function": {
        "name": "vault_search",
        "description": "Keyword search across all vault notes. Returns list of matching file paths.",
        "parameters": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Search keyword or phrase"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        }},
    }},
    {"type": "function", "function": {
        "name": "vault_append",
        "description": "Append text to an existing vault note.",
        "parameters": {"type": "object", "required": ["path", "content"], "properties": {
            "path":    {"type": "string", "description": "Vault-relative path"},
            "content": {"type": "string", "description": "Text to append"},
        }},
    }},
    {"type": "function", "function": {
        "name": "vault_write",
        "description": "Create or overwrite a vault note.",
        "parameters": {"type": "object", "required": ["path", "content"], "properties": {
            "path":    {"type": "string", "description": "Vault-relative path"},
            "content": {"type": "string", "description": "Full note content"},
        }},
    }},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch a URL and return stripped plain text.",
        "parameters": {"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
        }},
    }},
    {"type": "function", "function": {
        "name": "system_exec",
        "description": "Run a shell command on the Jarvis server. Returns stdout+stderr (max 2000 chars).",
        "parameters": {"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string", "description": "Shell command"},
        }},
    }},
    {"type": "function", "function": {
        "name": "task_done",
        "description": "Signal that all work is complete. MUST be called at the end of every task. Do not return a text answer without calling this first.",
        "parameters": {"type": "object", "required": ["summary"], "properties": {
            "summary": {"type": "string", "description": "What was accomplished, including any commit hashes, PR URLs, or file paths."},
            "pr_url":  {"type": "string", "description": "Pull request URL if a PR was created (optional)"},
            "blocked": {"type": "string", "description": "If a blocker was hit, describe it here (optional)"},
        }},
    }},
]


def _run_agent_task(task_id: str) -> None:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        if task["status"] == "cancelled":
            return
        task["status"] = "running"
        task["started_at"] = _time.time()
        _tasks_save()
        existing_messages: list[dict] = list(task.get("messages", []))

    try:
        # Seed messages for first run; use stored messages for continuations
        if existing_messages:
            send_messages = list(existing_messages)
        else:
            system_content = task.get("system") or _AGENT_SYSTEM
            send_messages = [
                {"role": "system", "content": system_content},
                {"role": "user",   "content": task["prompt"]},
            ]

        # Ensure a system message is present (continuations may lack one)
        if not send_messages or send_messages[0]["role"] != "system":
            send_messages = [{"role": "system", "content": _AGENT_SYSTEM}] + send_messages

        api_key = os.environ.get("LITELLM_API_KEY", "litellm")
        content = ""
        total_tokens = 0
        new_messages: list[dict] = []
        _MAX_ITERATIONS = 25
        _LLM_TIMEOUT = 300  # 5 min — qwen-coder-32b at 8192 tokens can take 3-4 min

        for _iteration in range(_MAX_ITERATIONS):
            body = _json.dumps({
                "model":       task["model"],
                "messages":    send_messages,
                "tools":       _AGENT_TOOLS,
                "tool_choice": "auto",
                "max_tokens":  8192,
            }).encode()

            req = urllib.request.Request(
                f"{_LITELLM_BASE}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT) as resp:
                    result = _json.loads(resp.read().decode())
            except Exception as llm_exc:
                # LLM call failed — surface what was done so far instead of a bare error
                partial = content or (
                    new_messages[-1].get("content", "") if new_messages else ""
                )
                raise RuntimeError(
                    f"LLM call failed on iteration {_iteration + 1}/{_MAX_ITERATIONS}: "
                    f"{llm_exc}."
                    + (f"\n\nWork completed before failure:\n{partial[:1000]}" if partial else "")
                ) from llm_exc

            total_tokens += result.get("usage", {}).get("total_tokens", 0)
            choice  = result["choices"][0]
            message = choice["message"]

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                send_messages.append(message)
                new_messages.append(message)
                done_called = False
                for tc in tool_calls:
                    fn   = tc["function"]["name"]
                    try:
                        args = _json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = {}

                    if fn == "task_done":
                        # Model signalled completion — use summary as output
                        content = args.get("summary", "Task complete.")
                        if args.get("pr_url"):
                            content += f"\n\nPR: {args['pr_url']}"
                        if args.get("blocked"):
                            content += f"\n\nBlocked: {args['blocked']}"
                        tool_msg = {
                            "role":         "tool",
                            "tool_call_id": tc["id"],
                            "content":      "acknowledged",
                        }
                        send_messages.append(tool_msg)
                        new_messages.append(tool_msg)
                        new_messages.append({"role": "assistant", "content": content})
                        done_called = True
                    else:
                        result_text = _execute_tool(fn, args)
                        tool_msg = {
                            "role":         "tool",
                            "tool_call_id": tc["id"],
                            "content":      result_text,
                        }
                        send_messages.append(tool_msg)
                        new_messages.append(tool_msg)

                if done_called:
                    break
            else:
                # Model returned text without calling task_done — could be mid-thought
                text = message.get("content") or ""
                text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()

                if _iteration < _MAX_ITERATIONS - 1:
                    # Push the response back and prompt continuation
                    send_messages.append({"role": "assistant", "content": text})
                    send_messages.append({
                        "role":    "user",
                        "content": "Continue the task. Call task_done when fully complete.",
                    })
                    new_messages.append({"role": "assistant", "content": text})
                else:
                    # Final iteration — accept whatever we have
                    content = text
                    new_messages.append({"role": "assistant", "content": content})
                    break

        with _TASKS_LOCK:
            task = _TASKS.get(task_id, {})
            if task.get("status") != "cancelled":
                task["status"]      = "done"
                task["output"]      = content
                task["finished_at"] = _time.time()
                task["tokens"]      = task.get("tokens", 0) + total_tokens
                if not existing_messages:
                    task["messages"] = send_messages[:2]  # system + user seed
                task["messages"].extend(new_messages)
            _tasks_save()

    except Exception as exc:
        with _TASKS_LOCK:
            task = _TASKS.get(task_id, {})
            if task.get("status") != "cancelled":
                task["status"]      = "failed"
                task["output"]      = str(exc)
                task["finished_at"] = _time.time()
                task["tokens"]      = task.get("tokens", 0) + total_tokens
                # Preserve any conversation history accumulated before the failure
                if new_messages:
                    if not existing_messages:
                        task["messages"] = send_messages[:2]
                    task["messages"].extend(new_messages)
            _tasks_save()


@app.post("/api/v1/agent/tasks")
def agent_task_create(payload: AgentTaskCreate, _: str = Depends(verify_key)) -> dict:
    """Submit a prompt as a background agent task and return its id immediately."""
    task_id = str(uuid.uuid4())
    name = payload.name.strip() or payload.prompt[:40].strip()
    task: dict = {
        "id":          task_id,
        "name":        name,
        "prompt":      payload.prompt,
        "model":       payload.model,
        "system":      payload.system,
        "status":      "queued",
        "output":      "",
        "tokens":      0,
        "messages":    [],
        "created_at":  _time.time(),
        "started_at":  None,
        "finished_at": None,
    }
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _tasks_save()

    _threading.Thread(target=_run_agent_task, args=(task_id,), daemon=True).start()
    return {"id": task_id, "status": "queued"}


@app.get("/api/v1/agent/tasks")
def agent_task_list(_: str = Depends(verify_key)) -> dict:
    """List all agent tasks, newest first (max 100)."""
    with _TASKS_LOCK:
        tasks = sorted(_TASKS.values(), key=lambda t: t["created_at"], reverse=True)
    return {"tasks": tasks[:100]}


@app.get("/api/v1/agent/tasks/{task_id}")
def agent_task_get(task_id: str, _: str = Depends(verify_key)) -> dict:
    """Get a single agent task by id."""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/api/v1/agent/tasks/{task_id}")
def agent_task_delete(task_id: str, _: str = Depends(verify_key)) -> dict:
    """Cancel and remove an agent task."""
    with _TASKS_LOCK:
        task = _TASKS.pop(task_id, None)
        if task:
            task["status"] = "cancelled"
            _tasks_save()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"id": task_id, "status": "cancelled"}


class AgentTaskReply(BaseModel):
    message: str


@app.post("/api/v1/agent/tasks/{task_id}/reply")
def agent_task_reply(task_id: str, payload: AgentTaskReply, _: str = Depends(verify_key)) -> dict:
    """Append a user message to a completed task and continue the conversation."""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail="Task is still running")
        # Backfill messages for tasks created before v1.2.6
        if "messages" not in task:
            task["messages"] = []
            if task.get("prompt"):
                task["messages"].append({"role": "user", "content": task["prompt"]})
            if task.get("output"):
                task["messages"].append({"role": "assistant", "content": task["output"]})
        # Append user turn; clear output so the next response becomes the new output
        task["messages"].append({"role": "user", "content": payload.message})
        task["status"]      = "queued"
        task["output"]      = ""
        task["started_at"]  = None
        task["finished_at"] = None
        _tasks_save()

    _threading.Thread(target=_run_agent_task, args=(task_id,), daemon=True).start()
    return {"id": task_id, "status": "queued"}


# ── Git workspace endpoints ────────────────────────────────────────────────────

_WORKSPACES_DIR = pathlib.Path(os.environ.get("JARVIS_WORKSPACES", "/home/lordmuffin/jarvis-workspaces"))

# Ensure ~/.local/bin is on PATH so gh CLI installed as user-local binary is found
_LOCAL_BIN = str(pathlib.Path.home() / ".local" / "bin")
if _LOCAL_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _LOCAL_BIN + ":" + os.environ.get("PATH", "")


def _ws_path(repo_slug: str) -> pathlib.Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", repo_slug)
    return _WORKSPACES_DIR / safe


_GITHUB_SSH_CONFIG = pathlib.Path.home() / ".config" / "jarvis" / "github-ssh.config"
_GIT_SSH_CMD = f"ssh -F {_GITHUB_SSH_CONFIG}" if _GITHUB_SSH_CONFIG.exists() else None


def _run(cmd: list[str], cwd: pathlib.Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    env = None
    if _GIT_SSH_CMD and cmd and cmd[0] == "git":
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = _GIT_SSH_CMD
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


class GitClonePayload(BaseModel):
    repo: str          # e.g. "lordmuffin/jarvis-voice" or full URL
    branch: str = ""


class GitWritePayload(BaseModel):
    repo: str
    path: str          # relative path inside repo
    content: str


class GitCommitPayload(BaseModel):
    repo: str
    message: str
    push: bool = True
    branch: str = ""   # creates branch if non-empty and doesn't exist


class GitPRPayload(BaseModel):
    repo: str
    title: str
    body: str = ""
    base: str = "main"


@app.post("/api/v1/git/clone")
def git_clone(payload: GitClonePayload, _: str = Depends(verify_key)) -> dict:
    """Clone a GitHub repo into the local workspace directory."""
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    slug = payload.repo.rstrip("/")
    ws = _ws_path(slug.replace("/", "__"))

    # Prefer SSH (uses the homelab deploy key via GIT_SSH_COMMAND).
    # Fall back to HTTPS only if a full HTTPS URL is explicitly passed.
    if slug.startswith("http"):
        url = slug
    elif slug.startswith("git@"):
        url = slug
    else:
        url = f"git@github.com:{slug}.git"

    if ws.exists():
        # Already cloned — just fetch
        rc, out, err = _run(["git", "fetch", "--all"], cwd=ws)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"git fetch failed: {err}")
        if payload.branch:
            rc, out, err = _run(["git", "checkout", payload.branch], cwd=ws)
            if rc != 0:
                _run(["git", "checkout", "-b", payload.branch], cwd=ws)
        return {"status": "updated", "path": str(ws)}

    cmd = ["git", "clone", url, str(ws)]
    if payload.branch:
        cmd += ["--branch", payload.branch]
    rc, out, err = _run(cmd, timeout=120)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"git clone failed: {err}")
    return {"status": "cloned", "path": str(ws)}


@app.post("/api/v1/git/write")
def git_write(payload: GitWritePayload, _: str = Depends(verify_key)) -> dict:
    """Write (create or overwrite) a file in a local workspace."""
    ws = _ws_path(payload.repo.replace("/", "__"))
    if not ws.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for {payload.repo} — clone first")
    target = (ws / payload.path).resolve()
    if not str(target).startswith(str(ws)):
        raise HTTPException(status_code=400, detail="Path traversal rejected")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    return {"status": "written", "path": payload.path}


@app.get("/api/v1/git/status")
def git_status(repo: str, _: str = Depends(verify_key)) -> dict:
    """Return git status and a short diff for a local workspace."""
    ws = _ws_path(repo.replace("/", "__"))
    if not ws.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for {repo}")
    _, status_out, _ = _run(["git", "status", "--short"], cwd=ws)
    _, diff_out, _   = _run(["git", "diff", "--stat", "HEAD"], cwd=ws)
    _, branch_out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
    return {
        "branch": branch_out.strip(),
        "status": status_out.strip(),
        "diff_stat": diff_out.strip(),
    }


@app.post("/api/v1/git/commit")
def git_commit(payload: GitCommitPayload, _: str = Depends(verify_key)) -> dict:
    """Stage all changes, commit, and optionally push."""
    ws = _ws_path(payload.repo.replace("/", "__"))
    if not ws.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for {payload.repo}")

    if payload.branch:
        rc, _, err = _run(["git", "checkout", "-b", payload.branch], cwd=ws)
        if rc != 0:
            # Branch may already exist
            _run(["git", "checkout", payload.branch], cwd=ws)

    _run(["git", "add", "-A"], cwd=ws)
    rc, out, err = _run(["git", "commit", "-m", payload.message], cwd=ws)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"git commit failed: {err or out}")

    sha = out.strip().splitlines()[0] if out else ""

    push_result = ""
    if payload.push:
        branch_name = payload.branch or ""
        if not branch_name:
            _, bn, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
            branch_name = bn.strip()
        rc2, out2, err2 = _run(["git", "push", "-u", "origin", branch_name], cwd=ws)
        push_result = "pushed" if rc2 == 0 else f"push failed: {err2}"

    return {"status": "committed", "commit": sha, "push": push_result}


@app.post("/api/v1/git/pr")
def git_pr(payload: GitPRPayload, _: str = Depends(verify_key)) -> dict:
    """Create a GitHub pull request using the gh CLI."""
    ws = _ws_path(payload.repo.replace("/", "__"))
    if not ws.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found for {payload.repo}")

    cmd = [
        "gh", "pr", "create",
        "--title", payload.title,
        "--body",  payload.body or "",
        "--base",  payload.base,
    ]
    rc, out, err = _run(cmd, cwd=ws, timeout=60)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"gh pr create failed: {err or out}")

    pr_url = out.strip().splitlines()[-1] if out else ""
    return {"status": "created", "url": pr_url}


# ── Agent tool executor ────────────────────────────────────────────────────────
# Called by _run_agent_task() when the LLM emits a tool_call.  Defined here so
# it has access to _run / _ws_path / VAULT_ROOT which are declared earlier.

def _execute_tool(name: str, args: dict) -> str:
    try:
        if name == "git_clone":
            _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
            slug = args["repo"].strip("/")
            ws   = _ws_path(slug.replace("/", "__"))
            url  = f"git@github.com:{slug}.git" if not slug.startswith(("http", "git@")) else slug
            if ws.exists():
                rc, _, err = _run(["git", "fetch", "--all"], cwd=ws)
                if args.get("branch"):
                    _run(["git", "checkout", args["branch"]], cwd=ws)
                return f"Updated workspace at {ws}"
            cmd = ["git", "clone", url, str(ws)]
            if args.get("branch"):
                cmd += ["--branch", args["branch"]]
            rc, out, err = _run(cmd, timeout=120)
            if rc != 0:
                return f"clone error: {err.strip()}"
            return f"Cloned {slug} to {ws}"

        elif name == "git_write":
            ws     = _ws_path(args["repo"].replace("/", "__"))
            target = (ws / args["path"]).resolve()
            if not str(target).startswith(str(ws)):
                return "Path traversal rejected"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"], encoding="utf-8")
            return f"Wrote {args['path']}"

        elif name == "git_status":
            ws = _ws_path(args["repo"].replace("/", "__"))
            _, status, _ = _run(["git", "status", "--short"], cwd=ws)
            _, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
            return f"branch: {branch.strip()}\n{status.strip() or '(clean)'}"

        elif name == "git_commit":
            ws = _ws_path(args["repo"].replace("/", "__"))
            if args.get("branch"):
                rc, _, err = _run(["git", "checkout", "-b", args["branch"]], cwd=ws)
                if rc != 0:
                    _run(["git", "checkout", args["branch"]], cwd=ws)
            _run(["git", "add", "-A"], cwd=ws)
            rc, out, err = _run(["git", "commit", "-m", args["message"]], cwd=ws)
            if rc != 0:
                return f"commit error: {(err or out).strip()}"
            push = args.get("push", True)
            if push:
                _, bn, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
                _run(["git", "push", "-u", "origin", bn.strip()], cwd=ws)
            return f"Committed: {out.strip().splitlines()[0] if out else 'ok'}"

        elif name == "git_pr":
            ws    = _ws_path(args["repo"].replace("/", "__"))
            _, bn, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
            branch = bn.strip()
            _run(["git", "push", "-u", "origin", branch], cwd=ws)
            cmd = ["gh", "pr", "create",
                   "--title", args["title"],
                   "--base",  args.get("base", "main"),
                   "--body",  args.get("body", "")]
            rc, out, err = _run(cmd, cwd=ws, timeout=60)
            if rc != 0:
                slug = args["repo"].strip("/")
                return f"Pushed branch {branch}. Open PR: https://github.com/{slug}/compare/{branch}"
            return out.strip().splitlines()[-1] if out else "PR created"

        elif name == "vault_read":
            path = VAULT_ROOT / args["path"]
            if not path.exists():
                return f"Not found: {args['path']}"
            return path.read_text(encoding="utf-8", errors="replace")[:_VAULT_MAX_CHARS]

        elif name == "vault_search":
            query = args["query"].lower()
            limit = int(args.get("limit", 10))
            hits: list[str] = []
            for f in VAULT_ROOT.rglob("*.md"):
                try:
                    if query in f.read_text(encoding="utf-8", errors="replace").lower():
                        hits.append(str(f.relative_to(VAULT_ROOT)))
                        if len(hits) >= limit:
                            break
                except Exception:
                    pass
            return _json.dumps(hits) if hits else "No results"

        elif name == "vault_append":
            path = VAULT_ROOT / args["path"]
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n" + args["content"])
            return f"Appended to {args['path']}"

        elif name == "vault_write":
            path = VAULT_ROOT / args["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return f"Wrote {args['path']}"

        elif name == "web_fetch":
            req2 = urllib.request.Request(
                args["url"], headers={"User-Agent": "JarvisAgent/1.0"}
            )
            with urllib.request.urlopen(req2, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")
            return _strip_html(raw)[:_VAULT_MAX_CHARS]

        elif name == "system_exec":
            r = subprocess.run(
                args["command"], shell=True, capture_output=True, text=True, timeout=30
            )
            out = (r.stdout or "") + (r.stderr or "")
            return out[:2000] or f"(exit {r.returncode})"

        elif name == "task_done":
            return "acknowledged"  # handled in the loop; shouldn't reach here

        else:
            return f"Unknown tool: {name}"

    except Exception as exc:
        return f"Tool error ({name}): {exc}"
