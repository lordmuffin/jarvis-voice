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
        # Snapshot existing messages so we can append to them after
        existing_messages: list[dict] = list(task.get("messages", []))

    try:
        # Build context: use stored messages if this is a continuation, else seed from prompt
        if existing_messages:
            send_messages = existing_messages
        else:
            send_messages = []
            if task.get("system"):
                send_messages.append({"role": "system", "content": task["system"]})
            send_messages.append({"role": "user", "content": task["prompt"]})

        body = _json.dumps({
            "model": task["model"],
            "messages": send_messages,
            "max_tokens": 4096,
        }).encode()

        api_key = os.environ.get("LITELLM_API_KEY", "litellm")
        req = urllib.request.Request(
            f"{_LITELLM_BASE}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = _json.loads(resp.read().decode())

        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})

        with _TASKS_LOCK:
            task = _TASKS.get(task_id, {})
            if task.get("status") != "cancelled":
                task["status"]      = "done"
                task["output"]      = content
                task["finished_at"] = _time.time()
                task["tokens"]      = task.get("tokens", 0) + usage.get("total_tokens", 0)
                # Persist full conversation: seed + assistant reply
                if not existing_messages:
                    seed: list[dict] = []
                    if task.get("system"):
                        seed.append({"role": "system", "content": task["system"]})
                    seed.append({"role": "user", "content": task["prompt"]})
                    task["messages"] = seed
                task["messages"].append({"role": "assistant", "content": content})
            _tasks_save()

    except Exception as exc:
        with _TASKS_LOCK:
            task = _TASKS.get(task_id, {})
            if task.get("status") != "cancelled":
                task["status"]      = "failed"
                task["output"]      = str(exc)
                task["finished_at"] = _time.time()
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
