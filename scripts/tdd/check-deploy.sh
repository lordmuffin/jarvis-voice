#!/usr/bin/env bash
# GitOps baseline (TDD.md §3b): render both overlays, kubeconform -strict, conftest policies.
set -euo pipefail
cd "$(dirname "$0")/../.."
BIN="$PWD/.tools/bin"
export PATH="$BIN:$PATH"
OUT="$PWD/.tools/render"
POLICY=server/deploy/policy
mkdir -p "$OUT"

# CRD schemas (OnePasswordItem etc.) come from the datree catalog; needs network.
SCHEMAS=(-schema-location default
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json')

conftest verify -p "$POLICY"

for channel in live lab; do
  dir=server/deploy/k8s/jarvis-live
  [ "$channel" = lab ] && dir=server/deploy/k8s/jarvis-live-lab
  kustomize build "$dir" >"$OUT/$channel.yaml"
  kubeconform -strict -summary "${SCHEMAS[@]}" "$OUT/$channel.yaml"
  conftest test "$OUT/$channel.yaml" -p "$POLICY" --data "$POLICY/exceptions.yaml" -n main -n "$channel"
done
