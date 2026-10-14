#!/usr/bin/env bash
# Vendor a pinned Instrument release into an app repo, or check that vendored files are untouched.
#
#   sync-instrument.sh <version> <dest-dir> [platform...]   vendor release v<version> into <dest-dir>
#   sync-instrument.sh --check <dest-dir>                    verify vendored files match their manifest
#
# Platforms: web swift android echarts flat (default: web). Each lands in <dest-dir>/<platform>/.
# <dest-dir> also gets INSTRUMENT_VERSION and .instrument-manifest (sha256 of every vendored file).
#
# Source of the release, in order:
#   INSTRUMENT_LOCAL_DIST=/path/to/dist    use a local dist/ (CI in the instrument repo, testing)
#   gh release download                     if the GitHub CLI is installed and authenticated
#   curl                                    public download URL (needs GITHUB_TOKEN if the repo is private)
# INSTRUMENT_REPO overrides the repo (default lordmuffin/instrument).
set -euo pipefail

REPO="${INSTRUMENT_REPO:-lordmuffin/instrument}"
ASSET="instrument-dist.tar.gz"

die() { echo "sync-instrument: $*" >&2; exit 1; }
sha() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

check() {
  local dest="$1"
  [[ -f "$dest/.instrument-manifest" ]] || die "$dest/.instrument-manifest not found. Run a sync first."
  [[ -f "$dest/INSTRUMENT_VERSION" ]] || die "$dest/INSTRUMENT_VERSION not found."
  local bad=0
  ( cd "$dest" && sha -c --quiet .instrument-manifest ) || bad=1
  # Files added under a vendored platform folder that the manifest doesn't know about.
  while IFS= read -r f; do
    grep -qF "  ./$f" "$dest/.instrument-manifest" || { echo "unexpected file in vendored Instrument: $dest/$f" >&2; bad=1; }
  done < <(cd "$dest" && find . -type f ! -name INSTRUMENT_VERSION ! -name .instrument-manifest | sed 's|^\./||' | sort)
  if (( bad )); then
    echo "Vendored Instrument in $dest was edited by hand. Change the design system instead, then re-sync." >&2
    exit 1
  fi
  echo "Instrument $(cat "$dest/INSTRUMENT_VERSION") in $dest: OK"
}

fetch() {
  local version="$1" out="$2"
  if [[ -n "${INSTRUMENT_LOCAL_DIST:-}" ]]; then
    [[ -d "$INSTRUMENT_LOCAL_DIST" ]] || die "INSTRUMENT_LOCAL_DIST=$INSTRUMENT_LOCAL_DIST is not a directory"
    local got; got="$(tr -d '[:space:]' < "$INSTRUMENT_LOCAL_DIST/VERSION")"
    [[ "$got" == "$version" ]] || die "local dist is v$got, asked for v$version"
    cp -R "$INSTRUMENT_LOCAL_DIST/." "$out/"
    return
  fi
  local dl; dl="$(mktemp -d)"
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh release download "v$version" -R "$REPO" -p "$ASSET" -p "$ASSET.sha256" -D "$dl" >/dev/null
  else
    local base="https://github.com/$REPO/releases/download/v$version" auth=()
    [[ -n "${GITHUB_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
    curl -fsSL "${auth[@]}" -o "$dl/$ASSET" "$base/$ASSET" || die "download failed: $base/$ASSET"
    curl -fsSL "${auth[@]}" -o "$dl/$ASSET.sha256" "$base/$ASSET.sha256" || die "download failed: $base/$ASSET.sha256"
  fi
  ( cd "$dl" && sha -c --quiet "$ASSET.sha256" ) || die "checksum mismatch for v$version"
  tar -xzf "$dl/$ASSET" -C "$out"
  rm -rf "$dl"
}

sync() {
  local version="${1#v}" dest="$2"; shift 2
  local platforms=("$@"); (( ${#platforms[@]} )) || platforms=(web)
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 0.1.0, got '$version'"

  local src; src="$(mktemp -d)"
  fetch "$version" "$src"
  # Releases unpack to dist/ contents directly (VERSION at the top).
  [[ -f "$src/VERSION" ]] || die "release v$version has no VERSION file"

  mkdir -p "$dest"
  for p in "${platforms[@]}"; do
    case "$p" in
      web|swift|android|echarts) [[ -d "$src/$p" ]] || die "release v$version has no $p/"; rm -rf "${dest:?}/$p"; cp -R "$src/$p" "$dest/$p" ;;
      flat) cp "$src/tokens.flat.json" "$dest/tokens.flat.json" ;;
      *) die "unknown platform '$p' (use web swift android echarts flat)" ;;
    esac
  done
  echo "$version" > "$dest/INSTRUMENT_VERSION"
  local manifest; manifest="$(mktemp)"
  ( cd "$dest" && find . -type f ! -name INSTRUMENT_VERSION ! -name .instrument-manifest | LC_ALL=C sort | while IFS= read -r f; do sha "$f"; done ) > "$manifest"
  mv "$manifest" "$dest/.instrument-manifest"
  rm -rf "$src"
  echo "Vendored Instrument v$version (${platforms[*]}) into $dest"
}

case "${1:-}" in
  --check) [[ $# -eq 2 ]] || die "usage: $0 --check <dest-dir>"; check "$2" ;;
  ""|-h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) [[ $# -ge 2 ]] || die "usage: $0 <version> <dest-dir> [platform...]"; sync "$@" ;;
esac
