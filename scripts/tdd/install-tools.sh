#!/usr/bin/env bash
# Install the pinned GitOps check tools into .tools/bin (conftest, kubeconform, kustomize).
# Checksummed; idempotent. `make tools` calls this; CI calls it too.
set -euo pipefail
cd "$(dirname "$0")/../.."
BIN="$PWD/.tools/bin"
mkdir -p "$BIN"

CONFTEST=0.62.0
KUBECONFORM=0.7.0
KUSTOMIZE=5.6.0

fetch() { # name version url sha256 binary-in-archive
  local name=$1 version=$2 url=$3 sha=$4 bin=$5
  if [ -x "$BIN/$name" ] && [ "$(cat "$BIN/.$name.version" 2>/dev/null)" = "$version" ]; then return; fi
  local tmp; tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/a.tgz" "$url"
  echo "$sha  $tmp/a.tgz" | sha256sum -c - >/dev/null
  tar -xzf "$tmp/a.tgz" -C "$tmp"
  install -m 0755 "$tmp/$bin" "$BIN/$name"
  echo "$version" >"$BIN/.$name.version"
  rm -rf "$tmp"
  echo "installed $name $version"
}

fetch conftest "$CONFTEST" \
  "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST}/conftest_${CONFTEST}_Linux_x86_64.tar.gz" \
  284231908e4cf66d156e5577c3eee2f67d9f2edf2521fa2309e0df1969f871e5 conftest
fetch kubeconform "$KUBECONFORM" \
  "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM}/kubeconform-linux-amd64.tar.gz" \
  c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3 kubeconform
fetch kustomize "$KUSTOMIZE" \
  "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE}/kustomize_v${KUSTOMIZE}_linux_amd64.tar.gz" \
  54e4031ddc4e7fc59e408da29e7c646e8e57b8088c51b84b3df0864f47b5148f kustomize
