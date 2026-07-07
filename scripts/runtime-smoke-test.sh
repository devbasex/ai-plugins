#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="all"
WITH_SECRETS="off"
KEEP_CONTAINER=false
ARTIFACT_DIR="$ROOT_DIR/tmp/runtime-smoke"

usage() {
  cat <<'USAGE'
Usage: bash scripts/runtime-smoke-test.sh [OPTIONS]

Options:
  --runtime claude|codex|kiro|all  runtime to test (default: all)
  --with-secrets off|auto|required secret handling mode (default: off)
  --secret-file KEY=PATH           inject an allowlisted file secret
  --keep-container                 keep the container for local debugging
  --artifact-dir PATH              write logs/JUnit/artifacts under PATH
  -h, --help                       show this help
USAGE
}

SECRET_FILES=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runtime) RUNTIME="${2:?}"; shift ;;
    --runtime=*) RUNTIME="${1#*=}" ;;
    --with-secrets) WITH_SECRETS="${2:?}"; shift ;;
    --with-secrets=*) WITH_SECRETS="${1#*=}" ;;
    --secret-file) SECRET_FILES+=("${2:?}"); shift ;;
    --secret-file=*) SECRET_FILES+=("${1#*=}") ;;
    --keep-container) KEEP_CONTAINER=true ;;
    --artifact-dir) ARTIFACT_DIR="${2:?}"; shift ;;
    --artifact-dir=*) ARTIFACT_DIR="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$RUNTIME" in claude|codex|kiro|all) ;; *) echo "ERROR: invalid --runtime: $RUNTIME" >&2; exit 2 ;; esac
case "$WITH_SECRETS" in off|auto|required) ;; *) echo "ERROR: invalid --with-secrets: $WITH_SECRETS" >&2; exit 2 ;; esac
if [ "$KEEP_CONTAINER" = true ] && [ "$WITH_SECRETS" != off ]; then
  echo "ERROR: --keep-container cannot be used with --with-secrets=$WITH_SECRETS" >&2
  exit 2
fi
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required" >&2; exit 1; }

mkdir -p "$ARTIFACT_DIR"
ARTIFACT_DIR="$(cd "$ARTIFACT_DIR" && pwd)"

RUNTIMES=("$RUNTIME")
[ "$RUNTIME" = all ] && RUNTIMES=(claude codex kiro)

RAW_SECRET_NAMES=(
  ANTHROPIC_API_KEY
  OPENAI_API_KEY
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_SESSION_TOKEN
  BIGQUERY_PROJECT
  BIGQUERY_LOCATION
  BIGQUERY_DATASET
  REDASH_URL
  REDASH_API_KEY
)
FILE_SECRET_ENV_NAMES=(GOOGLE_APPLICATION_CREDENTIALS BIGQUERY_KEY_FILE)
ALLOWLIST="$ROOT_DIR/tests/runtime-smoke/secrets-files.allowlist"

validate_secret_key() {
  local key="$1"
  [[ "$key" =~ ^[A-Za-z0-9_.-]+$ ]] || return 1
  grep -Fxq "$key" "$ALLOWLIST"
}

copy_secret_file() {
  local cid="$1"
  local src="$2"
  local key="$3"
  local base
  base="$(basename "$src")"
  tar -C "$(dirname "$src")" -cf - "$base" |
    docker exec -i "$cid" tar -C /tmp/runtime-secrets -xf -
  docker exec "$cid" sh -c 'mv "$1" "$2" && chmod 0444 "$2"' sh "/tmp/runtime-secrets/$base" "/tmp/runtime-secrets/$key"
}

secret_count=0
for name in "${RAW_SECRET_NAMES[@]}"; do
  [ -n "${!name:-}" ] && secret_count=$((secret_count + 1))
done
for name in "${FILE_SECRET_ENV_NAMES[@]}"; do
  [ -n "${!name:-}" ] && secret_count=$((secret_count + 1))
done
secret_count=$((secret_count + ${#SECRET_FILES[@]}))
if [ "$WITH_SECRETS" = required ] && [ "$secret_count" -eq 0 ]; then
  echo "ERROR: --with-secrets=required but no allowlisted secrets were found" >&2
  exit 1
fi

build_images() {
  docker build -q -f "$ROOT_DIR/tests/runtime-smoke/Containerfile.base" -t ai-plugins-runtime-smoke-base "$ROOT_DIR" >/dev/null
  for runtime in "${RUNTIMES[@]}"; do
    docker build -q -f "$ROOT_DIR/tests/runtime-smoke/Containerfile.$runtime" -t "ai-plugins-runtime-smoke-$runtime" "$ROOT_DIR" >/dev/null
  done
}

inject_secrets() {
  local cid="$1"
  [ "$WITH_SECRETS" = off ] && return 0

  {
    for name in "${RAW_SECRET_NAMES[@]}"; do
      [ -n "${!name:-}" ] && printf 'export %s=%q\n' "$name" "${!name}"
    done
  } | docker exec -i "$cid" sh -c 'cat > /tmp/runtime-secrets/raw-env && chmod 0444 /tmp/runtime-secrets/raw-env'

  for name in "${FILE_SECRET_ENV_NAMES[@]}"; do
    [ -n "${!name:-}" ] || continue
    local src="${!name}"
    [ -f "$src" ] || { echo "ERROR: $name points to a missing file" >&2; return 1; }
    local key
    key="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"
    validate_secret_key "$key" || { echo "ERROR: file secret key is not allowlisted: $key" >&2; return 1; }
    copy_secret_file "$cid" "$src" "$key"
    printf 'export %s=%q\n' "$name" "/tmp/runtime-secrets/$key" |
      docker exec -i "$cid" sh -c 'cat >> /tmp/runtime-secrets/raw-env'
  done

  for item in "${SECRET_FILES[@]}"; do
    local key="${item%%=*}"
    local src="${item#*=}"
    [ "$key" != "$item" ] || { echo "ERROR: --secret-file must be KEY=PATH" >&2; return 1; }
    validate_secret_key "$key" || { echo "ERROR: file secret key is not allowlisted: $key" >&2; return 1; }
    [ -f "$src" ] || { echo "ERROR: secret file not found: $src" >&2; return 1; }
    copy_secret_file "$cid" "$src" "$key"
  done
}

run_runtime() {
  local runtime="$1"
  local runtime_artifacts="$ARTIFACT_DIR/$runtime"
  local cid=""
  rm -rf "$runtime_artifacts"
  mkdir -p "$runtime_artifacts"

  cid="$(docker run -d \
    --tmpfs /tmp/runtime-secrets:rw,noexec,nosuid,nodev,size=1m \
    -e HOME=/tmp/runtime-home \
    -e RUNTIME="$runtime" \
    -e WITH_SECRETS="$WITH_SECRETS" \
    -e REPO_ROOT=/workspace/ai-plugins \
    -e PROJECT_DIR=/tmp/runtime-project \
    -e ARTIFACT_DIR=/tmp/runtime-artifacts \
    "ai-plugins-runtime-smoke-$runtime" sleep infinity)"
  if [ "$KEEP_CONTAINER" = false ]; then
    trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' RETURN
  else
    echo "$cid" > "$runtime_artifacts/container-id.txt"
  fi

  docker exec "$cid" mkdir -p /workspace/ai-plugins
  tar \
    --exclude=.git \
    --exclude=.claude \
    --exclude=.codex \
    --exclude=.kiro \
    --exclude=tmp \
    -C "$ROOT_DIR" -cf - . |
    docker exec -i "$cid" tar -C /workspace/ai-plugins -xf -
  docker exec "$cid" bash -lc 'chmod -R a-w /workspace/ai-plugins'
  inject_secrets "$cid"
  docker exec "$cid" bash -lc 'mkdir -p "$HOME" "$PROJECT_DIR" "$ARTIFACT_DIR"; cd "$PROJECT_DIR"; "/workspace/ai-plugins/tests/runtime-smoke/adapters/${RUNTIME}.sh"' \
    >"$runtime_artifacts/smoke.log" 2>&1
  docker exec "$cid" bash -lc 'find /tmp/runtime-project -maxdepth 5 -print | sort > /tmp/runtime-artifacts/generated-tree.txt'
  docker cp "$cid:/tmp/runtime-artifacts/." "$runtime_artifacts/" >/dev/null
  if [ "$KEEP_CONTAINER" = false ]; then
    docker rm -f "$cid" >/dev/null 2>&1 || true
  fi
}

build_images
for runtime in "${RUNTIMES[@]}"; do
  echo "==> runtime smoke: $runtime"
  run_runtime "$runtime"
done

echo "runtime smoke tests passed: ${RUNTIMES[*]}"
