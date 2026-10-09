# Jarvis Live wire protocol — v1

Shared by `server/`, the macOS app, and the web dashboard. JSON Schemas (draft 2020-12) in
`schemas/` are the machine-readable source of truth; this file is the human spec.
Fixtures in `fixtures/` are the conformance suite every implementation must pass.

## Fixtures

- `fixtures/valid/<schema>[__variant].json` must validate against `schemas/<schema>.json`.
- `fixtures/invalid/<schema>__<reason>.json` must be rejected by it.
- `fixtures/frame_mic_seq7.hex` is a binary frame (hex text): channel 0, seq 7, t_ms 140, then 640 zero bytes (20 ms).

Any change under `protocol/` needs fixtures plus updated server **and** Swift tests.

## REST

| Request | Body | Response |
|---|---|---|
| `POST /v1/sessions` | `session_create_request`: `{title?, mode: "meeting"\|"solo", channels: ["mic"] \| ["mic","system"]}` | `session_create_response`: `{id: uuid}` |
| `POST /v1/sessions/{id}/ticket` | `ticket_request`: `{role: "producer"\|"viewer"}` | `ticket_response`: `{ticket, expires_in: 60}` |

A ticket is a short-lived (60 s) credential for the WebSocket below.

## WebSocket

`/v1/sessions/{id}/stream?ticket=…`

### Binary frames (client → server)

12-byte little-endian header, then payload:

| Offset | Type | Field | Notes |
|---|---|---|---|
| 0 | u8 | version | `1` |
| 1 | u8 | channel | `0` mic, `1` system |
| 2 | u16 | flags | `0` (reserved) |
| 4 | u32 | seq | per-channel, increments by 1 per frame |
| 8 | u32 | t_ms | capture time since session start |

Payload: PCM16LE, 16 kHz, mono, 20–200 ms per frame (640–6400 bytes, 32 bytes/ms).
Senders MUST NOT send frames outside this range; receivers MUST reject them.

### Client → server JSON (discriminator: `type`)

- `hello` `{protocol: 1, device, codec: "pcm16le_16k", resume: {mic: int|null, system: int|null}}`
  — `resume` is the last seq the client believes was acked per channel.
- `draft_segment` `{channel, start_ms, end_ms, text, final}` — client-side draft transcript.
- `marker` `{t_ms, label}`
- `end` `{}`

### Server → client JSON

- `hello_ack` `{session, acked: {mic, system}}` — last seq stored per channel (`null` = none).
- `ack` `{channel, seq}` — cumulative: all frames `<= seq` on `channel` are durable.
- `segment` `{id, channel, speaker: "me"|"them", start_ms, end_ms, text, stt_tier}`
- `copilot` `{version, notes[], actions[], decisions[], suggestions[], related[]}` — full snapshot; higher `version` wins.
  - `notes`/`decisions`: `{id, text}`; `actions`: `{id, text, owner?, due?}`
  - `suggestions`: `{id, kind: "question"|"gap"|"counterpoint"|"fact_check", text, expires_at_ms}`
  - `related`: `{path, title, snippet, uri}`
- `status` `{stt_tier: str|null, llm_ok, lag_ms}`
- `final_note` `{path, title}`
- `error` `{code, message}`

All JSON objects reject unknown fields (`additionalProperties: false`).
