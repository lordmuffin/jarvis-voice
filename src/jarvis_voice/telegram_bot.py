"""Gate 2: Telegram voice handler. Receives voice/audio messages, transcribes
them with faster-whisper, and writes a markdown note to the vault.
"""
from __future__ import annotations

import logging
import os
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from jarvis_voice import transcribe as transcribe_mod
from jarvis_voice.vault import write_voice_note

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("jarvis_voice.telegram_bot")

PREVIEW_CHARS = 200


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return

    media = msg.voice or msg.audio
    if media is None:
        return

    suffix = ".ogg" if msg.voice is not None else ".m4a"
    try:
        tg_file = await context.bot.get_file(media.file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            audio_path = tmp.name
        await tg_file.download_to_drive(audio_path)
        try:
            transcript, duration = transcribe_mod.transcribe(audio_path)
        finally:
            try:
                os.unlink(audio_path)
            except FileNotFoundError:
                pass

        if not transcript:
            await msg.reply_text("Heard you, but got no speech back. Try again?")
            return

        note_path = write_voice_note(
            transcript=transcript,
            duration_seconds=duration,
            source="telegram-voice",
            model=f"whisper-{transcribe_mod.WHISPER_MODEL}",
        )
        preview = transcript[:PREVIEW_CHARS] + ("…" if len(transcript) > PREVIEW_CHARS else "")
        await msg.reply_text(
            f"✅ Saved → {os.path.basename(note_path)}\n\n{preview}"
        )
    except Exception:
        logger.exception("voice handler failed")
        try:
            await msg.reply_text("⚠️ Transcription failed — check the bot logs.")
        except Exception:
            logger.exception("failed to send error reply")


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Send me a voice message and I'll drop a transcript in your vault."
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set — refusing to start.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, _handle_voice))

    logger.info("jarvis voice bot starting — model=%s", transcribe_mod.WHISPER_MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
