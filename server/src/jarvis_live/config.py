from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_LIVE_")

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis_live"
    log_level: str = "info"
    data_dir: Path = Path("/data")

    # Ingest
    ack_interval_ms: int = 250  # protocol requires an ack at least every 500 ms
    fsync_interval_s: float = 1.0
    status_interval_s: float = 5.0

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

    # Retention
    audio_retention_days: int = 90
    retention_interval_s: float = 86_400.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
