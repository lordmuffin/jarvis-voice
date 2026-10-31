<!-- synced from TDD-FRAMEWORK.md on 2026-10-12; edit the source, not this copy -->
# TDD Framework — Agent & Human Operating Prompt

> Drop this file into any repo as `TDD.md` and reference it from `CLAUDE.md` / `AGENTS.md`:
> `Follow TDD.md for every change. No exceptions without an explicit waiver from Andrew.`

---

## 1. Your role

You are working in a repository that uses strict test-driven development. Every behavioral change starts with a failing test and ends with a passing test suite. You do not write production code, manifests, or prompts without a test that demands them. If you can't write a test first, stop and say why before changing anything.

"Production artifact" means whatever this repo ships: application code, scripts, Kubernetes/Flux manifests, Helm values, policies, prompts, skills, or templates.

---

## 2. The loop (non-negotiable)

For every unit of change, run this loop and **show the evidence** of each gate:

1. **RED** — Write the smallest test that describes the next behavior. Run it. Paste the failing output. It must fail *for the expected reason* (assertion failure, not import error or typo).
2. **GREEN** — Write the minimum change that makes it pass. Run the **full** fast suite. Paste the passing summary.
3. **REFACTOR** — Improve names, structure, duplication with tests green. Re-run. No new behavior in this step.
4. **COMMIT** — One loop = one commit where practical. Message format: `test: <behavior>` + `feat|fix|refactor: <behavior>`, or a single commit with both if tiny.

Rules:
- Never edit a test and the code it covers in the same GREEN step to make things pass. If a test is wrong, fix it in its own RED step with a stated reason.
- Never delete, skip, or weaken a test to get green. Flag it instead.
- Never mark work done with a failing, skipped, or `xfail` test unless Andrew approved it in the conversation.
- If a step needs more than ~20 lines of production change, the test was too big. Split it.

---

## 3. What a "test" is, by repo type

Detect the repo type(s) from its contents. Many repos are mixed — apply every section that matches.

### 3a. Application code (Python, TypeScript/JS, Go, shell)
| Language | Runner | Location |
|---|---|---|
| Python | `pytest` (+ `pytest-cov`) | `tests/` mirroring `src/` |
| TS/JS | `vitest` (or existing `jest`) | `*.test.ts` beside source |
| Go | `go test` | `*_test.go` beside source |
| Shell | `bats-core` + `shellcheck` | `tests/*.bats` |

- Unit tests first; integration tests for I/O boundaries (HTTP, DB, MCP servers, filesystem).
- External services are faked at the boundary, never mocked deep inside logic.
- Test names state behavior: `test_returns_empty_list_when_vault_has_no_inbox_items`.

### 3b. GitOps / Infrastructure (Kubernetes, Flux, Kustomize, Helm, Terraform)
Tests are executable checks against rendered manifests. The test pyramid here:

1. **Render** — every overlay must build: `kustomize build <path>` / `helm template`.
2. **Schema** — `kubeconform -strict` with CRD schemas (Flux, Traefik, CNPG, cert-manager, etc.).
3. **Policy** — `conftest` (Rego) in `policy/`. Baseline policies:
   - no `:latest` image tags
   - containers have resource requests/limits
   - no plaintext `kind: Secret` (must be SOPS-encrypted — check for `sops:` metadata)
   - every `HelmRelease` pins a chart version
   - IngressRoutes use the expected entrypoints/TLS resolver
4. **Flux** — `flux-local test` / `flux-local diff` for Kustomization + HelmRelease resolution.
5. **Smoke (post-deploy, optional)** — a script that asserts the thing is reachable/healthy.

TDD in GitOps looks like: *write a policy or assertion describing the desired state → it fails → add/change manifests → it passes.*
Example: adding an app → first write `tests/apps/<app>_test.rego` asserting a HelmRelease named `<app>` exists in namespace `<ns>` with chart version pinned → RED → add manifests → GREEN.

Terraform/OpenTofu: `tofu validate`, `tflint`, `tofu test` (native `.tftest.hcl`) or `checkov`.

### 3c. Prompt, skill, and notes repos
Prompts are code. Their tests are **evals**.

Layout per prompt or skill:
```
skills/<name>/
  SKILL.md
  evals/
    cases.yaml        # inputs + expected properties
    triggers.yaml     # should-trigger / should-not-trigger phrases (skills only)
```

Eval tiers, cheapest first — always run tiers 1–2, run tier 3 on changed prompts only:
1. **Lint (deterministic, free)** — frontmatter schema valid (`name`, `description` present, description < 1024 chars, no angle brackets), required sections present, internal `[[links]]` resolve, no secrets.
2. **Structural assertions (deterministic)** — run the prompt, assert on output: contains / not-contains, regex, JSON schema, max length, required headings, file written to correct path.
3. **Model-graded (costs tokens)** — `llm-rubric` assertions for tone, correctness, refusal behavior. Use `promptfoo` as the runner (`promptfooconfig.yaml` at repo root, `promptfoo eval`).

TDD for a prompt: *write the eval case that the current prompt fails (e.g., "when inbox has a voice transcript, output routes it to `Recording Transcripts/`") → run, see it fail → edit the prompt → run, see it pass, confirm no other case regressed.*

Every bug report against a prompt becomes an eval case before the prompt is touched.

Notes-only content (no executable behavior) is exempt from RED/GREEN but must pass tier-1 lint.

---

## 4. Standard repo contract

Every repo exposes the same entry points, so agents and CI never guess:

```
make test        # fast suite: unit + lint + render + schema + policy + tier-1/2 evals  (< 2 min target)
make test-all    # everything incl. integration, flux-local, tier-3 evals
make lint        # formatters + linters only
```
(Use `just` or `task` if the repo already does; keep the same verbs.)

Also required:
- `.pre-commit-config.yaml` running `make lint` and the fastest tests.
- CI (GitHub Actions) running `make test` on every PR; `make test-all` on main and nightly.
- `TESTING.md` (or a section in README) listing what's tested, how, and known gaps.

---

## 5. Bug-fix protocol

1. Reproduce the bug as a failing test. Paste the failure.
2. Fix. Paste the pass.
3. Ask: "What test would have caught the *class* of this bug?" Add it if cheap (often a policy or lint rule).

---

## 6. Definition of done

A change is done only when all are true — report each explicitly:
- [ ] Test(s) written first; RED output shown
- [ ] `make test` green; output shown
- [ ] No skipped/xfail/deleted tests without approval
- [ ] Coverage did not drop below the ratchet (see §7)
- [ ] `TESTING.md` updated if a new test type or gap was introduced

---

## 7. Migration playbook — converting existing repos

Do not try to retrofit everything at once. Legacy code earns tests when it's touched or when it's risky.

### Phase 0 — Inventory (one session, all repos)
Produce `tdd-inventory.md` with one row per repo:

| Repo | Type(s) | Existing tests | Has `make test`? | CI? | Risk (H/M/L) | Change frequency | Priority |

Priority = risk × change frequency. The homelab GitOps repo and anything touching secrets, auth, or data deletion are High risk by default.

### Phase 1 — Harness (per repo, ~1 hour each)
Install the runner(s), add `make test`/`make lint`, pre-commit, and CI. Add **one** trivial passing test so the pipeline is proven. No behavior changes. Merge.

### Phase 2 — Baseline safety net (per repo)
Add cheap, broad, deterministic checks that cover everything at once:
- Code: smoke tests that import every module / run every CLI with `--help`.
- GitOps: render + kubeconform + baseline conftest policies across all overlays. Fix or explicitly waive (in `policy/exceptions.yaml`, with a reason) every existing violation.
- Prompts/skills: tier-1 lint across all files; one `triggers.yaml` per skill with 3 should / 3 should-not phrases.

### Phase 3 — Characterization tests (touch-driven)
Before modifying any untested legacy unit, first write tests that pin its **current** behavior (even if that behavior looks wrong — name those tests `test_current_behavior_...` and note them in `TESTING.md`). Then apply the normal RED/GREEN loop for the actual change. This is the main engine of migration.

### Phase 4 — Ratchet
- Record current coverage per repo in `.coverage-baseline` (or the CI config). CI fails if coverage drops below it.
- Raise the baseline whenever coverage rises. Never lower it without approval.
- For prompt repos, the ratchet is "number of skills with ≥ N eval cases" and "eval pass rate."

### Phase 5 — Steady state
All new work follows §2. Monthly: review `TESTING.md` gaps, pick the top-risk untested area, add characterization tests.

### Rollout order across repos
1. One pilot repo per type (suggested: homelab GitOps, one Python/TS tool, the skills/prompts repo). Tune this file based on what hurt.
2. Remaining High-priority repos.
3. Everything else, Phase 1–2 only; Phase 3 happens naturally as they're touched.
4. Archived/dormant repos: skip unless reactivated.

### Agent instructions when asked to "migrate this repo to TDD"
1. Detect repo type(s); report what testing exists.
2. Propose the Phase 1 harness changes as a plan; wait for approval.
3. Implement Phase 1, run it, show green CI-equivalent output locally.
4. Implement Phase 2; list every waiver added and why.
5. Write `TESTING.md` with coverage numbers and the top 5 untested risk areas.
6. Stop. Do not start Phase 3 refactors unprompted.

---

## 8. When to push back

Say so and ask before proceeding if:
- A requested change has no testable behavior (and isn't pure notes/docs).
- The only way to test something is a slow or flaky end-to-end check — propose a seam instead.
- An existing test appears to encode a bug — surface it, don't silently "fix" it.
- Model-graded evals would cost more than ~$1 per run — propose a deterministic alternative or a smaller case set.
