import os
from pydantic_settings import BaseSettings

class VoiceCallSettings(BaseSettings):
    SIGNALING_HOST: str = os.getenv("SIGNALING_HOST", "0.0.0.0")
    SIGNALING_PORT: int = int(os.getenv("SIGNALING_PORT", "8765"))
    TURN_SERVER: str = os.getenv("TURN_SERVER", "stun:stun.l.google.com:19302")
    TURN_USERNAME: str = os.getenv("TURN_USERNAME", "jarvis")
    TURN_PASSWORD: str = os.getenv("TURN_PASSWORD", "secret")
    AUDIO_SAMPLE_RATE: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    AUDIO_CHANNELS: int = int(os.getenv("AUDIO_CHANNELS", "1"))
    AUDIO_FORMAT: str = os.getenv("AUDIO_FORMAT", "opus")

    class Config:
        env_prefix = "VOICE_"

voice_config = VoiceCallSettings()
