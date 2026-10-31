# Deploying jarvis-live

The Kubernetes manifests live here, next to the code that needs them. The homelab cluster's
Flux applies them from this repository, as two release channels:

| Channel | Runs | Host | Namespace | Overlay | Branch Flux follows |
|---|---|---|---|---|---|
| **live** | the latest release (`server-vX.Y.Z`) | `live.apj.dev` | `jarvis-live` | `k8s/jarvis-live/` | `deploy/live` |
| **lab** | the latest commit on `main` | `live.lab.apj.dev` | `jarvis-live-lab` | `k8s/jarvis-live-lab/` | `deploy/lab` |

Both overlays share `k8s/base/`; each brings its own Ingress. Live is what you depend on. Lab is where a change runs for
real first, against real Postgres, real Whisper and real clients, before a release
vouches for it. Lab may be broken at any time.

## How a change reaches the cluster

1. **Feature PRs** change code and, when needed, the manifests (a new setting in
   `base/configmap.yaml`, a volume, resources) in the same PR. CI and the image smoke test
   run on the PR.
2. **Merging to `main` deploys to lab.** The *Server Image* workflow builds
   `ghcr.io/lordmuffin/jarvis-live:sha-<7>`. Its `deploy-lab` job then moves **`deploy/lab`**
   to that commit plus one generated commit (`chore(deploy): lab -> sha-<7>`) that pins the
   lab overlay to that image. Flux (`ai-jarvis-live-lab`) rolls lab a minute or two later,
   and lab's own database takes any new migration first. Gotify reports it as `[lab]`.
3. **Use lab.** Point a client at `https://live.lab.apj.dev` and record real sessions.
   Check `curl https://live.lab.apj.dev/healthz` for the version, commit and channel (the
   dashboard shows the version next to your name, with the commit on hover). Meanwhile
   the release PR collects the changes.
4. **The release PR** (release-please's `chore: release main`) is the promotion PR. Besides
   the version and changelog, it bumps the image tag in `k8s/base/deployment.yaml` (the
   `# x-release-please-version` marker), so its diff shows exactly what will run on live.
   Merge it when lab has run what it lists long enough for you to trust it.
5. **Merging it promotes to live.** The release commit lands on `main`, so it goes to lab
   like any other commit, and the merge tags `server-vX.Y.Z`. On that tag, `promote` waits
   until lab has that exact commit. It then gives lab's image the `X.Y.Z` tag. That is the
   same digest, not a rebuild, so live runs byte-for-byte what lab ran. Only then does it
   move **`deploy/live`** to the tag. Flux (`ai-jarvis-live`) replaces the pod (`Recreate`).
   If lab never got the commit (its build or `deploy-lab` failed), promotion stops and says so.

Gotify reports each move of either branch ("deploying" or "NOT deployed").

## Rollback, or redeploy

Go to Actions, open **Server Image** and choose **Run workflow**:

- **Live:** set `tag` to an earlier release, e.g. `server-v0.9.0`. This moves
  `deploy/live` back to that release, whose image already exists. There is no lab check
  on this path. Only releases cut after the manifests moved here (from 0.9.0 on) can be
  deployed this way.
- **Lab:** set `lab_sha` to a commit on `main` (from after the lab channel existed). Lab
  then runs that commit's image until the next push to `main`.

Fill in one input, not both.

## How lab is fenced off

Lab runs code no release has vouched for, so `k8s/jarvis-live-lab/` keeps it away from
what live owns. *Deploy CI* enforces each of these:

- **Own state.** Lab has its own namespace and PVCs, and its own Postgres database
  `jarvis_live_lab` (secret `jarvis-live-lab-db-app`). A bad migration breaks lab, not live.
- **No writes to the vault.** Lab doesn't get `notes-vault-pat` and has publishing off
  (no `PUBLISH_REPO`), so it can never open or merge a PR on `lordmuffin/Notes`. It still
  has a read-only vault clone for search context.
- **Its own host, marked as lab.** Lab is at `live.lab.apj.dev` and has
  `JARVIS_LIVE_ENVIRONMENT=lab`. The dashboard shows a LAB badge and Gotify titles start
  with `[lab]`.
- **Admins only.** `OIDC_ALLOWED_GROUPS` is the admin group.
- **Dispatch to the sandbox only.** Lab runs approved work items as Claude Code only against
  `lordmuffin/jarvis-dispatch-sandbox`. It uses its own GitHub App (`jarvis-dispatch-lab-app`,
  installed on that repo alone) and never live's App (`policy/lab.rego`). Until that
  1Password item exists, lab runs with dispatch off.

Lab shares the Whisper tiers and LiteLLM with live. A runaway lab session competes for
the same GPUs.

## Dispatch

Approved work items run as Jobs in each channel's own namespace (`k8s/base/dispatch.yaml`).
Every channel ships the same fence, and the policies require it:

- **NetworkPolicy `jarvis-dispatch`:** run pods reach DNS and the internet, never the cluster
  or the LAN, and accept no connections.
- **Role `jarvis-dispatch`:** the app's `jarvis-live` ServiceAccount may manage its Jobs, its
  per-run Secrets (no list or watch) and read pods, in its own namespace only.
- **PVC `jarvis-dispatch-mirrors`:** RWX, `prune: disabled`, the shared repo mirrors.

Dispatch is off in base. Lab turns it on for the sandbox; live stays off until lab has proven
it. One-time setup (the sandbox repo, the GitHub App, the `claude-oauth` and
`jarvis-dispatch-lab-app` items, making the runner package public) is in
[`docs/plans/copilot-to-claude-code.md`](../../docs/plans/copilot-to-claude-code.md) §3.9.

## Resetting lab

Lab data is disposable and is not backed up (its backup CronJob is suspended). To start it from empty, for example after a
migration from a reverted change, do this in np-home-homelab or the cluster. Never do any
of it to `jarvis-live`.

1. `flux suspend kustomization ai-jarvis-live-lab`
2. `kubectl -n jarvis-live-lab scale deploy/jarvis-live --replicas=0`
3. Drop and recreate the database: delete then re-apply the CNPG `Database`
   `jarvis-live-lab` (np-home-homelab `apps/storage/postgres-cluster`), or
   `DROP DATABASE jarvis_live_lab` and let CNPG recreate it.
4. Optionally delete the PVCs: `kubectl -n jarvis-live-lab delete pvc --all`.
5. `flux resume kustomization ai-jarvis-live-lab`. Flux recreates the PVCs, and the app
   migrates the empty database on start.

## Metrics, SLOs and the dashboard

The server exports Prometheus metrics (`jarvis_live_*`) on `JARVIS_LIVE_METRICS_PORT` (9100, the
Service's `metrics` port). It is not the app port because each channel's Ingress routes the app
port to the internet. Labels are closed sets (channel, tier, outcome, job source); never a session.
np-home-homelab scrapes both channels and provisions the dashboard from
`server/deploy/grafana/jarvis-live.json` (this repo owns it; the homelab keeps a copy).

The server judges its own latency SLOs from the handoff's budget (`JARVIS_LIVE_SLO_*`, see
`slo.py`) and pushes **one** Gotify message per sustained breach, then stays quiet until the
signal has been healthy for `SLO_CLEAR_S`. There is no recovery push and no Alertmanager rule
(it would page twice). While a tier is breached the transcript SLO is tracked but silent, and
copilot staleness defers to the copilot round trip, so one cause is one alert. The "transcription
down" and "note failed" alerts are separate and unchanged.

Every final note ends its summary with a "Session timing" section and a one-line verdict.

To see it work against lab: `jarvis-live replay --server <lab url> --token ... --wav x.wav
--drafts`, then open the dashboard on the `jarvis-live-lab` channel.

## What is not here

This repository is public, so environment-private values stay in np-home-homelab:

- `JARVIS_LIVE_OWNER_EMAIL` is patched into each channel's ConfigMap by its Flux
  Kustomization (`ai-jarvis-live`, `ai-jarvis-live-lab`).
- The Postgres roles and databases (`apps/storage/postgres-cluster`) and the DB
  ExternalSecrets (`apps/secrets`).
- The TLS certificate reflector and the Pocket ID client registration. One client serves
  both channels, with a redirect URI for each host.

See np-home-homelab `docs/release-channels.md` for the pattern in general.

Secret *values* live in 1Password. The `OnePasswordItem`s here only name the items.

## Rules

- Never push to `deploy/live` or `deploy/lab` by hand, and never edit the lab overlay's
  `newTag`. The workflow owns both branches. Use "Run workflow" instead.
- The namespaces and PVCs carry `kustomize.toolkit.fluxcd.io/prune: disabled`. The PVCs are
  `freenas-nfs-csi` with reclaim `Delete`, so a pruned PVC is lost data. Keep the
  annotation on any new PVC; *Deploy CI* fails without it.
- Do not hand-edit the image line. *Deploy CI* checks it matches the server version.
- A new setting or volume goes in `base/`, so lab runs it before live does. Put only
  what differs per channel in an overlay.
- Render locally with `kubectl kustomize server/deploy/k8s/jarvis-live` (live) and
  `kubectl kustomize server/deploy/k8s/jarvis-live-lab` (lab).

## Backups and retention

- **Retention** (the server, daily): `JARVIS_LIVE_AUDIO_RETENTION_DAYS` (90) deletes old audio
  of finished sessions and keeps the rows. `JARVIS_LIVE_TRASH_RETENTION_DAYS` (30) deletes a
  session that has been in "Recently deleted" that long for good: rows, audio and note together.
  A session can also be set to move to the trash after N days. Set in `k8s/base/configmap.yaml`.
- **Backups** (`k8s/base/cronjob-backup.yaml`, 03:00 UTC): `jarvis-live backup` writes a
  `pg_dump` and a manifest of every recording to the `jarvis-live-backups` volume, which Velero
  carries offsite with the audio (04:00). Failure sends a Gotify notice; a backup that never ran
  shows on `/healthz` and in a daily notice. The CronJob takes the Deployment's image, so it
  moves with every release, and the image carries `pg_dump` 18 (the CNPG major).
- **Restoring** one session, the database, or a volume: [`docs/runbooks/restore.md`](../../docs/runbooks/restore.md).
