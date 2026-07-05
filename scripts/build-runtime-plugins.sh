#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/build-runtime-plugins.sh [--check]

Synchronize runtime plugin generated files from plugins/ndf-shared.

Options:
  --check   Compare generated output with the working tree and fail on drift.
  -h, --help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

SHARED_DIR="$ROOT_DIR/plugins/ndf-shared"

copy_tree() {
  local source_dir="$1"
  local dest_dir="$2"
  local tmp_dir

  if [ ! -d "$source_dir" ]; then
    echo "ERROR: source directory not found: $source_dir" >&2
    exit 1
  fi

  tmp_dir="$(mktemp -d)"
  cp -a "$source_dir/." "$tmp_dir/"
  find "$tmp_dir" \( \
    -name .venv -o \
    -name .pytest_cache -o \
    -name __pycache__ -o \
    -name '*.pyc' -o \
    -name '*.pyo' \
  \) -exec rm -rf {} + 2>/dev/null || true

  if [ "$CHECK" = true ]; then
    if [ ! -d "$dest_dir" ]; then
      echo "Generated directory missing: ${dest_dir#$ROOT_DIR/}" >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    if ! diff -ruN "$tmp_dir" "$dest_dir" >/tmp/runtime-plugin-build-check.diff; then
      echo "Generated directory is out of date: ${dest_dir#$ROOT_DIR/}" >&2
      cat /tmp/runtime-plugin-build-check.diff >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    rm -rf "$tmp_dir"
  else
    rm -rf "$dest_dir"
    mkdir -p "$(dirname "$dest_dir")"
    mv "$tmp_dir" "$dest_dir"
  fi
}

sync_skills() {
  local manifest="$1"
  local source_dir="$2"
  local dest_dir="$3"
  local tmp_dir
  local skill

  if [ ! -f "$manifest" ]; then
    echo "ERROR: manifest not found: $manifest" >&2
    exit 1
  fi
  if [ ! -d "$source_dir" ]; then
    echo "ERROR: source directory not found: $source_dir" >&2
    exit 1
  fi

  tmp_dir="$(mktemp -d)"
  while IFS= read -r skill || [ -n "$skill" ]; do
    skill="${skill%%#*}"
    skill="${skill#"${skill%%[![:space:]]*}"}"
    skill="${skill%"${skill##*[![:space:]]}"}"
    [ -z "$skill" ] && continue

    case "$skill" in
      */*|*..*|'')
        echo "ERROR: invalid skill name in ${manifest#$ROOT_DIR/}: $skill" >&2
        rm -rf "$tmp_dir"
        exit 1
        ;;
    esac

    if [ ! -f "$source_dir/$skill/SKILL.md" ]; then
      echo "ERROR: missing SKILL.md for $skill in ${source_dir#$ROOT_DIR/}" >&2
      rm -rf "$tmp_dir"
      exit 1
    fi
    cp -a "$source_dir/$skill" "$tmp_dir/$skill"
  done < "$manifest"

  find "$tmp_dir" \( \
    -name .venv -o \
    -name .pytest_cache -o \
    -name __pycache__ -o \
    -name '*.pyc' -o \
    -name '*.pyo' \
  \) -exec rm -rf {} + 2>/dev/null || true

  if [ "$CHECK" = true ]; then
    if [ ! -d "$dest_dir" ]; then
      echo "Generated directory missing: ${dest_dir#$ROOT_DIR/}" >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    if ! diff -ruN "$tmp_dir" "$dest_dir" >/tmp/runtime-plugin-build-check.diff; then
      echo "Generated directory is out of date: ${dest_dir#$ROOT_DIR/}" >&2
      cat /tmp/runtime-plugin-build-check.diff >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    rm -rf "$tmp_dir"
  else
    rm -rf "$dest_dir"
    mkdir -p "$(dirname "$dest_dir")"
    mv "$tmp_dir" "$dest_dir"
  fi
}

sync_legacy_ndf() {
  sync_skills \
    "$SHARED_DIR/manifests/claude-skills.txt" \
    "$SHARED_DIR/skills" \
    "$ROOT_DIR/plugins/ndf/skills"
  copy_tree "$SHARED_DIR/scripts" "$ROOT_DIR/plugins/ndf/scripts"
}

sync_runtime_if_present() {
  local runtime="$1"
  local manifest="$2"
  local plugin_dir="$ROOT_DIR/plugins/ndf-$runtime"

  [ -d "$plugin_dir" ] || return 0
  sync_skills "$manifest" "$SHARED_DIR/skills" "$plugin_dir/skills"
  copy_tree "$SHARED_DIR/scripts" "$plugin_dir/scripts"
}

sync_legacy_ndf
sync_runtime_if_present claude "$SHARED_DIR/manifests/claude-skills.txt"
sync_runtime_if_present codex "$SHARED_DIR/manifests/codex-skills.txt"
sync_runtime_if_present kiro "$SHARED_DIR/manifests/kiro-skills.txt"

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
