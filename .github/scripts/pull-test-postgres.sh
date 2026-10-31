#!/usr/bin/env bash
# Pull the Postgres image the integration tests use (testcontainers), surviving the
# anonymous pull limits of shared runners. Usage: pull-test-postgres.sh IMAGE
#
# IMAGE (public.ecr.aws/docker/library/postgres:16-alpine) is tried a few times. If that
# registry keeps refusing (ECR Public answers "toomanyrequests: Data limit exceeded" when
# the runner's IP is out of quota), the same official image is pulled from Google's Docker
# Hub mirror and tagged as IMAGE, so the tests find it locally under the name they ask for.
set -euo pipefail

image=${1:?usage: pull-test-postgres.sh IMAGE}
name=${image##*/}                      # postgres:16-alpine
fallback="mirror.gcr.io/library/${name}"

pull() {
  local img=$1 i
  for i in 1 2 3; do
    docker pull "$img" && return 0
    echo "pull of $img failed (attempt $i)" >&2
    sleep $((i * ${PULL_BACKOFF_S:-10}))
  done
  return 1
}

if pull "$image"; then
  exit 0
fi
echo "falling back to $fallback" >&2
pull "$fallback"
docker tag "$fallback" "$image"
