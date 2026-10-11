# Deploying jarvis-live

The Kubernetes manifests for live.lab.apj.dev live here, next to the code that needs
them, in `k8s/jarvis-live/`. The homelab cluster's Flux applies them from this repository.

## How a change reaches the cluster

1. **Feature PRs** change code and, when needed, these manifests (a new setting in
   `configmap.yaml`, a volume, resources) in the same PR. Merging to `main` deploys nothing.
2. **The release PR** (release-please's `chore: release main`) is the deploy PR. Besides
   the version and changelog, it bumps the image tag in `k8s/jarvis-live/deployment.yaml`
   (the `# x-release-please-version` marker), so its diff shows exactly what will run.
3. **Merging it** tags `server-vX.Y.Z`. The *Server Image* workflow builds and pushes
   `ghcr.io/lordmuffin/jarvis-live:X.Y.Z`. Only after the image exists does its `promote`
   job move the branch **`deploy/live`** to that commit.
4. **Flux** (np-home-homelab `clusters/k3s2/ai/jarvis-live.yaml`, GitRepository
   `jarvis-voice`, interval 1m) follows `deploy/live` and applies `k8s/jarvis-live` with the
   `ai-jarvis-live` Kustomization. The pod is replaced (`Recreate`) a minute or two later.

Gotify reports each promotion ("deploying" or "NOT deployed").

## Rollback, or redeploy

Go to Actions, open **Server Image**, choose **Run workflow** and enter an earlier tag (e.g.
`server-v0.9.0`). This moves `deploy/live` back to that release, whose image is already
built. Only releases cut after the manifests moved here (from 0.9.0 on) can be deployed
this way.

## What is not here

This repository is public, so environment-private values stay in np-home-homelab:

- `JARVIS_LIVE_OWNER_EMAIL` is patched into the ConfigMap by the `ai-jarvis-live`
  Kustomization.
- The Postgres role and database (`apps/storage/postgres-cluster`) and the DB
  ExternalSecret (`apps/secrets`).
- The TLS certificate reflector and the Pocket ID client registration.

Secret *values* live in 1Password. The `OnePasswordItem`s here only name the items.

## Rules

- The namespace and PVCs carry `kustomize.toolkit.fluxcd.io/prune: disabled`. The PVCs are
  `freenas-nfs-csi` with reclaim `Delete`, so a pruned PVC is lost data. Keep the
  annotation on any new PVC; *Deploy CI* fails without it.
- Do not hand-edit the image line. *Deploy CI* checks it matches the server version.
- Render locally with `kubectl kustomize server/deploy/k8s/jarvis-live`.
