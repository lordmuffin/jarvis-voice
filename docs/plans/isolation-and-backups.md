# Plan: isolation, reversible deletes, recoverable recordings

Status: **approved 2026-10-30 with decisions D1–D5 below. Implementation in progress.**
Branch: `feat/isolation-and-backups`. Scope: `server/`, `web/`, `android/`, `server/deploy/k8s/`, `docs/`.

Read for this plan: `CLAUDE.md`, `TDD.md`, `TESTING.md`, `server/src/jarvis_live/{db/models,users,oidc,auth,retention,config,app,cli}.py`,
every router in `api/`, `workitems/service.py`, `publish/service.py` (queue), `finalize/{outbox,finalizer}.py`, `copilot/loop.py`,
`vault/index.py`, `ingest/hub.py`, migrations `0001`–`0009`, `server/deploy/k8s/base/*`, and, from np-home-homelab,
`apps/storage/{postgres-cluster,velero}/` plus `docs/postgres-cnpg.md` and `docs/velero.md`.

## 0. Things I found that change the brief

You said not to re-litigate the stated context. These are new facts, not disagreements, but two of them change the work.

1. **Backups already exist one layer down.** Nothing in *this repo* backs anything up, as you said. But in np-home-homelab:
   - Velero's `offsite-data` schedule (daily 04:00, 30-day TTL) and `offsite-monthly` (1st of month, 1-year TTL) both include the
     `jarvis-live` namespace. The `jarvis-live-data` PVC (audio, outbox) goes to Backblaze B2 via Kopia file-system backup.
     `jarvis-live-lab` is excluded on purpose.
   - Postgres is covered by CNPG/barman: nightly base backup at 02:00 plus continuous WAL, to SeaweedFS on the TrueNAS box.
     **That is the same NAS as the audio PVC and it has no offsite copy.** Velero deliberately skips `PG_DATA`.
   - So today the audio has an offsite copy up to ~24 h old, and the database has only an on-NAS copy. What is missing is
     (a) a database copy that survives losing the NAS, (b) restoring *one session* instead of a whole namespace or cluster,
     (c) any alert that is about *this app's* data, and (d) a measured RPO/RTO.
   - Section 5 is designed around this rather than around a blank slate. Decision D1 below is yours.
2. **There is no delete today.** `api/sessions.py` has no `DELETE`, and neither do web or android. "Delete hides the row" means
   adding delete *and* soft delete together. That is a new API verb, so purely additive (no migration beyond the column).
3. **A real cross-user leak exists on a read path** (section 1): the copilot's "related notes" search runs over the *owner's* Obsidian
   vault for every user's session and stores the hits in that user's copilot output. This is the one finding the test suite
   would have caught, and it is not a missing `WHERE user_id`.

## 1. Isolation audit

How access works today: every `Session` has `user_id` (copied from the device at creation). `_visible()` in `api/sessions.py` returns
404 unless the caller owns the session **or is an admin**; `_owned()` is owner-only. Admins seeing everything is documented in
`CLAUDE.md`/`models.py` and I treat it as intended. The audit therefore asks: can a *non-admin* cross, and is an *admin*
allowed to do more than read?

| Read/write path | Where | Scoping today | Verdict |
|---|---|---|---|
| Session list | `GET /v1/sessions` | `user_id == caller`; `all`/`user_id` params 403 for non-admin | OK |
| Session detail (segments, markers, copilot, final note, audio flags) | `GET /v1/sessions/{id}` | `_visible` → 404 | OK |
| Processing state | `GET .../processing` | `_visible` | OK |
| Audio bytes | `GET .../audio/{ch}.wav` | `_visible` → 404 | OK |
| Transcript | in detail (`SegmentRow` filtered by `session_id` only, after `_visible`) | gated by the session check | OK, but nothing at the query level; a future endpoint that forgets the check leaks |
| Copilot output | in detail; `CopilotItem` by `session_id` | gated by session check | OK |
| **Copilot "related" (vault search)** | `copilot/loop.py:147`, `vault/index.py` | **none**: one shared index of the owner's vault, searched for every user | **LEAK**: another user's session shows snippets/paths from the owner's private notes |
| Final note read | detail; path from the session's own `final_note` item, `read_note` limited to 2 path parts | OK; paths come from the DB row, not the client | OK |
| Final note write | `PUT .../note` | `_visible` (so **admin can overwrite another user's note**) | Decision D3 |
| Rename | `PATCH .../{id}` | `_visible` (admin can rename; matches the stated rule) | OK per docs |
| Regenerate / process | `POST .../regenerate`, `.../process` | `_visible` (admin can re-run another user's LLM/STT job) | Decision D3 |
| Publication get/act | `/sessions/{id}/publication*` | `_visible`; the service also needs `can_publish` (owner/admin only) | OK |
| Publishing queue | `GET /v1/publishing` | only owner/admin sessions, only owner/admin callers | OK (admins see each other's by design) |
| Work items | `workitems/service.py` `_own()` filters `user_id`, 404 | OK; `for_session`/`for_user` filter too | OK |
| Stream ticket + WebSocket | `POST .../ticket`, `WS .../stream` | ticket issuance via `_visible`/`_owned`; ticket is bound to session id | OK. The socket does not re-check ownership at connect (it trusts the ticket); fine, but must re-check `deleted_at` after soft delete |
| Notify (Gotify) | `finalizer._notify_owner` | only sends for `OWNER_USER_ID` sessions | OK; global STT-outage and dispatch pushes carry no user content. No test pins it |
| Devices | `/v1/devices`, revoke | `user_id` filter; test exists (`test_someone_elses_device_is_not_yours_to_revoke`) | OK |
| Admin user list | `/v1/admin/users` | `require_admin`; test exists | OK |
| `POST /v1/homelab/ask` | `api/homelab.py` | any signed-in user; touches no per-user data, but it is the owner's homelab capability | Decision D4 |
| Upload retry | `POST /v1/sessions/upload` with `client_id` | lookup is `(user_id, client_id)` | OK |
| Search | no user-facing search endpoint exists; the only "search" is the vault one above | | covered by the LEAK row |

Fix for the leak: the vault index is the *owner's* personal vault, so related-notes search runs only for sessions whose owner is
`OWNER_USER_ID` (or whose user is flagged to use it). Other users get no `related`. This is a one-line gate at the call site plus a
test, with no schema change. (An alternative, a per-user vault, is out of scope.)

### Tests

New `server/tests/test_isolation.py` (integration, uses the existing `server`/`oidc_server` fixtures and `new_device`). Two
non-admin users A and B, plus one admin. One parametrised table drives every route in the matrix above, each as B against A's
session, asserting **status 404 (or 403 for admin-only routes), and that the body contains none of A's data** (not 200-with-empty):

- `GET` detail, processing, audio wav (with and without `Range`), ticket (viewer and producer), publication
- `PATCH` rename, `PUT` note, `POST` end / regenerate / process / publication / publication/merge / close / skip, `DELETE` skip
- work items: list for session, get, patch, approve, unapprove, discard, restore, done, reopen, merge (with A's id in the body),
  prompt, copied, events, dispatch, continue, cancel
- list: B's list never contains A's id; `?all=true` and `?user_id=A` are 403 for B
- WebSocket: B cannot redeem a ticket for A's session
- a "route inventory" test that walks `app.routes` and fails if a new route taking `{session_id}` or `{item_id}` is not in the
  matrix, so coverage cannot silently rot
- related-notes: with a fake vault, B's copilot cycle persists no `related` items; the owner's does
- notify: a finished session of B does not call the notifier; the owner's does
- admin positive controls: admin can read A's session (pins the documented behaviour)
- once soft delete lands, the same matrix runs against a **soft-deleted** session and the **restore** and **recently-deleted**
  routes, so deleted data is not a side door

### Tenant layer above users: recommendation

**Not now. User scoping is sufficient.** Reasons: the deployment is single-household (one owner, a few family users, one admin
group); the only cross-user capability is admin read, which a tenant layer would not remove; and the missing piece was never
the ownership model but enforcement coverage, which the route-inventory test addresses. A `tenant_id` column would touch
every table, every query and the unique index `uq_sessions_user_client_id` for no present benefit.
Revisit when (a) a second, unrelated group is hosted on the same deployment, or (b) an admin must be prevented from reading a
group's data. If that happens, the cheaper path is Postgres row-level security keyed on a per-request setting, added later without
changing the API. Not building it now.

## 2. Soft delete

**Migration `0010_session_soft_delete`**: `sessions.deleted_at timestamptz NULL`, `sessions.purge_after timestamptz NULL`
(a per-session "delete after N days" target; see section 3), and a partial index `ix_sessions_user_deleted` on
`(user_id, deleted_at DESC) WHERE deleted_at IS NOT NULL`. Downgrade drops them. Existing rows get NULL, so nothing changes for
current data. `models.py` gets the two columns.

API (additive; existing routes keep their response shapes, `SessionOut` gains optional `deleted_at` and `purge_after`, null for live rows):

| Route | Behaviour |
|---|---|
| `DELETE /v1/sessions/{id}` | owner-only (`_owned`), 204. Sets `deleted_at = now()`. Idempotent. **409 if the session is `live` or `finalizing`** (end it first), so an in-flight ingest is never orphaned. Audio, transcript, notes untouched |
| `POST /v1/sessions/{id}/restore` | owner-only, restores one session; 404 if not deleted; clears `deleted_at` and `purge_after` |
| `GET /v1/sessions?deleted=true` | the caller's soft-deleted sessions, newest-deleted first (admins: `all`/`user_id` as today). Default list excludes deleted |
| `PATCH /v1/sessions/{id}` | accepts `delete_after_days: int \| null` (1–3650) to set `purge_after`, null clears it. See §3 |

A deleted session is **hidden everywhere else**: default list, detail (404 unless fetched with the restore flow's
`?deleted=true` list), audio, ticket, WebSocket connect, work-item lists, publishing queue, the publish reconciler, and
`GET /v1/admin/users` session counts. The detail route returns the session only to its owner when `deleted_at` is set and `?include_deleted=true`
is passed, so the "Recently deleted" screen can preview it. The publish reconciler and auto-publish skip deleted sessions.
A deleted session's `client_id` still blocks re-upload of the same `client_id`: the retry returns the existing id with 202 as
today (idempotent), and the client sees it in "Recently deleted". Restore is the way back.

Web: "Recently deleted" view under Sessions (list with deleted-on date and "purges on" date, Restore button, delete action on a
session page with confirm). All from `web/src/ds` / Instrument components and tokens, no local components. Android: delete
and a "Recently deleted" screen reachable from `SessionsActivity`, restore action, via `LiveApiClient`/`RemoteSessions`.
Paparazzi snapshots re-recorded for the changed screens.

Tests (server, integration): delete hides from list/detail/audio/ticket; audio file and segments still on disk/in DB after delete;
restore round-trip; 409 on live; idempotent delete; cross-user delete and restore are 404; the deleted list is per-user; admin
`?deleted=true&all=true` works; migration up/down on a populated DB (extends the `test_accounts` migration pattern).
Web: vitest for the list/restore/delete state, Playwright for delete → appears in Recently deleted → restore. Android: unit tests for the client calls and
list model, Paparazzi for the new screens.

## 3. Retention

Extend `retention.py` (currently: unlink `*.pcm` older than N days for `done` sessions, rows kept forever; wired in `app.py` using
`audio_retention_days`, `retention_interval_s`). Three independent behaviours, three settings:

| Setting (env `JARVIS_LIVE_…`) | Default | Meaning |
|---|---|---|
| `AUDIO_RETENTION_DAYS` | 90 (unchanged) | Existing PCM purge for `done` sessions. Untouched logic, own function, own test |
| `TRASH_RETENTION_DAYS` (new) | 30 | A soft-deleted session is hard-deleted this long after `deleted_at` |
| per-session `purge_after` | none | Set via `delete_after_days`: at `purge_after` the session is soft-deleted (not hard-deleted), then follows the trash window |

Rules:
- Hard delete removes, in one pass: `copilot_items`, `segments`, `markers`, `audio_chunks`, `work_items` (cascade covers events and
  dispatches), `note_publications` (cascades), the session row, and `data_dir/sessions/<id>/` plus the outbox note file(s) the
  session's `final_note` items point at. Order: DB rows first in one transaction, files after commit, so a crash leaves
  orphan files (cleaned by the next sweep) rather than a row pointing at nothing. The sweep also removes session directories with no row.
- A session with an **open publication PR** is not hard-deleted; the sweep logs it and tries again next run (the PR is the user's record).
- `purge_after` is evaluated by the same sweep, so one loop does: auto-soft-delete due sessions → hard-delete expired trash → existing PCM purge.
- Interval stays `retention_interval_s`. The sweep logs counts and sends a Gotify notice only on failure.
- Setting `TRASH_RETENTION_DAYS=0` hard-deletes on the next sweep; documented as "no trash".
- The manifest default goes in `server/deploy/k8s/base/configmap.yaml` in the same PR as the code (repo rule).

Tests: injectable `now` (already the pattern), cases at 29/30/31 days, `purge_after` in past/future, live session never
purged, open-PR session skipped, files and rows both gone, orphan-dir cleanup, the PCM purge unchanged (existing behaviour pinned first), crash between DB commit and unlink
(simulated) leaves a state the next sweep finishes.

## 4. Isolation of the delete path

Covered in §1's matrix: delete, restore and the deleted list are in the cross-user table from the start.

## 5. Backups

### Current coverage, for the RPO/RTO table

| Data | Mechanism (np-home-homelab) | Copies | RPO | RTO |
|---|---|---|---|---|
| Postgres `jarvis_live` | CNPG barman: nightly base + WAL, SeaweedFS on the NAS | 1 (NAS only) | ~minutes (WAL) | hours: restore whole `postgres-cluster` or PITR, affects every app |
| Audio PVC `jarvis-live-data` | Velero + Kopia, daily 04:00 → B2, 30 d; monthly 1 yr | 1 offsite + live | ≤ 24 h | hours: restore the PVC, whole namespace granularity |
| Both, per session | none | | | no way to restore one deleted session without a full restore |

Both layers are crash-consistent only. A Velero audio copy and a barman DB copy taken hours apart can disagree (rows for audio that
the copy lacks). That mismatch is the thing a per-session restore must tolerate and report.

### What this repo adds

A `jarvis-live backup` / `jarvis-live restore-session` pair in `cli.py`, implemented in a testable module `jarvis_live/backup.py`,
run from a CronJob using the **server image** (so version-matched with the schema). Needs `postgresql-client` added to
`server/Dockerfile` (it only has ffmpeg today).

**Backup job (`cronjob-backup.yaml`, base/, both channels' manifests share it; lab gets it disabled; see D2):**
1. `pg_dump -Fc` of `jarvis_live` to a new PVC `jarvis-live-backups`, as `db/<UTC timestamp>.dump`.
2. An **audio manifest** next to it: `audio/<ts>.manifest.json` listing every `sessions/<id>/*.pcm` with size and sha256 (mtime-stable files only; a file still growing is recorded with the size at scan time). This is the cheap, consistent link between the DB dump and the audio copy.
3. Audio itself: **recommended to rely on Velero** for the bytes, since it already ships `jarvis-live-data` offsite. The new PVC also lives in the `jarvis-live` namespace, so Velero carries the dumps and manifests offsite for free (the same pattern as Forgejo's `forgejo-db-dump`). This closes the "database has no offsite copy" gap without a new storage target or secret.
   Alternative (D1): also hardlink-snapshot the audio dir onto the backups PVC for an in-cluster, per-session-restorable copy. It doubles NAS usage and shares the NAS failure domain, so I would not.
4. Prune: keep 7 daily + 4 weekly dumps; the rest deleted by the same job.
5. Success writes `last_success.json`; **any failure** (dump error, empty dump, manifest mismatch, prune error) → Gotify at priority 8 via the existing `GotifyNotifier` (token already mounted at `/secrets/gotify`), title `Jarvis backup failed`, with the reason. A second, independent signal: the app's `/healthz` detail or a Prometheus-visible age of `last_success.json` (D5), so a job that never ran at all also alerts. The CronJob's own failure is not enough to tell you the schedule was suspended.

**Restore (`jarvis-live restore-session <id> --from <dump> --data-src <dir> --to-database <url> --to-data <dir>`):**
`pg_restore` into a *scratch* database, then copy only that session's rows (all tables in §3's list, in FK order) and its
`sessions/<id>/` audio files plus outbox note into the target, refusing to overwrite an existing id unless `--force`. It verifies
every audio file against the manifest sha256 and reports size/hash mismatches rather than silently succeeding. A soft-deleted
session restores with `deleted_at` set (it is then restored in the app normally), which is the case you asked about.

**RPO / RTO targets I would write down** (to be confirmed by the measured restore, §7):
- DB: RPO 24 h from the dump alone (minutes if barman PITR is used instead); RTO for one session < 15 min.
- Audio: RPO ≤ 24 h (Velero); RTO for one session < 15 min if the Velero PVC is restored to a scratch namespace first, hours for the full PVC.
- Offsite: DB dump and audio both reach B2 within one Velero cycle (≤ 24 h) after the dump runs, so the dump job must run **before** 04:00 UTC (proposed 03:00).

### Tests

- `test_backup.py` (integration, uses the existing Postgres fixture; CI needs `postgresql-client`, added to the workflow): seed users, sessions, segments, audio files; soft-delete one; run backup; delete the session and its files entirely (simulated disk loss); run `restore-session` into a scratch database and data dir; **assert the restored audio bytes are identical** (sha256 and `==`), the rows are back, `deleted_at` is preserved, and no other session was touched. This is the automated restore test you asked for.
- failure paths each assert a Gotify call: `pg_dump` failing, empty dump file, manifest/dump disagree, backup dir unwritable, prune failure; and that success sends nothing.
- prune keeps exactly the retention set; a corrupted dump is detected on restore (non-zero exit, nothing written).
- restore refuses to clobber an existing id; restore of an id not in the dump fails clearly; audio hash mismatch is reported.
- manifest tests: growing file is recorded with scan-time size; empty dir; id with no row.
- Deploy render checks (`make test`: kustomize, kubeconform, conftest): CronJob has resources, no `:latest`, pinned image, no secret in env; lab must not get a vault write credential (existing Deploy CI check, unchanged); a new check that lab's backup CronJob is suspended (D2).

## 6. Order of work (each a PR, Conventional Commits, `make test` before each)

1. `test(server): cross-user isolation matrix + route inventory` and `fix(server): related-notes search only for the owner`. Red first, then the leak fix.
2. `feat(server): soft delete` — migration `0010`, models, DELETE/restore/list/PATCH, hidden-everywhere changes, isolation matrix extended.
3. `feat(web)` + `feat(android)`: Recently deleted view, delete, restore, per-session "delete after N days". Android re-records Paparazzi. (`web` is linked to `server` in release-please, so 2 and 3 release together.)
4. `feat(server): trash and per-session retention` — `retention.py`, config, configmap.
5. `feat(server): backup and restore-session` — `backup.py`, CLI, Dockerfile, CronJob, PVC, configmap, tests.
6. `docs`: `docs/runbooks/restore.md`, RPO/RTO, `server/deploy/README.md` section. The one real restore is performed and logged here.

PRs 1–2 go first because they are independent of infra; 5 can proceed in parallel with 3–4.

## 7. The real restore, and its limits

`docs/runbooks/restore.md` will have: prerequisites, the per-session procedure, the whole-DB (barman) and whole-PVC (Velero) pointers
to the existing homelab runbooks, verification steps, and a **restore log** table (date, backup used, session, who, result, measured RTO).
The first row is a real restore of one soft-deleted session into a scratch namespace (`jarvis-live-restore-test`, never live or lab).

**I need your go-ahead to run that part**: it needs `kubectl` access to the k3s2 cluster, the ability to create a throwaway
namespace and PVC, and a real backup to exist (so it can only happen after the first scheduled run on a deployed build). I will not touch the live
or lab namespaces. If I cannot reach the cluster from this machine, I will stop and give you the exact commands to run with `!`.

## 8. Decisions (resolved)

D1 Velero carries the audio bytes. D2 backup CronJob suspended on lab. D3 admins read-only except rename (implemented for the note, regenerate and process; publication stays with the publisher rule, because existing behaviour and tests have admins publish the owner's notes). D4 `/v1/homelab/ask` restricted to owner/admins. D5 `/healthz` checks the age of the last successful backup. D6 stays out of scope.

Original questions:

- **D1. Audio backup:** rely on Velero for the bytes (recommended; dumps and manifests ride Velero offsite), or also snapshot audio onto the backups PVC?
- **D2. Lab:** the README says lab data is disposable and unbacked. I propose the backup CronJob ships in `base/` but is `suspend: true` in lab, with the restore test using a scratch namespace instead. OK?
- **D3. Admin writes:** admins can currently overwrite another user's note, regenerate and reprocess. Keep (the docs say "read and rename") or make admin **read-only**, with writes owner-only? I recommend read-only except rename. That is a behaviour change, so it would be called out in the changelog; no schema change.
- **D4. `/v1/homelab/ask`:** open to every signed-in user today. Restrict to owner/admin? Not part of the stated scope; I will only add a test pinning whatever you choose.
- **D5. Missing-run alert:** add an age-of-last-success check to `/healthz` (or just rely on a Gotify failure from the job)? I recommend the check; it is ~20 lines.
- **D6. Offsite for the DB:** this plan gets a dump offsite via Velero. The homelab docs note barman data itself has no offsite copy; fixing that belongs in np-home-homelab and is out of scope here.

## 9. Definition of done (from your brief)

- [ ] This plan approved
- [ ] Server, web and Android tests green in CI (`make test-all`)
- [ ] `docs/runbooks/restore.md` committed
- [ ] One real restore performed and logged in the runbook
