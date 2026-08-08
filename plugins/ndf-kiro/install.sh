#!/usr/bin/env bash
# NDF Plugin Installer for Kiro CLI
# Usage: bash plugins/ndf-kiro/install.sh [--project PATH] [--scope workspace|global]
#                                        [--set-default] [--yes]
#                                        [--with-slack] [--with-codex] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR"
AGENT_NAME="ndf"
TEMPLATE_FILE="$PLUGIN_DIR/agents/$AGENT_NAME.json.template"
PLUGIN_SKILLS_DIR="$PLUGIN_DIR/skills"
PLUGIN_PROMPTS_DIR="$PLUGIN_DIR/prompts"
POLICY_SKILL_FILE="$PLUGIN_SKILLS_DIR/ndf-policies/SKILL.md"

# Parse options
PROJECT_ROOT="$(pwd)"
PROJECT_GIVEN=false
SCOPE="workspace"
SET_DEFAULT=false
ASSUME_YES=false
WITH_SLACK=false
WITH_CODEX=false
DRY_RUN=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --project)
      [ "$#" -ge 2 ] || { echo "ERROR: --project requires a path" >&2; exit 2; }
      PROJECT_ROOT="$(cd "$2" && pwd)"
      PROJECT_GIVEN=true
      shift
      ;;
    --scope)
      [ "$#" -ge 2 ] || { echo "ERROR: --scope requires workspace or global" >&2; exit 2; }
      SCOPE="$2"
      shift
      ;;
    --set-default) SET_DEFAULT=true ;;
    --yes|-y) ASSUME_YES=true ;;
    --with-slack) WITH_SLACK=true ;;
    --with-codex) WITH_CODEX=true ;;
    --dry-run) DRY_RUN=true ;;
    --help|-h)
      echo "Usage: bash plugins/ndf-kiro/install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --project PATH   install into PATH instead of current directory (--scope workspace のみ)"
      echo "  --scope SCOPE    workspace（既定, プロジェクトの .kiro/）または global（~/.kiro/）"
      echo "  --set-default    kiro-cli の既定エージェントを ndf に切り替える（オプトイン）"
      echo "  -y, --yes        --set-default の確認プロンプトを省略する"
      echo "  --with-slack     stopフックにSlack通知を追加"
      echo "  --with-codex     Codex CLI直接実行用プロンプトを追加"
      echo "  --dry-run        書き込みを行わず実行内容を表示"
      echo "  -h, --help       このヘルプを表示"
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

case "$SCOPE" in
  workspace)
    KIRO_DIR="$PROJECT_ROOT/.kiro"
    ;;
  global)
    [ -n "${HOME:-}" ] || { echo "ERROR: --scope global には HOME が必要です" >&2; exit 2; }
    KIRO_DIR="$HOME/.kiro"
    if [ "$PROJECT_GIVEN" = true ]; then
      echo "WARN: --scope global では --project は使用されません" >&2
    fi
    ;;
  *)
    echo "ERROR: invalid --scope: $SCOPE (workspace|global)" >&2
    exit 2
    ;;
esac

SKILLS_DIR="$KIRO_DIR/skills"
PROMPTS_DIR="$KIRO_DIR/prompts"
STEERING_FILE="$KIRO_DIR/steering/ndf-policies.md"
AGENT_FILE="$KIRO_DIR/agents/$AGENT_NAME.json"
LEGACY_AGENT_FILE="$KIRO_DIR/agents/default.json"

echo "=== NDF Plugin Installer for Kiro CLI ==="
echo "  スコープ: $SCOPE ($KIRO_DIR)"

if [ ! -d "$PLUGIN_SKILLS_DIR" ]; then
  echo "ERROR: $PLUGIN_SKILLS_DIR が見つかりません。先に scripts/build-runtime-plugins.sh を実行してください。" >&2
  exit 1
fi
if [ ! -f "$TEMPLATE_FILE" ]; then
  echo "ERROR: $TEMPLATE_FILE が見つかりません" >&2
  exit 1
fi
if [ ! -f "$POLICY_SKILL_FILE" ]; then
  echo "ERROR: $POLICY_SKILL_FILE が見つかりません" >&2
  exit 1
fi

# --- Step 1: Create symlinks in <scope>/skills/ ---
echo "Skills シンボリックリンクを作成中..."
SKILL_COUNT=0
if [ "$DRY_RUN" = false ]; then
  mkdir -p "$SKILLS_DIR"
  while IFS= read -r skill_link; do
    target="$(readlink "$skill_link")"
    case "$target" in
      /*) target_abs="$target" ;;
      *) target_abs="$(realpath -m "$(dirname "$skill_link")/$target")" ;;
    esac
    plugin_skills_abs="$(realpath -m "$PLUGIN_SKILLS_DIR")"
    case "$target_abs" in
      "$plugin_skills_abs"/*) rm -f "$skill_link" ;;
    esac
  done < <(find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type l | sort)
fi

while IFS= read -r src_dir; do
  skill_name="$(basename "$src_dir")"

  if [ ! -f "$src_dir/SKILL.md" ]; then
    echo "  SKIP: $skill_name (SKILL.mdなし)"
    continue
  fi

  # ndf-policies は Step 3 で steering として展開する。Skill としてもリンクすると
  # Kiro 組み込みルールの Skill 読み込みと steering 読み込みで文脈へ二重注入されるため、
  # ここではリンクしない。manifest には残す（steering の生成元として必要なため）。
  if [ "$skill_name" = "ndf-policies" ]; then
    echo "  SKIP: $skill_name (steering として配置)"
    continue
  fi

  if [ "$DRY_RUN" = false ]; then
    ln -sfn "$PLUGIN_SKILLS_DIR/$skill_name" "$SKILLS_DIR/$skill_name"
  fi
  echo "  linked: $skill_name"
  SKILL_COUNT=$((SKILL_COUNT + 1))
done < <(find "$PLUGIN_SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

# --- Step 2: Create prompts in <scope>/prompts/ for workflow skills ---
echo "ワークフロープロンプトを作成中..."
if [ "$DRY_RUN" = false ]; then
  mkdir -p "$PROMPTS_DIR"
  if [ "$WITH_CODEX" = false ]; then
    rm -f "$PROMPTS_DIR/codex.md"
  fi
fi

while IFS= read -r prompt_file; do
  prompt_name="$(basename "$prompt_file")"
  [ "$prompt_name" = "codex.md" ] && [ "$WITH_CODEX" = false ] && continue
  if [ "$DRY_RUN" = false ]; then
    cp "$prompt_file" "$PROMPTS_DIR/$prompt_name"
  fi
  echo "  prompt: ${prompt_name%.md}"
done < <(find "$PLUGIN_PROMPTS_DIR" -maxdepth 1 -type f -name '*.md' | sort)

if [ "$WITH_CODEX" = true ] && [ ! -f "$PLUGIN_PROMPTS_DIR/codex.md" ]; then
  echo "ERROR: $PLUGIN_PROMPTS_DIR/codex.md が見つかりません" >&2
  exit 1
fi

if [ "$WITH_SLACK" = true ]; then echo "Slack通知: 有効"; else echo "Slack通知: 無効 (--with-slack で有効化)"; fi
if [ "$WITH_CODEX" = true ]; then echo "Codex CLI連携: 有効"; else echo "Codex CLI連携: 無効 (--with-codex で有効化)"; fi

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "DRY RUN: 書き込みは行いませんでした"
  echo "  エージェント設定: $AGENT_FILE"
  echo "  常時指示: $STEERING_FILE"
  echo "  Skills数: $SKILL_COUNT"
  exit 0
fi

# --- Step 3: Generate steering (always-on instructions) ---
# steering はエージェント選択に依存せず読み込まれるため、常時指示はここへ置く。
mkdir -p "$(dirname "$STEERING_FILE")"
python3 - "$POLICY_SKILL_FILE" "$STEERING_FILE" <<'PY'
import sys
from pathlib import Path

source, dest = (Path(p) for p in sys.argv[1:3])
text = source.read_text(encoding="utf-8")
if text.startswith("---\n"):
    end = text.find("\n---\n", 3)
    if end != -1:
        text = text[end + len("\n---\n"):]
body = text.strip("\n")
header = (
    "<!-- plugins/ndf-kiro/install.sh が生成します。直接編集しないでください。 -->\n"
    "<!-- 編集元: plugins/ndf-shared/skills/ndf-policies/SKILL.md -->\n"
)
dest.write_text(f"{header}\n{body}\n", encoding="utf-8")
PY
echo "常時指示を生成: $STEERING_FILE"

# --- Step 4: Migrate legacy default agent ---
if [ -f "$LEGACY_AGENT_FILE" ]; then
  cp "$LEGACY_AGENT_FILE" "${LEGACY_AGENT_FILE}.bak"
  echo ""
  echo "WARN: 旧エージェント設定 $LEGACY_AGENT_FILE を検出しました。"
  echo "      バックアップ: ${LEGACY_AGENT_FILE}.bak"
  if python3 -c '
import json, sys
try:
    config = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
sys.exit(0 if config.get("name") == "default" and "NDF" in (config.get("description") or "") else 1)
' "$LEGACY_AGENT_FILE"; then
    echo "      これは旧版 NDF installer の生成物です。移行手順:"
    echo "        1. 独自に追記した設定があればバックアップから $AGENT_FILE へ写す"
    echo "        2. rm $LEGACY_AGENT_FILE ${LEGACY_AGENT_FILE}.bak"
  else
    echo "      NDF 以外が管理している設定です。移行手順:"
    echo "        1. 必要な mcpServers / hooks を $AGENT_FILE へ写す"
    echo "        2. 不要になったら rm $LEGACY_AGENT_FILE ${LEGACY_AGENT_FILE}.bak"
    echo "      Kiro 用 MCP プラグインの installer は default.json を更新するため、"
    echo "      MCP を併用する場合は上記の写し替えが必要です。"
  fi
  echo ""
fi

# --- Step 5: Generate agent config ---
mkdir -p "$KIRO_DIR/agents"

if [ -f "$AGENT_FILE" ]; then
  cp "$AGENT_FILE" "${AGENT_FILE}.bak"
  echo "既存設定をバックアップ: ${AGENT_FILE}.bak"
fi

python3 - "$TEMPLATE_FILE" "$WITH_SLACK" "$WITH_CODEX" "$AGENT_FILE" "$SCRIPT_DIR" <<'PY'
import json
import shlex
import sys
from pathlib import Path

template_file, with_slack, with_codex, agent_file, script_dir = sys.argv[1:6]
with open(template_file, encoding="utf-8") as f:
    config = json.load(f)

hooks = config.setdefault("hooks", {})
if with_slack == "true":
    slack_script = Path(script_dir) / "scripts" / "slack-notify.js"
    hooks["stop"] = [
        {
            "command": f"node {shlex.quote(str(slack_script))} session_end",
            "timeout_ms": 70000,
        }
    ]
else:
    hooks.pop("stop", None)

if with_codex == "true":
    config["mcpServers"] = {
        "codex": {
            "command": "codex",
            "args": ["mcp-server"],
            "env": {},
        }
    }
else:
    config.pop("mcpServers", None)

with open(agent_file, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

# --- Step 6: Optionally switch the default agent ---
if [ "$SET_DEFAULT" = true ]; then
  echo ""
  if ! command -v kiro-cli >/dev/null 2>&1; then
    echo "ERROR: kiro-cli が見つからないため既定エージェントを変更できません" >&2
    exit 1
  fi
  # kiro-cli は workspace エージェントを cwd 配下の .kiro/agents からのみ検出する。
  # 呼び出し元 cwd のままだと、--project で別ディレクトリへ導入したエージェントを
  # 見つけられない（または同名の別エージェントを既定にしてしまう）。生成した
  # $AGENT_FILE を確実に指すディレクトリで kiro-cli を実行する。
  #   workspace: 導入先プロジェクトルート ($PROJECT_ROOT/.kiro/agents)
  #   global:    $HOME （$HOME/.kiro/agents = 生成先。global エージェントはどこからでも
  #              解決できるが、cwd 側の同名 workspace エージェントに隠されないようにする）
  case "$SCOPE" in
    workspace) KIRO_CWD="$PROJECT_ROOT" ;;
    *) KIRO_CWD="$HOME" ;;
  esac
  esc="$(printf '\033')"
  # kiro-cli 2.16.1 の agent list は一覧を標準エラー出力へ書く
  kiro_default_agent() {
    (cd "$KIRO_CWD" && kiro-cli agent list 2>&1) \
      | sed -e "s/${esc}\\[[0-9;]*m//g" \
      | awk '/^\*/ { print $2; exit }'
  }
  # 表示用の取得は失敗しても続行する（未ログイン等でも set-default の結果は後段で検証する）
  current_default="$(kiro_default_agent || true)"
  echo "既定エージェントの操作ディレクトリ: $KIRO_CWD"
  echo "現在の既定エージェント: ${current_default:-不明}"
  echo "変更後の既定エージェント: $AGENT_NAME"
  proceed=true
  if [ "$ASSUME_YES" = false ]; then
    if [ -t 0 ]; then
      printf '既定エージェントを %s に変更しますか? [y/N]: ' "$AGENT_NAME"
      read -r answer
      case "$answer" in
        [yY]|[yY][eE][sS]) ;;
        *) proceed=false ;;
      esac
    else
      echo "確認入力を取得できないため、--set-default の指定を承認とみなして続行します"
    fi
  fi
  if [ "$proceed" = true ]; then
    (cd "$KIRO_CWD" && kiro-cli agent set-default "$AGENT_NAME")
    # kiro-cli 2.16.1 の set-default はエージェント未検出でも終了コード 0 を返すため、
    # 反映結果を agent list で検証する。
    if [ "$(kiro_default_agent || true)" != "$AGENT_NAME" ]; then
      echo "ERROR: 既定エージェントを $AGENT_NAME に変更できませんでした（$KIRO_CWD で検出できず）" >&2
      exit 1
    fi
    echo "既定エージェントを $AGENT_NAME に変更しました（元に戻す: kiro-cli agent set-default ${current_default:-kiro_default}）"
  else
    echo "既定エージェントは変更しませんでした"
  fi
fi

echo ""
echo "=== インストール完了 ==="
echo "  エージェント設定: $AGENT_FILE"
echo "  常時指示: $STEERING_FILE"
echo "  Skills数: $SKILL_COUNT (シンボリックリンク: $SKILLS_DIR)"
echo ""
echo "Kiro CLIを起動して動作確認してください:"
echo "  kiro-cli chat --agent $AGENT_NAME"
if [ "$SET_DEFAULT" = false ]; then
  echo ""
  echo "既定エージェントとして起動したい場合は --set-default を付けて再実行してください。"
fi
