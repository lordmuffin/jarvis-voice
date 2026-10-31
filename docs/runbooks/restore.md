# Restoring Jarvis Live: one session, the database, the volumes

When a session is gone (deleted for good, or the disk is lost), this is what to type. It
covers the common case, **one session**, and points at the homelab's runbooks for the rest.

Plan and design: [`docs/plans/isolation-and-backups.md`](../plans/isolation-and-backups.md).

## 1. What is backed up, and how fresh

| What | By | Where it lands | Kept | RPO | RTO (one session) |
|---|---|---|---|---|---|
| Database, whole | `jarvis-live backup`, a CronJob at 03:00 UTC: `pg_dump -Fc`, read back to check it | `jarvis-live-backups` volume (`db/<stamp>.dump`), then B2 with Velero | 7 daily + 4 weekly | 24 h | ~15 min |
| Recordings and notes: a manifest of every file with size and sha256 | the same job | `audio/<stamp>.manifest.json` next to the dump | same | 24 h | |
| Recording bytes (`/data/sessions/<id>/*.pcm`) and notes (`/capture/outbox`) | Velero `offsite-data` at 04:00 UTC (Kopia → Backblaze B2), 30 days; `offsite-monthly` 1 year | B2 | 30 d / 1 y | ≤ 24 h | ~15 min + the Velero restore |
| Database, continuous | CNPG barman: base backup 02:00 + WAL | SeaweedFS **on the NAS only** | open-ended | minutes (point in time) | hours, and it is the shared `postgres-cluster` |

- The backup job runs at 03:00 so its output is in the namespace **before** Velero's 04:00 run
  carries it offsite. That is what gives the database an offsite copy; barman's bucket is on the
  same NAS as the audio.
- Lab (`jarvis-live-lab`) is **not backed up**: its job is suspended and Velero skips it.
- The dump and the audio are separate copies taken up to an hour apart. The manifest is what
  makes that safe: `restore-session` checks every recording against it and refuses a session
  whose recordings are missing, short, or different. A recording that kept growing after the
  manifest was taken (a live session) still verifies, because only the first *size* bytes are
  compared.
- A failed backup sends a Gotify notice (**Jarvis backup failed**, priority 8). One that never ran
  is caught by the server: `/healthz` shows `backup.stale`, and the daily retention sweep sends
  **Jarvis: backup is stale** once the last good backup is over 36 h old.

The targets above are what the design promises. The measured times are in the restore log
(§6): fill in a row after every real restore.

## 2. Before you start

- `kubectl` pointed at k3s2: `export KUBECONFIG=~/.kube/k3s2.yaml`. `velero` on PATH.
- Know the session id (the web dashboard's URL, `/sessions/<id>`) and roughly when it existed.
  A session in **Recently deleted** needs no restore at all: use *Restore* in the app, or
  `POST /v1/sessions/<id>/restore`. Everything below is for a session that is gone for good
  (purged from the trash after 30 days, or lost with the disk).
- Pick the backup: the newest one **before** the session was purged.
  ```bash
  kubectl -n jarvis-live exec deploy/jarvis-live -- ls /backups/db /backups/audio
  velero backup get | grep offsite-data
  ```
  The dump and manifest share a stamp (`20261030T030000Z`). Use a Velero backup taken **after**
  that stamp, so the audio is at least as new as the manifest.

## 3. Restore one session (into a scratch namespace first)

Never restore straight into live as the first step. The scratch namespace holds a Velero copy of
the volumes and a throwaway database, and you copy the one session across.

```bash
# 3.1 A scratch copy of the namespace's volumes (production is untouched, no Flux suspend needed;
#     the same shape as velero-restore-runbook.md §3).
velero restore create jarvis-live-restore-$(date -u +%Y%m%d-%H%M) \
  --from-backup <velero-offsite-data-...> \
  --include-namespaces jarvis-live \
  --namespace-mappings jarvis-live:jarvis-live-restore-test \
  --wait
kubectl -n jarvis-live-restore-test get pvc

# 3.2 A scratch Postgres in that namespace, for the dump to be loaded into.
kubectl -n jarvis-live-restore-test run scratch-pg --image=postgres:18 \
  --env=POSTGRES_PASSWORD=scratch --port=5432
kubectl -n jarvis-live-restore-test wait --for=condition=Ready pod/scratch-pg --timeout=120s
```

Then run the restore from a pod of the **server image** (it has `pg_restore` and the CLI), with
the restored volumes mounted. Save as `restore-pod.yaml`, replacing `<image>` with the live
deployment's image (`kubectl -n jarvis-live get deploy jarvis-live -o jsonpath='{..image}'`):

```yaml
apiVersion: v1
kind: Pod
metadata: {name: restore, namespace: jarvis-live-restore-test}
spec:
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001}
  containers:
    - name: restore
      image: <image>
      command: ["sleep", "3600"]
      env:
        - {name: JARVIS_LIVE_DATABASE_URL, value: "postgresql+asyncpg://postgres:scratch@scratch-pg:5432/postgres"}
      volumeMounts:
        - {name: data, mountPath: /data, readOnly: true}
        - {name: outbox, mountPath: /capture, readOnly: true}
        - {name: backups, mountPath: /backups, readOnly: true}
        - {name: out, mountPath: /out}
  volumes:
    - {name: data, persistentVolumeClaim: {claimName: jarvis-live-data}}
    - {name: outbox, persistentVolumeClaim: {claimName: jarvis-live-outbox}}
    - {name: backups, persistentVolumeClaim: {claimName: jarvis-live-backups}}
    - {name: out, emptyDir: {}}
```

```bash
kubectl apply -f restore-pod.yaml
kubectl -n jarvis-live-restore-test exec restore -- jarvis-live migrate     # the target schema
# 3.3 Restore the one session into /out (a directory) and a second database:
kubectl -n jarvis-live-restore-test exec restore -- jarvis-live restore-session <session-id> \
  --dump /backups/db/<stamp>.dump --manifest /backups/audio/<stamp>.manifest.json \
  --audio-src /data --outbox-src /capture \
  --scratch-url postgresql+asyncpg://postgres:scratch@scratch-pg:5432/scratch \
  --target-data /out/data --target-outbox /out/outbox
```

(Create the `scratch` database first: `kubectl exec scratch-pg -- createdb -U postgres scratch`.)

It prints the rows restored and `audio: N files, verified against the manifest`. It refuses, and
writes nothing, if the session is not in the dump, the dump is unreadable, or any recording does
not match the manifest.

## 4. Check it, then put it back

```bash
# the bytes the recording had:
kubectl -n jarvis-live-restore-test exec restore -- sha256sum /out/data/sessions/<id>/*.pcm
# compare with the manifest's entries for that session:
kubectl -n jarvis-live exec deploy/jarvis-live -- python -c \
  "import json,sys; [print(e['sha256'], e['name']) for e in json.load(open('/backups/audio/<stamp>.manifest.json'))['audio'] if e['session_id']=='<id>']"
```

Once the scratch result is right, put the session into live. Files first, then rows, so the
session never shows without its audio:

```bash
# 1. The recordings and the note, into the live volumes (the app's pod mounts them read-write):
POD=$(kubectl -n jarvis-live get pod -l app=jarvis-live -o name | head -1)
kubectl -n jarvis-live-restore-test exec restore -- tar -C /out/data/sessions -cf - <id> \
  | kubectl -n jarvis-live exec -i "$POD" -- tar -C /data/sessions -xf -
kubectl -n jarvis-live-restore-test exec restore -- tar -C /out/outbox -cf - . \
  | kubectl -n jarvis-live exec -i "$POD" -- tar -C /capture/outbox -xf -

# 2. The rows, straight into the live database (reachable from the scratch namespace as
#    postgres-cluster-rw.postgres.svc; the URL is in live's secret; do not echo it):
LIVE_URL=$(kubectl -n jarvis-live get secret jarvis-live-db-app -o jsonpath='{.data.connection}' | base64 -d)
kubectl -n jarvis-live-restore-test exec restore -- env LIVE_URL="$LIVE_URL" sh -c \
  'jarvis-live restore-session <id> --dump /backups/db/<stamp>.dump \
     --manifest /backups/audio/<stamp>.manifest.json --audio-src /data --outbox-src /capture \
     --scratch-url postgresql+asyncpg://postgres:scratch@scratch-pg:5432/scratch \
     --target-url "$LIVE_URL" --target-data /out/data2 --target-outbox /out/outbox2'
```

`restore-session` refuses to replace a session that already exists unless you pass `--force`.
Its `--target-data` is only a staging directory here, since the files were copied in step 1.

Then open the session in the dashboard and play the audio. If it was in the trash it comes back
there (`deleted_at` is preserved): use Restore in Recently deleted. Finally
`kubectl delete ns jarvis-live-restore-test`.

## 5. The bigger cases

- **The whole database** (corruption, a bad migration): CNPG point-in-time recovery from barman.
  See np-home-homelab `docs/postgres-cnpg.md` §Backups. It is the shared `postgres-cluster`, so
  that is a cluster-level operation; for one app's data, prefer the nightly dump:
  `pg_restore --clean --if-exists --no-owner -d <new database> db/<stamp>.dump`.
- **The data or outbox volume** (disk loss, a deleted PVC): Velero, np-home-homelab
  `docs/velero-restore-runbook.md` §2 (suspend Flux, `velero restore create`, resume). The PVCs
  carry `kustomize.toolkit.fluxcd.io/prune: disabled` so a path change cannot delete them, but
  the storage class's reclaim policy is `Delete`: deleting a PVC deletes the data.
- **The NAS is gone**: the offsite copy is B2 (Velero's `backblaze` location), which holds the
  audio, the outbox, and the nightly dumps and manifests. Barman's WAL is on the NAS only.

## 6. Restore log

One row for every real restore (and drill). Measured RTO is wall-clock from "decided to restore"
to "audio plays in the app".

| Date (UTC) | By | Session | Backup (dump / Velero) | Scratch or live | Result | Measured RTO |
|---|---|---|---|---|---|---|
| | | | | | *No real restore performed yet. The first row is the drill that completes the plan's definition of done; the automated equivalent is `tests/test_backup.py::test_a_soft_deleted_session_is_restored_from_a_backup`, which runs on every CI build.* | |

## 7. If the backup job fails

- **Jarvis backup failed** on Gotify: the message says why. Common causes: the backups volume
  full or unmounted, the database unreachable, `pg_dump` refused (a server newer than the
  client: the image's `POSTGRES_CLIENT_MAJOR` build arg must match the CNPG major, 18).
  `kubectl -n jarvis-live logs job/<latest jarvis-live-backup job>` has the detail.
- **Jarvis: backup is stale**: the job did not run at all. `kubectl -n jarvis-live get cronjob
  jarvis-live-backup` (suspended? `LAST SCHEDULE`?) and the pod events. Run one now:
  `kubectl -n jarvis-live create job --from=cronjob/jarvis-live-backup backup-now`.
