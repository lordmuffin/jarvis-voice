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
    data_dir: Path = Path("/data")

    # Ingest
    ack_interval_ms: int = 250  # protocol requires an ack at least every 500 ms
    fsync_interval_s: float = 1.0
    status_interval_s: float = 5.0

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

    # LLM (LiteLLM's OpenAI-compatible API). Empty base URL disables the copilot and finalizer.
    litellm_base_url: str = ""
    litellm_api_key_file: Path | None = None
    copilot_model: str = "fast"
    final_model: str = "heavy"
    copilot_timeout_s: float = 60.0
    final_timeout_s: float = 600.0

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
    final_filename_template: str = "{date} {time} - {slug}.md"
    final_max_input_tokens: int = 24_000
    final_window_s: float = 1200.0
    # On startup, finalize sessions left in `finalizing` that ended within this window. Older
    # ones (e.g. from before the finalizer existed) are left alone. 0 disables recovery.
    finalize_recover_max_age_s: float = 86_400.0
    idle_end_s: float = 600.0  # end a live session after this long without audio
    # A live session counts as streaming while a producer is connected and audio arrived
    # within this window; otherwise viewers show it as offline.
    streaming_stale_s: float = 10.0
    timezone: str = "UTC"  # for note filenames and `created`

    # Gotify (no-op unless both are set)
    gotify_url: str = ""
    gotify_token_file: Path | None = None
    public_base_url: str = ""
    stt_outage_notify_s: float = 120.0

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
    publish_subdir: str = "00 Inbox/Live Sessions"  # vault folder the outbox maps onto
    publish_branch_prefix: str = "jarvis-live/"
    publish_merge_method: Literal["squash", "merge", "rebase"] = "squash"
    publish_committer_name: str = "jarvis-live"
    publish_committer_email: str = "jarvis-live@apj.dev"
    # Pick up PRs merged or closed on GitHub. 0 disables the loop (the dashboard still syncs).
    publish_reconcile_interval_s: float = 300.0

    @property
    def publish_enabled(self) -> bool:
        return bool(self.publish_repo and self.github_token_file)

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
