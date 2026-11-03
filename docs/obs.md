# Streaming from OBS (or a phone) into Jarvis Live

Point OBS at Jarvis Live and get a live session while you stream or record: segments as you
talk, the copilot, and the final note when you stop. Nothing to upload afterwards.

How it works: OBS pushes RTMP or SRT to **MediaMTX** in the cluster. For every stream that has
an ingest key, the server reads the audio back from MediaMTX and runs it through the same path
as any live client. The audio stays on the homelab.

| | |
|---|---|
| Where | lab only, for now (`JARVIS_LIVE_STREAMS_ENABLED`, `server/deploy/k8s/base/streams.yaml`) |
| Reachable from | the LAN and the VPN only (the homelab exposes it; nothing on the internet) |
| Protocols | RTMP (port 1935) and SRT (port 8890/udp) |
| Added lag | about 0.5 s over SRT, 1–2 s over RTMP, on top of the usual transcript lag |

Below, `<ingest-host>` is MediaMTX's LAN name or address (from the homelab), `<key>` the ingest
key and `obs-desk` the stream's name. Use your own name; keep it short: a-z, 0-9 and `-`.

## 1. Create an ingest key (once per OBS install or phone)

An ingest key can only publish one stream. It cannot read anything, so it is what you type
into OBS, never a device token. Create it on the server:

```sh
kubectl -n jarvis-live-lab exec deploy/jarvis-live -- \
  jarvis-live stream-key create obs-desk --meeting --title "Stream" --host <ingest-host>
```

- `--meeting` for sessions with other people (calls, guests); leave it out for solo.
- `--title` names every session from this stream; leave it out and the copilot names each one.
- `--restricted` for work calls: the note stays on the homelab, as with any restricted session.
- `--source mobile` for a phone, so its sessions are tagged as such (default `obs`).

The key is printed **once**, with the URLs to paste. `jarvis-live stream-key list` shows the
streams (never the keys), revoked ones included.

- **Lost the key, or it ended up in a screenshot or a shared log?**
  `jarvis-live stream-key rotate obs-desk --host <ingest-host>` revokes it and prints a new one
  with the same name and settings, so only the key in OBS changes. A stream already running on
  the old key keeps its session until it reconnects.
- **Done with a stream:** `jarvis-live stream-key revoke obs-desk`. Its name can be created
  again later.

There is no local-only stream: a stream has left the computer by the time Jarvis sees it.
For a session that must stay on your machine, use the desktop app's local-only mode instead.

## 2. OBS: one track (simplest)

Everything OBS hears (mic and desktop audio, mixed) becomes the mic channel. Speakers are told
apart by diarization only, and everything is labelled "me".

1. **Settings → Stream**
   - Service: **Custom...**
   - Server: `rtmp://<ingest-host>:1935`
   - Stream Key: `obs-desk?user=obs&pass=<key>`
2. **Settings → Advanced → Automatically Reconnect**: on, retry delay 2–10 s. A dropped
   connection that comes back within 3 minutes continues the same session.
3. **Start Streaming.** The session appears in the dashboard within a few seconds.
4. **Stop Streaming.** Sixty seconds later the session ends and the final note is written.

Over SRT instead (better on Wi-Fi or a lossy VPN): Server
`srt://<ingest-host>:8890?streamid=publish:obs-desk:obs:<key>&pkt_size=1316` and an empty
Stream Key. The key is then part of the server URL, which OBS keeps in its profile and may write
to its log, so prefer RTMP when the network is good.

Already streaming to a platform? The Stream output has room for one destination. Use the
recording output for Jarvis (section 3), which also gives you separate channels.

## 3. OBS: mic and desktop audio apart (recommended)

With two tracks, your voice is "me" and what the computer plays (call guests, video) is
"them", and the server can drop your voice where it also comes back through desktop audio.
Your broadcast is untouched.

1. **Edit → Advanced Audio Properties**, the **Tracks** columns:
   - Mic/Aux: tracks **1** and **2**
   - Desktop Audio: tracks **1** and **3**

   Track 1 stays the full mix for your broadcast. Tracks 2 and 3 are for Jarvis.
2. **Settings → Output**, Output Mode **Advanced**, tab **Recording**:
   - Type: **Custom Output (FFmpeg)**
   - FFmpeg Output Type: **Output to URL**
   - File path or URL: `srt://<ingest-host>:8890?streamid=publish:obs-desk:obs:<key>&pkt_size=1316`
   - Container Format: **mpegts**
   - Video: any encoder at a low bitrate (Jarvis ignores video; 500 Kbps is plenty)
   - Audio Track: tick **2** and **3** only
   - Audio Encoder: **aac**, Audio Bitrate 128
3. **Start Recording** starts the Jarvis session; **Stop Recording** ends it (after the 3-minute
   grace period). Start Streaming is separate and goes wherever it went before.

The first audio track in what OBS sends is the mic, the second desktop audio; any further tracks
are ignored. With only one track the session is mic only (section 2).

While Jarvis has the recording output, OBS does not record to a file. That is the trade.

## 4. A phone

Any app that publishes SRT or RTMP works (for example Larix Broadcaster):

- SRT: `srt://<ingest-host>:8890?streamid=publish:phone:obs:<key>`
- RTMP: URL `rtmp://<ingest-host>:1935/phone?user=obs&pass=<key>`

Create its key with `--source mobile`. The phone has to be on the LAN or the VPN.

## What happens when

| You | Jarvis |
|---|---|
| Start streaming or recording | A session starts with the key's title, mode and restricted flag, tagged `obs` (or `mobile`). |
| Network drops, OBS is back within 3 minutes | The same session continues. The audio sent while disconnected is lost; the timeline skips the gap. |
| Network drops for longer | The session ends with a final note; the reconnect starts a new one. |
| Stop | The session ends 3 minutes later (`JARVIS_LIVE_STREAMS_STOP_GRACE_S`, 180 s) and the final note runs. OBS gives no sign that tells a stop from a drop, so every end waits out the grace period. |
| More than 8 streams at once | The ninth is refused (`JARVIS_LIVE_STREAMS_MAX`). |

## When it does not work

- **OBS says it cannot connect, or the connection is refused at once:** a wrong key or name,
  a revoked key, or ingest is full. The server logs `stream publish refused` with the path
  (never the key). `stream-key list` shows whether the key is active.
- **OBS connects but no session appears:** the stream has no audio track, or audio is muted
  in OBS. The server logs `online but has no ingest key` if the name has no key.
- **Nothing reaches the server at all:** check that you are on the LAN or the VPN, and that
  the homelab exposes the ingest ports (RTMP 1935/tcp, SRT 8890/udp).
- **A session per reconnect:** OBS took longer than the 3-minute grace period to reconnect. OBS waits longer between each retry, so a short Retry Delay (Settings → Advanced → Automatically Reconnect, 2 s) and a high Maximum Retries help.
