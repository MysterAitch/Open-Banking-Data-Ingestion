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
#      only on a v* tag build. The tag build is the one awaited.
#      Both used to be awaited in turn, and each runs the whole suite
#      before its 35-second image build: measured on 2026-10-02, a
#      release took 22 minutes, of which 6.9 was waiting for a main
#      build whose verdict the tag build repeats. main and the tag are
#      now pushed together and run side by side.
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
# The verdict is the exit code, never the output - but the output is KEPT, and
# shown when a gate fails. Discarding it made a failure say only "re-run to see
# why", and on 2026-10-02 the re-run passed: a suite that failed under machine
# load took six minutes to not reproduce, and which test had failed was never
# learnt. A failure that cannot be read is a failure that gets retried instead.
GATE_LOG=$(mktemp)
run_gate() {
  say "gate: $*"
  if ! "$@" >"$GATE_LOG" 2>&1; then
    say "--- last lines of the failed gate's output (all of it: $GATE_LOG)" >&2
    tail -n 40 "$GATE_LOG" >&2
    fail "gate failed: $*"
  fi
}
PY=./.venv/Scripts/python.exe
[ -x "$PY" ] || PY=python
run_gate "$PY" -m ruff check .
run_gate "$PY" -m mypy
# In parallel: 2,708 tests took 8.5 minutes in one process and 2 minutes 7
# seconds across six (2026-10-02). OBDI_TEST_WORKERS=0 runs them in one
# process again, for a failure that only appears in parallel.
run_gate "$PY" -m pytest -q -n "${OBDI_TEST_WORKERS:-6}"
# The applier's tests gate the image in CI; run here they fail in seconds
# and before anything is pushed.
run_gate bash -c 'cd applier && node --test'

# --- push main and the tag together ----------------------------------------
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
    # Heartbeat: a background shell that is silent for minutes is
    # indistinguishable from a hung one, and supervisors reap what looks
    # hung. One line per poll is liveness the environment can see and
    # progress a person can read - learned from six kills in one night,
    # none of which this script deserved.
    say "  waiting on $ref build (${state:-not yet listed}, $((i * POLL_SECONDS))s)"
    i=$((i + 1)); sleep $POLL_SECONDS
  done
  fail "$ref build for ${SHA:0:9} did not complete within $((POLL_LIMIT * POLL_SECONDS))s"
}

# --- tag the gated commit, await the tag build -----------------------------
# The tag build runs the same checks as the main build before it publishes,
# so a commit that fails them publishes nothing and this script says so.
# What that costs is the version number: a tag on a failed build stays.
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
