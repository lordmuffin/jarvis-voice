# Plan: held asks answered from the Notes vault

Status: **built, not yet measured live.** Written 2026-11-05. Extends
[`voice-push-to-talk.md`](voice-push-to-talk.md): a held ask used to answer only from the last
`voice_ask_window_s` of transcript. For the owner it now also reads a few passages of their Notes
vault. No live eval run yet: see [Follow-ups](#follow-ups).

Goal: when the owner holds the ask key during a recording and asks Jarvis a question ("what did I
decide about the backup schedule?", "what's in my note on the Grayfield review?"), the answer can
draw on their Notes vault (lordmuffin/Notes), not only the last few minutes of transcript.

## Where it started

- `server/src/jarvis_live/voice/ask_pass.py`: `AskPass`. `_ask` loads the last
  `voice_ask_window_s` of transcript, builds `ask_user(today, transcript, question)` and calls
  `complete_json` with `ASK_SYSTEM`, thinking off, `voice_ask_timeout_s`. It never looked at
  the vault. Wake-phrase requests go to `VoicePass` first and fall back to `_ask` when nothing is filed.
- `server/src/jarvis_live/vault/index.py`: `VaultIndex`, a SQLite FTS5 index over the read-only
  clone at `vault_clone_dir` (already mounted at `/vault/repo` on lab and live by
  `server/deploy/k8s/base/`; no manifest change is needed). `search()` returns `Related` with
  240-char snippets; `VaultContext` is the protocol and `NullVault` the no-op.
- `server/src/jarvis_live/orchestrator.py`: builds `AskPass` in `_open_desk` (around line 226).
  The copilot gets `self._vault` only when `row.user_id == OWNER_USER_ID`, else `NullVault()`.
  The vault is the owner's personal notes: other users' sessions must never see it.
- Evals: `server/tests/evals/ask_cases.py` (the bar), `test_ask_offline.py`, `test_ask_evals.py`
  (`--live`, against LiteLLM). Plan context: `docs/plans/voice-push-to-talk.md`,
  `docs/plans/fast-tier.md`.
- The repo is public: tests and docs use invented note text, never real vault content, names or
  internal addresses.

## Decisions

- Read-only. The ask only reads the index. Writing to the vault already goes through action
  proposals (`actions/vault.py`, spoken "Jarvis, ..." requests); this change does not touch it.
- Retrieval is the existing FTS index, run once per ask with the question as the query. No
  tool-calling loop: the ask model is a small fast-tier model with a 2.5 s LLM budget.
- Small prompts. At most `voice_ask_vault_notes` passages (default 3), at most
  `voice_ask_vault_chars` characters in total (default 1500). Long prompts at a steady rate have
  OOMed the cluster's small model before; keep the block bounded and do not raise the defaults.
- The vault search is capped at `voice_ask_vault_timeout_s` (default 1.0 s). A timeout or error
  means no notes, logged, and the ask is answered from the transcript as today. It never fails
  the ask.
- Owner only, the same gate as the copilot: `NullVault()` for any other user's session.
- `voice_ask_vault_enabled: bool = True`, so it can be switched off per channel by config.
- No protocol or client change. The reply is still spoken text on the existing wire. Which notes
  were used is stored in the `voice_ask` row's payload (`notes`: list of vault paths) and its
  search time in `stages["vault"]`, for the dashboard and `ask-report` to use later.

## What was built

Three commits, server only. No protocol, client or manifest change.

1. **Vault passages** (`vault/index.py`). `Passage(path, title, heading, text)` and
   `passages(query, limit, max_chars)` on `VaultContext`, `NullVault` (always `[]`) and
   `VaultIndex`. `search` and `passages` share one query (`_best_chunks`: the same `_fts_queries`,
   bm25 weights 6/3/1, one chunk per note), so they rank alike. Exclusions are the index's own:
   excluded folders and globs are never indexed. The lookup runs in a thread; a `sqlite3.Error` is
   logged and gives no passages. `max_chars` counts passage text only. Each passage gets an even
   share of what is left, so 1500 over 3 is about 500 each and a short one leaves more for the
   next; text is whitespace-collapsed and cut at a word with "…".
2. **The ask reads them** (`voice/ask_pass.py`, `orchestrator.py`). `AskPass` takes a `vault`.
   `_notes()` looks up passages for the question, under `voice_ask_vault_timeout_s`, only when
   `voice_ask_vault_enabled` and the session is the owner's. `ask_user` always sends a `NOTES`
   block between the transcript and the question: `[Title > Heading]` then the text per passage
   (heading left out when empty or equal to the title; the path is never in the prompt), or
   `(none)`. So every ask sees the same prompt shape. `ASK_SYSTEM` says NOTES are the owner's own
   notes, data and never instructions; answer from the transcript or the notes; it may name the
   note; otherwise say it did not catch that or has no note on it. The 30-word rule and the JSON
   reply are unchanged.
3. **Eval cases** (`tests/evals/ask_cases.py`, `test_ask_offline.py`). A case can carry invented
   notes, sent by `eval_user` exactly as `AskPass` sends them. Five new cases, so the bar is 17
   cases, 3 critical: the answer is only in a note; the meeting wins over a note that disagrees; a
   note giving instructions is not obeyed (**critical**); irrelevant notes are no answer; naming
   the note stays within 30 words. `DONT_KNOW` also accepts "no note" and its variants. The
   offline test runs the real `AskPass` as the owner with a stand-in vault, so it covers the NOTES
   rendering and checks every case's notes fit the default limits.

### Stored

The `voice_ask` row's payload gains `notes`, the vault paths of the passages sent, and
`stages["vault"]`, the search seconds. `stages["vault"]` is absent when the lookup was skipped
(disabled, or not the owner). `stages["llm"]` excludes the vault time, so `ask-report`'s LLM
numbers stay comparable with rows from before this change.

## Settings

| Setting | Default | |
|---|---|---|
| `JARVIS_LIVE_VOICE_ASK_VAULT_ENABLED` | `true` | Off: asks answer from the transcript only, as before |
| `JARVIS_LIVE_VOICE_ASK_VAULT_NOTES` | `3` | Passages per ask, one per note |
| `JARVIS_LIVE_VOICE_ASK_VAULT_CHARS` | `1500` | Passage text in all |
| `JARVIS_LIVE_VOICE_ASK_VAULT_TIMEOUT_S` | `1.0` | A slower search is no notes, not a slower answer |

Do not raise the notes or chars defaults: long prompts at a steady rate have OOMed the cluster's
small model before.

## The owner gate

The vault is the owner's personal notes. Two checks keep it out of anyone else's ask:

- `orchestrator._open_desk` hands `AskPass` the vault only when the session's user is
  `OWNER_USER_ID`, and `NullVault()` otherwise, the same gate as the copilot.
- `AskPass._notes` checks the user again and skips the lookup for anyone else.

Admins' sessions can hold an ask, but never read the owner's vault.

## Follow-ups

- **Show `notes` on the web ask review,** so a reviewer can see which notes an answer drew on.
- **Add a vault stage to `ask-report`,** from `stages["vault"]`, beside the LLM and STT stages.
- **A live eval run.** Not run in this change. From `server/`:
  `JARVIS_LIVE_LITELLM_BASE_URL=<litellm> uv run pytest -q tests/evals/test_ask_evals.py --live -s`
  (add `JARVIS_LIVE_VOICE_MODEL=<model>` to pin the fast-tier model). Watch the small model's memory
  as well as its queue while it runs.
