"""Kai — LiveKit voice agent with vault tool calls and barge-in.

Stack (all on DGX .155):
  VAD  : Silero
  STT  : faster-whisper (local)  or  LiteLLM whisper-1 proxy
  LLM  : LiteLLM → Qwen3-heavy via hermes tool parser
  TTS  : Kokoro HTTP  or  LiteLLM TTS fallback
  Tools: read_note, search_vault, get_sprint_state, append_to_note

Barge-in: AgentSession(allow_interruptions=True, min_interruption_duration=0.5)

Qwen3 thinking mode is disabled globally via extra_body so tool call
parsing is never broken by chain-of-thought output.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool
from livekit.plugins import openai as lk_openai, silero

from jarvis_voice.vault_tools import execute_tool, vault_available

logger = logging.getLogger("kai-agent")

LITELLM_BASE  = os.environ.get("LITELLM_URL",   "http://192.168.1.93:4000")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL",  "qwen3-heavy")
KOKORO_URL    = os.environ.get("KOKORO_URL",      "")
KOKORO_VOICE  = os.environ.get("KOKORO_VOICE",   "af_sky")

_SYSTEM = (
    "You are Kai, Andrew's AI chief of staff. "
    "This is a voice conversation — reply in 1-3 sentences unless you need vault data. "
    "When Andrew asks about tasks, priorities, sprint board, daily notes, or wants to "
    "capture a thought, use the available vault tools before answering."
)


class KaiAgent(Agent):
    """Kai voice agent with vault access and barge-in."""

    def __init__(self) -> None:
        super().__init__(instructions=_SYSTEM)
        self._vault_ok = vault_available()
        if not self._vault_ok:
            logger.warning("Vault not mounted at /mnt/notes — tool calls will fail")

    async def on_enter(self) -> None:
        greeting = (
            "Kai ready. Vault connected."
            if self._vault_ok
            else "Kai ready. Warning: vault is not mounted — note tools unavailable."
        )
        await self.session.say(greeting, allow_interruptions=True)

    # ── Vault tool definitions ────────────────────────────────────────────────

    @function_tool(
        name="read_note",
        description=(
            "Read a specific note from Andrew's vault by relative path. "
            "Use for: daily notes, sprint board, project notes, TASKS.md. "
            "Paths are relative to vault root, e.g. '20 Areas/Personal/Sprints.md'"
        ),
    )
    async def read_note(
        self,
        path: Annotated[str, "Relative path to the note within the vault"],
    ) -> str:
        logger.info("tool: read_note path=%s", path)
        return execute_tool("read_note", {"path": path})

    @function_tool(
        name="search_vault",
        description="Search the vault for notes containing a keyword or phrase. Returns matching file paths.",
    )
    async def search_vault(
        self,
        query: Annotated[str, "Search term or phrase"],
        directory: Annotated[str, "Subdirectory to search within (optional)"] = "",
    ) -> str:
        logger.info("tool: search_vault query=%s dir=%s", query, directory)
        return execute_tool("search_vault", {"query": query, "directory": directory})

    @function_tool(
        name="get_sprint_state",
        description=(
            "Get the current sprint board, daily highlight, and open tasks. "
            "Call when Andrew asks about priorities, what's next, or what to work on."
        ),
    )
    async def get_sprint_state(self) -> str:
        logger.info("tool: get_sprint_state")
        return execute_tool("get_sprint_state", {})

    @function_tool(
        name="append_to_note",
        description=(
            "Append text to an existing note in Andrew's vault. "
            "Use for capturing voice thoughts into Brain Dump, adding action items to TASKS.md, "
            "or logging to today's daily note."
        ),
    )
    async def append_to_note(
        self,
        path: Annotated[str, "Relative path to the note within the vault"],
        text: Annotated[str, "Text to append"],
    ) -> str:
        logger.info("tool: append_to_note path=%s", path)
        return execute_tool("append_to_note", {"path": path, "text": text})


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    # ── TTS ───────────────────────────────────────────────────────────────────
    if KOKORO_URL:
        # Kokoro HTTP TTS: strip trailing path, add /v1 for openai-compat endpoint
        kokoro_base = KOKORO_URL.rstrip("/").removesuffix("/audio/speech").removesuffix("/v1")
        tts = lk_openai.TTS(
            base_url=f"{kokoro_base}/v1",
            api_key="kokoro",
            model="kokoro",
            voice=KOKORO_VOICE,
        )
    else:
        tts = lk_openai.TTS(
            base_url=f"{LITELLM_BASE}/v1",
            api_key="fake",
            voice="alloy",
        )

    # ── LLM (disable Qwen3 thinking mode globally via extra_body) ─────────────
    # Thinking mode breaks hermes tool call parsing — always off when tools active.
    llm = lk_openai.LLM(
        base_url=f"{LITELLM_BASE}/v1",
        api_key="fake",
        model=LITELLM_MODEL,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    # ── AgentSession with barge-in ────────────────────────────────────────────
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=lk_openai.STT(
            base_url=f"{LITELLM_BASE}/v1",
            api_key="fake",
            model="whisper-1",
        ),
        llm=llm,
        tts=tts,
        allow_interruptions=True,
        min_interruption_duration=0.5,
        min_endpointing_delay=0.5,
    )

    await session.start(agent=KaiAgent(), room=ctx.room)
    await asyncio.sleep(1)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
