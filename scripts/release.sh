#!/usr/bin/env bash
# Release: push, prove, tag, prove, verify at the registry.
#
# Exists because five ad-hoc release attempts in one night each failed a
# different way, and every failure was a *reading* failure, not a build
# failure. The counterexamples this script encodes:
#
#   1. "Newest run" is not "this commit's run" - a poll racing run
#      creation reads the PREVIOUS run's conclusion. Every wait here is
#      pinned to the commit SHA.
#   2. A green main build publishes nothing deployable - :latest moves
#      only on a v* tag build. Both builds are awaited, separately.
#   3. Gate output piped through tail can swallow the failure line while
#      showing a truthful-looking tail. Gates here are judged by EXIT
#      CODE, never by reading their output.
#   4. A SHA typed from memory is a filter that never matches. The SHA
#      is taken from git, once, and threaded everywhere.
#
# The registry digest comparison at the end is the only step that proves
# a deploy will actually fetch the new code. Nothing before it counts.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

IMAGE_PATH="mysteraitch/open-banking-data-ingestion"
POLL_SECONDS=20
POLL_LIMIT=45   # ~15 minutes per awaited build

say()  { printf '%s\n' "$*"; }
fail() { printf 'RELEASE FAILED: %s\n' "$*" >&2; exit 1; }

# --- preconditions ---------------------------------------------------------
[ -z "$(git status --porcelain)" ] || fail "working tree not clean"

VERSION=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" 2>/dev/null) \
  || VERSION=$(./.venv/Scripts/python.exe -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
[ -n "$VERSION" ] || fail "could not read version from pyproject.toml"
TAG="v$VERSION"
SHA=$(git rev-parse HEAD)

if git ls-remote --tags origin "$TAG" | grep -q .; then
  EXISTING=$(git ls-remote --tags origin "$TAG^{}" | awk '{print $1}')
  [ "$EXISTING" = "$SHA" ] || fail "$TAG already exists on the remote at a DIFFERENT commit - bump the version"
  say "$TAG already on the remote at this commit - resuming"
fi

say "releasing $TAG at ${SHA:0:9}"

# --- gates, judged by exit code only ---------------------------------------
run_gate() {
  say "gate: $*"
  "$@" >/dev/null 2>&1 || fail "gate failed: $* (re-run without the script to see why)"
}
PY=./.venv/Scripts/python.exe
[ -x "$PY" ] || PY=python
run_gate "$PY" -m ruff check .
run_gate "$PY" -m mypy
run_gate "$PY" -m pytest -q

# --- push and await THIS COMMIT's main build -------------------------------
git push origin main

await_run() { # $1 = branch/ref name shown by gh
  local ref="$1" state="" i=0
  while [ $i -lt $POLL_LIMIT ]; do
    state=$(gh run list --branch "$ref" --limit 5 \
      --json headSha,status,conclusion \
      --jq ".[] | select(.headSha == \"$SHA\") | \"\(.status) \(.conclusion // \"-\")\"" \
      | head -1)
    if [ "${state%% *}" = "completed" ]; then
      [ "${state##* }" = "success" ] || fail "$ref build for ${SHA:0:9} concluded: ${state##* }"
      say "$ref build: success"
      return 0
    fi
    i=$((i + 1)); sleep $POLL_SECONDS
  done
  fail "$ref build for ${SHA:0:9} did not complete within $((POLL_LIMIT * POLL_SECONDS))s"
}

await_run main

# --- tag the proven commit, await the tag build ----------------------------
git tag -a "$TAG" -m "release $TAG" "$SHA" 2>/dev/null || true
git push origin "$TAG" 2>/dev/null || true
await_run "$TAG"

# --- prove it at the registry ----------------------------------------------
TOKEN=$(curl -fsS "https://ghcr.io/token?scope=repository:${IMAGE_PATH}:pull&service=ghcr.io" \
  | sed -E 's/.*"token":"([^"]+)".*/\1/')
[ -n "$TOKEN" ] || fail "could not obtain a registry token"

digest_of() {
  curl -fsS -o /dev/null -D - \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json" \
    "https://ghcr.io/v2/${IMAGE_PATH}/manifests/$1" \
    | tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" {print $2}'
}

VERSION_DIGEST=$(digest_of "$VERSION")
LATEST_DIGEST=$(digest_of "latest")
[ -n "$VERSION_DIGEST" ] || fail "no image manifest for $VERSION at the registry"
[ "$VERSION_DIGEST" = "$LATEST_DIGEST" ] \
  || fail "latest (${LATEST_DIGEST:0:24}) does not match $VERSION (${VERSION_DIGEST:0:24}) - latest did not move"

say ""
say "RELEASED $TAG"
say "  commit  $SHA"
say "  image   $VERSION_DIGEST"
say "  latest  confirmed moved - a deploy will fetch this build"
