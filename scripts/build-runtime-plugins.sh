#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/build-runtime-plugins.sh [--check]

Generate the tracked build outputs: Codex implicit-invocation policies for
single-directory plugins, and the runtime copies of MCP plugins.

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


sync_codex_skill_policies() {
  local family="$1"
  local plugin_dir="$ROOT_DIR/plugins/$family"
  local manifest="$plugin_dir/manifests/codex-skills.txt"

  [ -f "$manifest" ] || return 0
  python3 - "$plugin_dir/skills" "$manifest" "$CHECK" <<'PY'
import sys
from pathlib import Path

skills_dir, manifest_path, check = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "true"


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
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


published = {
    line.split("#", 1)[0].strip()
    for line in manifest_path.read_text(encoding="utf-8").splitlines()
    if line.split("#", 1)[0].strip()
}

expected: dict[Path, str] = {}
for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file() or skill_dir.name not in published:
        continue
    fields = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if fields.get("disable-model-invocation") != "true":
        continue
    lines = ["policy:", "  allow_implicit_invocation: false"]
    argument_hint = fields.get("argument-hint")
    if argument_hint:
        lines += ["interface:", f"  default_prompt: {yaml_double_quoted(argument_hint)}"]
    expected[skill_dir / "agents" / "openai.yaml"] = "\n".join(lines) + "\n"

actual = {p for p in skills_dir.glob("*/agents/openai.yaml")}
stale = actual - set(expected)

failed = False
for path, content in expected.items():
    if check:
        if not path.is_file():
            print(f"Generated file missing: {path}", file=sys.stderr)
            failed = True
        elif path.read_text(encoding="utf-8") != content:
            print(f"Generated file is out of date: {path}", file=sys.stderr)
            failed = True
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

for path in sorted(stale):
    if check:
        print(f"Generated file is stale: {path}", file=sys.stderr)
        failed = True
    else:
        path.unlink()
        if not any(path.parent.iterdir()):
            path.parent.rmdir()

raise SystemExit(1 if failed else 0)
PY
}

# Kiro CLI にはプラグイン機構が無いため、MCP プラグインごとに installer を置く。内容は
# プラグイン名に依存しないので、雛形を 1 つ持って各プラグインの dev.kiro/ へ書き出す。
# Agent Plugins 仕様 §8.2 のクライアント拡張ディレクトリに当たる。
write_kiro_mcp_installer() {
  local plugin_dir="$1"
  local installer="$plugin_dir/dev.kiro/install.sh"
  local tmp

  tmp="$(mktemp)"
  cat > "$tmp" <<'EOF'
#!/usr/bin/env bash
# MCP Plugin Installer for Kiro CLI
# Usage: bash plugins/mcp/<プラグイン名>/dev.kiro/install.sh [--project PATH] [--dry-run]
#
# scripts/build-runtime-plugins.sh が生成します。直接編集しないでください。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
PROJECT_ROOT="$(pwd)"
KIRO_DIR="$PROJECT_ROOT/.kiro"
KIRO_AGENT_FILE="$KIRO_DIR/agents/default.json"
KIRO_PLUGIN_LINK="$KIRO_DIR/mcp_runtime/$PLUGIN_NAME"
KIRO_SKILLS_DIR="$KIRO_DIR/skills"
TARGET_MCP="$PROJECT_ROOT/.mcp.json"
SOURCE_MCP="$PLUGIN_DIR/.mcp.json"
SOURCE_HOOKS="$PLUGIN_DIR/hooks/hooks.json"
SOURCE_SKILLS="$PLUGIN_DIR/skills"
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
ln -sfn "$PLUGIN_DIR" "$KIRO_PLUGIN_LINK"

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

python3 - "$SOURCE_MCP" "$TARGET_MCP" "$SOURCE_HOOKS" "$KIRO_AGENT_FILE" "$KIRO_PLUGIN_LINK" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
hooks_path = Path(sys.argv[3])
agent_path = Path(sys.argv[4])
plugin_link = sys.argv[5]
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
    # Kiro にはプラグインルートを示す環境変数が無い。hook の command が使う
    # プレースホルダを、installer が張った symlink の絶対パスへ置き換える。
    placeholder = "${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}}"
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
                if not command:
                    continue
                command = command.replace(placeholder, plugin_link)
                if command in existing:
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

  if [ "$CHECK" = true ]; then
    if [ ! -f "$installer" ] || ! diff -q "$tmp" "$installer" >/dev/null; then
      echo "Generated file is out of date: ${installer#$ROOT_DIR/}" >&2
      rm -f "$tmp"
      return 1
    fi
    rm -f "$tmp"
    return 0
  fi
  mkdir -p "$(dirname "$installer")"
  mv "$tmp" "$installer"
  chmod +x "$installer"
}

sync_mcp_plugins() {
  local plugin_dir

  for plugin_dir in "$ROOT_DIR"/plugins/mcp/*; do
    [ -f "$plugin_dir/.mcp.json" ] || continue
    write_kiro_mcp_installer "$plugin_dir"
  done
}

for dir in "$ROOT_DIR"/plugins/*; do
  [ -d "$dir/manifests" ] || continue
  family="$(basename "$dir")"
  case "$family" in
    *-shared|*-claude|*-codex|*-kiro) continue ;;
  esac
  sync_codex_skill_policies "$family"
done

sync_mcp_plugins

if [ "$CHECK" = true ]; then
  echo "runtime plugin generated files are up to date"
else
  echo "runtime plugin generated files synchronized"
fi
