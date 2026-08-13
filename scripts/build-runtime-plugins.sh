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

rewrite_codex_skill_paths() {
  local skills_dir="$1"
  local script_dir="$2"
  local file

  for file in \
    "$skills_dir/fix/SKILL.md"
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

# Codex は Skill ごとの `<Skill 名>/agents/openai.yaml` で暗黙起動を制御する。
# SKILL.md の frontmatter を読み、`disable-model-invocation: true` を持つ Skill だけへ生成する。
write_codex_skill_policies() {
  local skills_dir="$1"

  python3 - "$skills_dir" <<'PY'
import sys
from pathlib import Path

skills_dir = Path(sys.argv[1])


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        # ネストした値（allowed-tools のリストなど）はここでは扱わない
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def yaml_double_quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        continue

    agents_dir = skill_dir / "agents"
    policy_path = agents_dir / "openai.yaml"
    # 前回の生成物を必ず捨ててから作り直す（対象から外れた Skill に残さない）
    policy_path.unlink(missing_ok=True)

    fields = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if fields.get("disable-model-invocation") != "true":
        if agents_dir.is_dir() and not any(agents_dir.iterdir()):
            agents_dir.rmdir()
        continue

    lines = ["policy:", "  allow_implicit_invocation: false"]
    argument_hint = fields.get("argument-hint")
    if argument_hint:
        lines += ["interface:", f"  default_prompt: {yaml_double_quoted(argument_hint)}"]

    agents_dir.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

rewrite_kiro_skill_paths() {
  local skills_dir="$1"
  local file

  for file in \
    "$skills_dir/fix/SKILL.md"
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

  if [ "$variant" = codex-runtime ]; then
    rewrite_codex_skill_paths "$tmp_dir" skills
    write_codex_skill_policies "$tmp_dir"
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

  if [ ! -f "$claude_manifest" ]; then
    echo "ERROR: Claude manifest not found: $claude_manifest" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$codex_manifest")"
  python3 - "$plugin_dir" "$claude_manifest" "$codex_manifest" <<'PY'
import json
import sys
from pathlib import Path

plugin_dir = Path(sys.argv[1])
claude_manifest = Path(sys.argv[2])
codex_manifest = Path(sys.argv[3])

manifest = json.loads(claude_manifest.read_text())
manifest["mcpServers"] = "./.mcp.json"
if (plugin_dir / "skills").is_dir():
    manifest["skills"] = "./skills/"
if (plugin_dir / "hooks" / "hooks.json").is_file():
    manifest["hooks"] = "./hooks/hooks.json"

codex_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
PY
}

write_codex_mcp_config() {
  local plugin_dir="$1"
  local mcp_config="$plugin_dir/.mcp.json"

  if [ ! -f "$mcp_config" ]; then
    echo "ERROR: MCP config not found: $mcp_config" >&2
    exit 1
  fi

  python3 - "$mcp_config" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
config = json.loads(config_path.read_text())
servers = config.get("mcpServers")
if not isinstance(servers, dict):
    raise SystemExit(f"ERROR: mcpServers object not found: {config_path}")

config_path.write_text(json.dumps(servers, indent=2, ensure_ascii=False) + "\n")
PY
}

rewrite_mcp_readme_for_runtime() {
  local runtime="$1"
  local readme="$2"

  [ -f "$readme" ] || return 0

  python3 - "$runtime" "$readme" <<'PY'
import sys
from pathlib import Path

runtime = sys.argv[1]
readme_path = Path(sys.argv[2])
text = readme_path.read_text()

runtime_titles = {"Claude Code", "Codex", "Kiro CLI"}
keep_title = {"claude": "Claude Code", "codex": "Codex", "kiro": "Kiro CLI"}[runtime]

lines = text.splitlines(keepends=True)
rewritten = []
in_install = False
keep_subsection = True

for line in lines:
    if line.startswith("## "):
        in_install = line.strip() == "## インストール"
        keep_subsection = True
        rewritten.append(line)
        continue

    if in_install and line.startswith("### "):
        title = line[4:].strip()
        keep_subsection = title not in runtime_titles or title == keep_title

    if keep_subsection:
        rewritten.append(line)

text = "".join(rewritten)
runtime_name = {"claude": "Claude Code", "codex": "Codex", "kiro": "Kiro"}[runtime]
runtime_command = {"claude": "claude", "codex": "codex", "kiro": "kiro"}[runtime]

text = text.replace("Claude Code", runtime_name)
text = "\n".join(
    runtime_command if line == "claude" else line
    for line in text.split("\n")
)

readme_path.write_text(text)
PY
}

apply_mcp_readme_template() {
  local runtime="$1"
  local plugin_dir="$2"
  local readme_template="$plugin_dir/README.$runtime.md"

  if [ -f "$readme_template" ]; then
    mv "$readme_template" "$plugin_dir/README.md"
  else
    rewrite_mcp_readme_for_runtime "$runtime" "$plugin_dir/README.md"
  fi
  rm -f "$plugin_dir"/README.claude.md "$plugin_dir"/README.codex.md "$plugin_dir"/README.kiro.md
}

rewrite_kiro_mcp_paths() {
  local plugin_dir="$1"
  local plugin_name="$2"
  local root_expr="\${PLUGIN_ROOT:-.kiro/mcp_runtime/$plugin_name}"
  local file

  while IFS= read -r file; do
    sed "s#\${PLUGIN_ROOT:-\${CODEX_PLUGIN_ROOT:-\${CLAUDE_PLUGIN_ROOT}}}#$root_expr#g" \
      "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
  done < <(find "$plugin_dir" -type f \( -name 'SKILL.md' -o -name 'hooks.json' \))
}

write_kiro_mcp_installer() {
  local plugin_dir="$1"
  local plugin_name="$2"
  local installer="$plugin_dir/install.sh"

  cat > "$installer" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_NAME="$(basename "$SCRIPT_DIR")"
PROJECT_ROOT="$(pwd)"
KIRO_DIR="$PROJECT_ROOT/.kiro"
KIRO_AGENT_FILE="$KIRO_DIR/agents/default.json"
KIRO_PLUGIN_LINK="$KIRO_DIR/mcp_runtime/$PLUGIN_NAME"
KIRO_SKILLS_DIR="$KIRO_DIR/skills"
TARGET_MCP="$PROJECT_ROOT/.mcp.json"
SOURCE_MCP="$SCRIPT_DIR/.mcp.json"
SOURCE_HOOKS="$SCRIPT_DIR/hooks/hooks.json"
SOURCE_SKILLS="$SCRIPT_DIR/skills"
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: bash install.sh [--project PATH] [--dry-run]

Install this MCP plugin into the current project's Kiro settings.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project)
      [ "$#" -ge 2 ] || { echo "ERROR: --project requires a path" >&2; exit 2; }
      PROJECT_ROOT="$(cd "$2" && pwd)"
      KIRO_DIR="$PROJECT_ROOT/.kiro"
      KIRO_AGENT_FILE="$KIRO_DIR/agents/default.json"
      KIRO_PLUGIN_LINK="$KIRO_DIR/mcp_runtime/$PLUGIN_NAME"
      KIRO_SKILLS_DIR="$KIRO_DIR/skills"
      TARGET_MCP="$PROJECT_ROOT/.mcp.json"
      shift
      ;;
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
  echo "Would link plugin runtime into $KIRO_PLUGIN_LINK"
  if [ -d "$SOURCE_SKILLS" ]; then
    echo "Would link skills from $SOURCE_SKILLS into $KIRO_SKILLS_DIR"
  fi
  if [ -f "$SOURCE_HOOKS" ]; then
    echo "Would merge hooks from $SOURCE_HOOKS into $KIRO_AGENT_FILE"
  fi
  exit 0
fi

mkdir -p "$KIRO_DIR/mcp_runtime" "$KIRO_SKILLS_DIR" "$(dirname "$KIRO_AGENT_FILE")"
if [ -e "$KIRO_PLUGIN_LINK" ] && [ ! -L "$KIRO_PLUGIN_LINK" ]; then
  echo "ERROR: $KIRO_PLUGIN_LINK already exists and is not a symlink" >&2
  exit 1
fi
ln -sfn "$SCRIPT_DIR" "$KIRO_PLUGIN_LINK"

if [ -d "$SOURCE_SKILLS" ]; then
  find "$SOURCE_SKILLS" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r skill_dir; do
    skill_name="$(basename "$skill_dir")"
    skill_link="$KIRO_SKILLS_DIR/$skill_name"
    [ -f "$skill_dir/SKILL.md" ] || continue
    if [ -e "$skill_link" ] && [ ! -L "$skill_link" ]; then
      echo "ERROR: $skill_link already exists and is not a symlink" >&2
      exit 1
    fi
    ln -sfn "$skill_dir" "$skill_link"
    echo "Linked Kiro skill: $skill_name"
  done
fi

python3 - "$SOURCE_MCP" "$TARGET_MCP" "$SOURCE_HOOKS" "$KIRO_AGENT_FILE" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
hooks_path = Path(sys.argv[3])
agent_path = Path(sys.argv[4])
source = json.loads(source_path.read_text())
target = json.loads(target_path.read_text()) if target_path.exists() else {}
source_servers = source.get("mcpServers", {})
target.setdefault("mcpServers", {})
target["mcpServers"].update(source_servers)
target_path.write_text(json.dumps(target, indent=2, ensure_ascii=False) + "\n")

if agent_path.exists():
    agent = json.loads(agent_path.read_text())
else:
    agent = {
        "name": "default",
        "resources": ["skill://.kiro/skills/**/SKILL.md"],
    }

agent.setdefault("mcpServers", {})
agent["mcpServers"].update(source_servers)

if hooks_path.exists():
    source_hooks = json.loads(hooks_path.read_text()).get("hooks", {})
    target_hooks = agent.setdefault("hooks", {})
    event_map = {
        "SessionStart": "agentSpawn",
        "Stop": "stop",
    }
    for source_event, groups in source_hooks.items():
        target_event = event_map.get(source_event, source_event)
        target_list = target_hooks.setdefault(target_event, [])
        existing = {
            entry.get("command")
            for entry in target_list
            if isinstance(entry, dict)
        }
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command")
                if not command or command in existing:
                    continue
                entry = {"command": command}
                if "timeout_ms" in hook:
                    entry["timeout_ms"] = hook["timeout_ms"]
                target_list.append(entry)
                existing.add(command)

agent_path.write_text(json.dumps(agent, indent=2, ensure_ascii=False) + "\n")

print(f"Installed MCP servers: {', '.join(sorted(source_servers))}")
print(f"Updated: {target_path}")
print(f"Updated: {agent_path}")
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
  apply_mcp_readme_template "$runtime" "$tmp_dir"

  case "$runtime" in
    claude)
      rm -rf "$tmp_dir/.codex-plugin"
      ;;
    codex)
      write_codex_mcp_manifest "$tmp_dir"
      write_codex_mcp_config "$tmp_dir"
      rm -rf "$tmp_dir/.claude-plugin"
      ;;
    kiro)
      rm -rf "$tmp_dir/.claude-plugin" "$tmp_dir/.codex-plugin"
      rewrite_kiro_mcp_paths "$tmp_dir" "$(basename "$dest_dir")"
      write_kiro_mcp_installer "$tmp_dir" "$(basename "$dest_dir")"
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

clean_mcp_runtime_outputs() {
  local runtime="$1"
  local runtime_dir="$ROOT_DIR/plugins/mcp/$runtime"
  local plugin_dir plugin_name

  [ -d "$runtime_dir" ] || return 0
  for plugin_dir in "$runtime_dir"/*; do
    [ -d "$plugin_dir" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    [ -d "$MCP_SHARED_DIR/$plugin_name" ] && continue

    if [ "$CHECK" = true ]; then
      echo "Generated plugin directory is stale: ${plugin_dir#$ROOT_DIR/}" >&2
      return 1
    fi
    rm -rf "$plugin_dir"
  done
}

sync_mcp_plugins() {
  local plugin_dir plugin_name

  [ -d "$MCP_SHARED_DIR" ] || return 0
  clean_mcp_runtime_outputs claude
  clean_mcp_runtime_outputs codex
  clean_mcp_runtime_outputs kiro

  for plugin_dir in "$MCP_SHARED_DIR"/*; do
    [ -d "$plugin_dir" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    sync_mcp_runtime claude "$plugin_dir" "$ROOT_DIR/plugins/mcp/claude/$plugin_name"
    sync_mcp_runtime codex "$plugin_dir" "$ROOT_DIR/plugins/mcp/codex/$plugin_name"
    sync_mcp_runtime kiro "$plugin_dir" "$ROOT_DIR/plugins/mcp/kiro/$plugin_name"
  done
}

# Kiro 配布物は plugin.json を持たないため版数を示す手段がない。
# Claude 版の plugin.json を唯一の基準として VERSION ファイルへ書き出す。
sync_kiro_version() {
  local src="$ROOT_DIR/plugins/ndf-claude/.claude-plugin/plugin.json"
  local dest="$ROOT_DIR/plugins/ndf-kiro/VERSION"
  local version

  [ -f "$src" ] || return 0
  version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$src")"

  if [ "$CHECK" = true ]; then
    if [ ! -f "$dest" ] || [ "$(cat "$dest")" != "$version" ]; then
      echo "Generated file is stale: plugins/ndf-kiro/VERSION" >&2
      return 1
    fi
    return 0
  fi
  printf '%s\n' "$version" > "$dest"
}

sync_runtime_if_present claude "$SHARED_DIR/manifests/claude-skills.txt"
sync_runtime_if_present codex "$SHARED_DIR/manifests/codex-skills.txt"
sync_runtime_if_present kiro "$SHARED_DIR/manifests/kiro-skills.txt"
sync_kiro_version
sync_mcp_plugins

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
