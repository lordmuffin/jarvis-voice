# Standard repo contract (TDD.md §4): make test (fast) / make test-all (everything) / make lint.
# Component commands stay as documented in CLAUDE.md; this wraps them.

.DEFAULT_GOAL := test
LEGACY_PY ?= python3
RATCHET := python3 scripts/tdd/ratchet.py

.PHONY: help deps tools test test-all lint lint-server lint-web lint-workflows \
	test-server test-legacy test-web test-policy \
	test-server-full test-legacy-cov test-web-cov test-e2e test-android test-macos ratchet

help:
	@echo "make test      fast suite: lint + unit tests + render/schema/policy (no Docker needed)"
	@echo "make test-all  everything: + Postgres integration, coverage/eval ratchets, Playwright, Android, Swift"
	@echo "make lint      formatters and linters only"
	@echo "make deps      install dependencies (uv sync, npm ci, legacy requirements)"

deps:
	cd server && uv sync --frozen
	cd web && npm ci
	$(LEGACY_PY) -m pip install -r requirements.txt -r requirements-dev.txt

tools:
	scripts/tdd/install-tools.sh

# ---- lint -------------------------------------------------------------------------------
lint: lint-server lint-web lint-workflows

lint-server:
	cd server && uv run ruff check . && uv run ruff format --check . && uv run mypy src

lint-web:
	cd web && npx tsc --noEmit

lint-workflows:
	uvx --from actionlint-py actionlint .github/workflows/*.yml

# ---- fast suite -------------------------------------------------------------------------
test: lint test-server test-legacy test-web test-policy

# `integration` is added automatically to every test that needs Postgres (server/tests/conftest.py)
test-server:
	cd server && uv run pytest -q -m "not integration"

test-legacy:
	$(LEGACY_PY) -m pytest -q tests/

test-web:
	cd web && npm test

test-policy: tools
	scripts/tdd/check-deploy.sh

# ---- everything -------------------------------------------------------------------------
test-all: test test-server-full test-legacy-cov test-web-cov test-e2e test-android test-macos ratchet

# Needs Docker, or JARVIS_LIVE_TEST_DATABASE_URL pointing at a Postgres.
test-server-full:
	cd server && uv run pytest -q --cov --cov-report=json:coverage.json

test-legacy-cov:
	$(LEGACY_PY) -m pytest -q tests/ --cov=src/jarvis_voice --cov-branch --cov-report=json:coverage.json

test-web-cov:
	cd web && npx vitest run --coverage

test-e2e:
	cd web && npm run build && npx playwright test

test-android:
	@if command -v java >/dev/null 2>&1 && [ -n "$$ANDROID_HOME$$ANDROID_SDK_ROOT" ]; then bash download-models.sh --no-model && cd android && ./gradlew testDebugUnitTest --no-daemon; \
	else echo "SKIP android: needs a JDK and ANDROID_HOME (CI runs it)"; fi

test-macos:
	@if [ "$$(uname)" = Darwin ]; then cd macos/JarvisLiveKit && swift test; \
	else echo "SKIP macos: not on macOS (macos-ci runs it)"; fi

# CI fails if any of these drop below the recorded baseline. Raise with: make ratchet RATCHET_FLAGS=--update
ratchet:
	$(RATCHET) server/.coverage-baseline server/coverage.json:totals.percent_covered $(RATCHET_FLAGS)
	$(RATCHET) .coverage-baseline coverage.json:totals.percent_covered $(RATCHET_FLAGS)
	$(RATCHET) web/.coverage-baseline web/coverage/coverage-summary.json:total.lines.pct $(RATCHET_FLAGS)
	$(RATCHET) server/.eval-baseline "$$(cd server && uv run pytest tests/evals --co -q 2>/dev/null | grep -c '::')" $(RATCHET_FLAGS)
