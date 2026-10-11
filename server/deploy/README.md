# Deploying jarvis-live

The Kubernetes manifests live here, next to the code that needs them. The homelab cluster's
Flux applies them from this repository, as two release channels:

| Channel | Runs | Host | Namespace | Overlay | Branch Flux follows |
|---|---|---|---|---|---|
| **live** | the latest release (`server-vX.Y.Z`) | `live.apj.dev` (also `live.lab.apj.dev` until its promotion retires it) | `jarvis-live` | `k8s/jarvis-live/` | `deploy/live` |
| **lab** | the latest commit on `main` | `live-next.lab.apj.dev` | `jarvis-live-lab` | `k8s/jarvis-live-lab/` | `deploy/lab` |

Both overlays share `k8s/base/`. Live is what you depend on. Lab is where a change runs for
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
3. **Use lab.** Point a client at `https://live-next.lab.apj.dev` and record real sessions.
   Check `curl https://live-next.lab.apj.dev/healthz` for the version and channel. Meanwhile
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
- **Its own host, marked as lab.** Lab is at `live-next.lab.apj.dev` and has
  `JARVIS_LIVE_ENVIRONMENT=lab`. The dashboard shows a LAB badge and Gotify titles start
  with `[lab]`.
- **Admins only.** `OIDC_ALLOWED_GROUPS` is the admin group.

Lab shares the Whisper tiers and LiteLLM with live. A runaway lab session competes for
the same GPUs.

## Resetting lab

Lab data is disposable and is not backed up. To start it from empty, for example after a
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
