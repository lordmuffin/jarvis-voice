from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_LIVE_")

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis_live"
    # Apply Alembic migrations at startup. Off by default; `jarvis-live migrate` does the same
    # by hand. Run with a single replica: concurrent upgrades are not serialised.
    auto_migrate: bool = False
    log_level: str = "info"
    # Release channel this instance runs as: "live" (the stable instance) or e.g. "lab"
    # (the unstable one fed from main; server/deploy/README.md). Anything but "live" is
    # marked as such: in /healthz, the dashboard header and Gotify titles.
    environment: str = "live"
    data_dir: Path = Path("/data")
    # The family's shared apps list (JSON), behind the dashboard's ▦ switcher. Read once at
    # startup; unset or unreadable means no switcher. See jarvis_live/apps.py.
    apps_list_path: Path | None = None

    # Ingest
    ack_interval_ms: int = 250  # protocol requires an ack at least every 500 ms
    fsync_interval_s: float = 1.0
    status_interval_s: float = 5.0
    # Uploaded recordings (POST /v1/sessions/upload), decoded with ffmpeg.
    upload_max_mb: int = 500
    ffmpeg_path: str = "ffmpeg"

    # Web dashboard: built assets served at `/`. Unset: ./web/dist (the image), then the
    # repo's web/dist. No index.html there means the dashboard is simply not served.
    web_dist_dir: Path | None = None

    # Segmenter
    vad_aggressiveness: int = 2
    vad_frame_ms: int = 30
    segment_close_silence_ms: int = 700
    segment_max_ms: int = 15_000
    segment_overlap_ms: int = 500
    segment_min_speech_ms: int = 400

    # STT: one "<name> <endpoint> <model>" per line, same format as capture-pipeline.
    whisper_tiers: str = ""
    tier_probe_interval_s: float = 30.0
    stt_timeout_s: float = 60.0
    stt_prompt_chars: int = 200
    hallucination_phrases: list[str] = Field(
        default_factory=lambda: [
            "thank you for watching",
            "thanks for watching",
            "subtitles by",
            "subtitles by the amara.org community",
            "please subscribe",
            "like and subscribe",
        ]
    )
    hallucination_rms_threshold: float = 0.01  # normalized to full scale (0..1)
    duplicate_overlap_ratio: float = 0.5
    duplicate_similarity: float = 0.8
    # Max stream time a mic segment waits for the system channel to settle before the
    # duplicate guard is applied anyway.
    duplicate_hold_ms: int = 20_000

    # Diarization sidecar (pyannote on the Spark). Empty URL: no speaker pass, as before.
    diarize_url: str = ""
    # The pass after a session ends may take max(min, ratio x recording length) seconds; past
    # that the session goes on with the undiarized transcript.
    diarize_timeout_min_s: float = 60.0
    diarize_timeout_ratio: float = 0.3
    # An enrolled voice is recognised at this cosine similarity, and only if it beats the next
    # best enrolled voice by the margin. Depends on the embedding model: tune in Phase 0.
    speaker_match_threshold: float = 0.6
    speaker_match_margin: float = 0.05

    # LLM (LiteLLM's OpenAI-compatible API). Empty base URL disables the copilot and finalizer.
    litellm_base_url: str = ""
    litellm_api_key_file: Path | None = None
    copilot_model: str = "fast"
    final_model: str = "heavy"
    copilot_timeout_s: float = 60.0
    final_timeout_s: float = 600.0
    # Off: a reasoning model can spend the whole completion budget thinking and write no note.
    final_thinking: bool = False

    # Copilot loop
    copilot_poll_s: float = 1.0
    copilot_min_segments: int = 3
    copilot_max_wait_s: float = 25.0
    copilot_window_s: float = 480.0
    copilot_max_related: int = 3
    suggestion_default_ttl_s: int = 120

    # Vault context (read-only clone of the Obsidian vault; index lives on local disk)
    vault_clone_dir: Path | None = None
    index_dir: Path = Path("/tmp/jarvis-live-index")  # noqa: S108
    vault_name: str = "Notes"
    vault_exclude_globs: list[str] = Field(default_factory=list)
    vault_refresh_interval_s: float = 600.0

    # Finalizer
    outbox_dir: Path | None = None  # default: <data_dir>/outbox
    final_filename_template: str = "{date} - {slug}.md"  # the vault inbox style
    final_max_input_tokens: int = 24_000
    final_window_s: float = 1200.0
    # On startup, finalize sessions left in `finalizing` that ended within this window. Older
    # ones (e.g. from before the finalizer existed) are left alone. 0 disables recovery.
    finalize_recover_max_age_s: float = 86_400.0
    watchdog_interval_s: float = 10.0  # idle sessions, STT outage, SLO checks
    idle_end_s: float = 600.0  # end a live session after this long without audio
    # A producer that dropped without `end` (a phone in a tunnel) is given this long to come back
    # and replay its buffer before the idle watchdog ends the session.
    producer_gone_grace_s: float = 2_700.0
    # A live session counts as streaming while a producer is connected and audio arrived
    # within this window; otherwise viewers show it as offline.
    streaming_stale_s: float = 10.0
    timezone: str = "UTC"  # for note filenames and `created`

    # Gotify (no-op unless both are set)
    gotify_url: str = ""
    gotify_token_file: Path | None = None
    public_base_url: str = ""
    stt_outage_notify_s: float = 120.0

    # SLOs on the live path (see slo.py). A breach is sustained when the signal's p95 over
    # `slo_window_s` exceeds its limit with at least `slo_min_samples` samples; it re-arms after
    # `slo_clear_s` without one. Limits are the 2026-10-09 handoff's latency budget.
    slo_enabled: bool = True
    slo_window_s: float = 300.0
    slo_min_samples: int = 8
    slo_clear_s: float = 600.0
    slo_authoritative_p95_s: float = 5.0  # segment cut -> published (dup-guard hold excluded)
    slo_stt_p95_s: float = 4.0  # one Whisper request, per tier
    slo_copilot_roundtrip_p95_s: float = 8.0
    slo_copilot_staleness_p95_s: float = 35.0  # handoff: notes refresh in 25-35 s
    slo_finalize_s: float = 120.0  # handoff: final note within 2 min of stop

    # Prometheus scrape port, separate from the app port (which the Ingress routes). 0: off.
    metrics_port: int = 0

    # Sign-in with Pocket-ID (OIDC). Unset issuer: no web sign-in; device tokens still work.
    # The redirect URI is <public_base_url>/auth/callback.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret_file: Path | None = None
    oidc_admin_group: str = ""  # members are admins (see every user's sessions)
    # Comma-separated groups; if set, only their members may sign in.
    oidc_allowed_groups: str = ""
    # The first sign-in with this (verified) email claims the owner account: every device and
    # session from before there were users. See also `jarvis-live claim-owner`.
    owner_email: str = ""
    login_session_days: int = 30

    # Retention
    audio_retention_days: int = 90
    retention_interval_s: float = 86_400.0

    # Publishing finished notes to the Obsidian vault's git repo, one branch + PR per session,
    # through the GitHub REST API (no git binary, no clone). Off unless both the repo
    # ("owner/name") and the token file are set. The token needs Contents and Pull requests
    # read+write on that repo.
    publish_repo: str = ""
    github_token_file: Path | None = None
    github_api: str = "https://api.github.com"
    publish_base_branch: str = "main"
    publish_subdir: str = "00 Inbox/AI Output"  # vault folder the outbox maps onto
    publish_branch_prefix: str = "jarvis-live/"
    publish_merge_method: Literal["squash", "merge", "rebase"] = "squash"
    publish_committer_name: str = "jarvis-live"
    publish_committer_email: str = "jarvis-live@apj.dev"
    # Pick up PRs merged or closed on GitHub. 0 disables the loop (the dashboard still syncs).
    publish_reconcile_interval_s: float = 300.0

    # Work items: ideas spoken in the owner's and admins' sessions become draft Claude Code
    # prompts to review (see workitems/). Only repos listed here can be targeted: one
    # "owner/name  description" per line; the description helps the LLM pick.
    work_items_enabled: bool = False
    work_repos: str = ""
    # The prompt template. Unset: /app/prompts/templates/work-item.md (the image), then the
    # repo's prompts/templates/work-item.md.
    work_template: Path | None = None
    # Close approved items whose PR merged on Forgejo. "work repo  forgejo owner/name" per line:
    # only these are looked up. Other repos, or no URL or token: only Mark Done closes items.
    forgejo_url: str = ""
    forgejo_token_file: Path | None = None  # read:repository
    work_forgejo_repos: str = ""
    work_pr_poll_s: float = 300.0  # 0 disables
    ideas_model: str = ""  # empty: the copilot model
    ideas_timeout_s: float = 120.0
    # A pass runs after this many new final segments, or this long after the first new one.
    ideas_min_new_segments: int = 4
    ideas_interval_s: float = 120.0
    ideas_overlap_s: float = 60.0  # transcript before the last pass shown again, for context
    ideas_max_lines: int = 400  # newest lines kept if the LLM was down for a long stretch

    # Dispatch: approved work items run as headless Claude Code, one Kubernetes Job and one
    # git worktree each, in this pod's namespace (dispatch/). Off unless enabled.
    dispatch_enabled: bool = False
    # Repos a dispatch may target; empty means the work repos. Lab narrows it to a sandbox.
    dispatch_repos: str = ""
    # The runner image. Unset: the runner built from this server's commit (GIT_SHA).
    dispatch_image: str = ""
    git_sha: str = ""  # baked into the image at build time
    dispatch_model: str = "claude-sonnet-5-5"
    dispatch_max_concurrent: int = 1  # runs share the Claude subscription's usage limits
    dispatch_deadline_s: int = 3600
    dispatch_poll_s: float = 15.0
    dispatch_namespace: str = ""  # empty: the pod's own (the service account's namespace)
    dispatch_oauth_secret: str = "claude-oauth"  # Secret (key `password`) with the setup-token
    dispatch_mirror_pvc: str = "jarvis-dispatch-mirrors"
    dispatch_postgres_image: str = "postgres:16.4-alpine"  # test database for each run
    dispatch_node_selector: str = "kubernetes.io/arch=amd64"  # "key=value,key=value"
    github_app_id: str = ""
    github_app_installation_id: str = ""
    github_app_key_file: Path | None = None

    # Homelab Q&A (jarvis_live/homelab/): POST /v1/homelab/ask forwards a device's question to
    # Pi Durable's persistent `jarvis` session and returns its answer. Empty base URL: the
    # route always returns the fallback phrase, so lab can run with no backend wired up.
    dpi_base_url: str = ""
    dpi_token_file: Path | None = None
    dpi_session: str = "jarvis"
    dpi_wait_ms: int = 60_000

    @staticmethod
    def _repo_list(text: str) -> dict[str, str]:
        repos: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name, *rest = line.split(None, 1)
            repos[name] = rest[0].strip() if rest else ""
        return repos

    @property
    def work_repo_allowlist(self) -> dict[str, str]:
        """``owner/name`` -> description, in the order given."""
        return self._repo_list(self.work_repos)

    @property
    def work_forgejo_repo_map(self) -> dict[str, str]:
        """Work repo -> the same repo's ``owner/name`` on Forgejo."""
        return self._repo_list(self.work_forgejo_repos)

    @property
    def dispatch_repo_allowlist(self) -> dict[str, str]:
        return self._repo_list(self.dispatch_repos) or self.work_repo_allowlist

    @property
    def dispatch_runner_image(self) -> str:
        if self.dispatch_image:
            return self.dispatch_image
        sha = self.git_sha.strip()
        return f"ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-{sha[:7]}" if sha else ""

    @property
    def notify_title_prefix(self) -> str:
        return f"[{self.environment.upper()}] "

    @property
    def publish_enabled(self) -> bool:
        return bool(self.publish_repo and self.github_token_file)

    @field_validator("environment")
    @classmethod
    def _channel_name(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("environment must not be empty")
        return v

    @field_validator("publish_subdir")
    @classmethod
    def _vault_relative(cls, v: str) -> str:
        v = v.strip().strip("/")
        if any(p in {".", ".."} for p in v.split("/")):
            raise ValueError("publish_subdir must stay inside the repository")
        return v

    @field_validator("publish_repo")
    @classmethod
    def _owner_slash_name(cls, v: str) -> str:
        v = v.strip()
        if v and (v.count("/") != 1 or not all(v.split("/"))):
            raise ValueError('publish_repo is "owner/name"')
        return v

    @field_validator("work_repos", "dispatch_repos", "work_forgejo_repos")
    @classmethod
    def _repo_lines(cls, v: str) -> str:
        for raw in v.splitlines():
            name = (raw.split(None, 1) or [""])[0]
            bad = name.count("/") != 1 or not all(name.split("/"))
            if name and not name.startswith("#") and bad:
                raise ValueError(f'"{name}" is not "owner/name"')
        return v

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        """Only asyncpg is installed; a bare ``postgresql://`` would make SQLAlchemy ask for
        psycopg. Accept the plain libpq-style URL that CNPG and most tooling hand out."""
        for plain in ("postgresql://", "postgres://"):
            if v.startswith(plain):
                return "postgresql+asyncpg://" + v[len(plain) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
