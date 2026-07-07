#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/build-runtime-plugins.sh [--check]

Synchronize runtime plugin generated files from shared plugin sources.

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
MCP_SHARED_DIR="$ROOT_DIR/plugins/mcp/shared"

copy_tree() {
  local source_dir="$1"
  local dest_dir="$2"
  local tmp_dir
  local diff_file

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
    diff_file="$(mktemp)"
    if ! diff -ruN "$tmp_dir" "$dest_dir" >"$diff_file"; then
      echo "Generated directory is out of date: ${dest_dir#$ROOT_DIR/}" >&2
      cat "$diff_file" >&2
      rm -f "$diff_file"
      rm -rf "$tmp_dir"
      return 1
    fi
    rm -f "$diff_file"
    rm -rf "$tmp_dir"
  else
    rm -rf "$dest_dir"
    mkdir -p "$(dirname "$dest_dir")"
    mv "$tmp_dir" "$dest_dir"
  fi
}

json_string() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"
}

rewrite_codex_skill_paths() {
  local skills_dir="$1"
  local script_dir="$2"
  local file

  for file in \
    "$skills_dir/fix/SKILL.md" \
    "$skills_dir/review-pr-comments/SKILL.md"
  do
    [ -f "$file" ] || continue
    sed "s#\${PLUGIN_ROOT:-\${CLAUDE_PLUGIN_ROOT}}/skills/fix/scripts/fetch-pr-comments.sh#\${PLUGIN_ROOT:-\${CODEX_PLUGIN_ROOT:-\${CLAUDE_PLUGIN_ROOT}}}/$script_dir/fix/scripts/fetch-pr-comments.sh#g" \
      "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
  done

  for file in \
    "$skills_dir/cross-review/SKILL.md" \
    "$skills_dir/cross-review/docs/01-state-and-review.md"
  do
    [ -f "$file" ] || continue
    sed \
      -e 's#${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}#${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}}#g' \
      -e "s#skills/cross-review/scripts#$script_dir/cross-review/scripts#g" \
      "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
  done
}

rewrite_kiro_skill_paths() {
  local skills_dir="$1"
  local file

  for file in \
    "$skills_dir/fix/SKILL.md" \
    "$skills_dir/review-pr-comments/SKILL.md"
  do
    [ -f "$file" ] || continue
    sed 's#${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/skills/fix/scripts/fetch-pr-comments.sh#${PLUGIN_ROOT:-plugins/ndf-kiro}/skills/fix/scripts/fetch-pr-comments.sh#g' \
      "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
  done

  for file in \
    "$skills_dir/statusline/SKILL.md" \
    "$skills_dir/cross-review/SKILL.md" \
    "$skills_dir/cross-review/docs/01-state-and-review.md"
  do
    [ -f "$file" ] || continue
    sed 's#${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}#${PLUGIN_ROOT:-plugins/ndf-kiro}#g' \
      "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
  done
}

sync_skills() {
  local manifest="$1"
  local source_dir="$2"
  local dest_dir="$3"
  local variant="${4:-}"
  local tmp_dir
  local diff_file
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

  if [ "$variant" = codex-legacy ]; then
    rewrite_codex_skill_paths "$tmp_dir" skills-codex
  elif [ "$variant" = codex-runtime ]; then
    rewrite_codex_skill_paths "$tmp_dir" skills
  elif [ "$variant" = kiro-runtime ]; then
    rewrite_kiro_skill_paths "$tmp_dir"
  fi

  if [ "$CHECK" = true ]; then
    if [ ! -d "$dest_dir" ]; then
      echo "Generated directory missing: ${dest_dir#$ROOT_DIR/}" >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    diff_file="$(mktemp)"
    if ! diff -ruN "$tmp_dir" "$dest_dir" >"$diff_file"; then
      echo "Generated directory is out of date: ${dest_dir#$ROOT_DIR/}" >&2
      cat "$diff_file" >&2
      rm -f "$diff_file"
      rm -rf "$tmp_dir"
      return 1
    fi
    rm -f "$diff_file"
    rm -rf "$tmp_dir"
  else
    rm -rf "$dest_dir"
    mkdir -p "$(dirname "$dest_dir")"
    mv "$tmp_dir" "$dest_dir"
  fi
}

sync_legacy_ndf() {
  copy_tree "$SHARED_DIR/skills" "$ROOT_DIR/plugins/ndf/skills"
  sync_skills \
    "$SHARED_DIR/manifests/codex-skills.txt" \
    "$SHARED_DIR/skills" \
    "$ROOT_DIR/plugins/ndf/skills-codex" \
    codex-legacy
  copy_tree "$SHARED_DIR/scripts" "$ROOT_DIR/plugins/ndf/scripts"
}

sync_runtime_if_present() {
  local runtime="$1"
  local manifest="$2"
  local plugin_dir="$ROOT_DIR/plugins/ndf-$runtime"

  [ -d "$plugin_dir" ] || return 0
  if [ "$runtime" = codex ]; then
    sync_skills "$manifest" "$SHARED_DIR/skills" "$plugin_dir/skills" codex-runtime
  elif [ "$runtime" = kiro ]; then
    sync_skills "$manifest" "$SHARED_DIR/skills" "$plugin_dir/skills" kiro-runtime
  else
    sync_skills "$manifest" "$SHARED_DIR/skills" "$plugin_dir/skills"
  fi
  copy_tree "$SHARED_DIR/scripts" "$plugin_dir/scripts"
}

write_codex_mcp_manifest() {
  local plugin_dir="$1"
  local claude_manifest="$plugin_dir/.claude-plugin/plugin.json"
  local codex_manifest="$plugin_dir/.codex-plugin/plugin.json"
  local name version description keywords

  if [ ! -f "$claude_manifest" ]; then
    echo "ERROR: Claude manifest not found: $claude_manifest" >&2
    exit 1
  fi

  name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$claude_manifest")"
  version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version","1.0.0"))' "$claude_manifest")"
  description="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("description",""))' "$claude_manifest")"
  keywords="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])).get("keywords", []), ensure_ascii=False))' "$claude_manifest")"

  mkdir -p "$(dirname "$codex_manifest")"
  cat > "$codex_manifest" <<EOF
{
  "name": $(json_string "$name"),
  "version": $(json_string "$version"),
  "description": $(json_string "$description"),
  "keywords": $keywords,
  "mcpServers": "./.mcp.json"
}
EOF
}

write_kiro_mcp_installer() {
  local plugin_dir="$1"
  local installer="$plugin_dir/install.sh"

  cat > "$installer" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(pwd)"
TARGET_MCP="$PROJECT_ROOT/.mcp.json"
SOURCE_MCP="$SCRIPT_DIR/.mcp.json"
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: bash install.sh [--dry-run]

Install this MCP plugin into the current project's .mcp.json.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ ! -f "$SOURCE_MCP" ]; then
  echo "ERROR: source .mcp.json not found: $SOURCE_MCP" >&2
  exit 1
fi

if [ "$DRY_RUN" = true ]; then
  echo "Would merge $SOURCE_MCP into $TARGET_MCP"
  exit 0
fi

python3 - "$SOURCE_MCP" "$TARGET_MCP" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
source = json.loads(source_path.read_text())
target = json.loads(target_path.read_text()) if target_path.exists() else {}
source_servers = source.get("mcpServers", {})
target.setdefault("mcpServers", {})
target["mcpServers"].update(source_servers)
target_path.write_text(json.dumps(target, indent=2, ensure_ascii=False) + "\n")
print(f"Installed MCP servers: {', '.join(sorted(source_servers))}")
print(f"Updated: {target_path}")
PY
EOF
  chmod +x "$installer"
}

sync_mcp_runtime() {
  local runtime="$1"
  local source_dir="$2"
  local dest_dir="$3"
  local tmp_dir
  local diff_file

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

  case "$runtime" in
    claude)
      rm -rf "$tmp_dir/.codex-plugin"
      ;;
    codex)
      write_codex_mcp_manifest "$tmp_dir"
      rm -rf "$tmp_dir/.claude-plugin"
      ;;
    kiro)
      rm -rf "$tmp_dir/.claude-plugin" "$tmp_dir/.codex-plugin"
      write_kiro_mcp_installer "$tmp_dir"
      ;;
    *)
      echo "ERROR: unknown MCP runtime: $runtime" >&2
      rm -rf "$tmp_dir"
      exit 1
      ;;
  esac

  if [ "$CHECK" = true ]; then
    if [ ! -d "$dest_dir" ]; then
      echo "Generated directory missing: ${dest_dir#$ROOT_DIR/}" >&2
      rm -rf "$tmp_dir"
      return 1
    fi
    diff_file="$(mktemp)"
    if ! diff -ruN "$tmp_dir" "$dest_dir" >"$diff_file"; then
      echo "Generated directory is out of date: ${dest_dir#$ROOT_DIR/}" >&2
      cat "$diff_file" >&2
      rm -f "$diff_file"
      rm -rf "$tmp_dir"
      return 1
    fi
    rm -f "$diff_file"
    rm -rf "$tmp_dir"
  else
    rm -rf "$dest_dir"
    mkdir -p "$(dirname "$dest_dir")"
    mv "$tmp_dir" "$dest_dir"
  fi
}

sync_mcp_plugins() {
  local plugin_dir plugin_name

  [ -d "$MCP_SHARED_DIR" ] || return 0
  for plugin_dir in "$MCP_SHARED_DIR"/*; do
    [ -d "$plugin_dir" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    sync_mcp_runtime claude "$plugin_dir" "$ROOT_DIR/plugins/mcp/claude/$plugin_name"
    sync_mcp_runtime codex "$plugin_dir" "$ROOT_DIR/plugins/mcp/codex/$plugin_name"
    sync_mcp_runtime kiro "$plugin_dir" "$ROOT_DIR/plugins/mcp/kiro/$plugin_name"
  done
}

sync_legacy_ndf
sync_runtime_if_present claude "$SHARED_DIR/manifests/claude-skills.txt"
sync_runtime_if_present codex "$SHARED_DIR/manifests/codex-skills.txt"
sync_runtime_if_present kiro "$SHARED_DIR/manifests/kiro-skills.txt"
sync_mcp_plugins

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
