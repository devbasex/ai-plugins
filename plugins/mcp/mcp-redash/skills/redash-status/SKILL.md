---
name: redash-status
description: Redash MCP の設定状況と環境変数の確認
disable-model-invocation: true
user-invocable: true
allowed-tools:
  - Bash
---

# /redash-status

Redash MCP の設定状況を詳細表示します。各 MCP が必要とする環境変数と、未設定の警告を確認できます。

## 実行方法

以下のコマンドを実行してください。

```bash
# この Skill のディレクトリを決める。候補を順に試し、最初に当たったものを絶対パスで採る。
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である（未定義の変数を読まないので `set -u` でも落ちない）。置き換えない runtime では、
# **この bash を実行する前に `<この Skill のディレクトリ>` をランタイムから渡された実際の
# パスへ置き換えること**。置き換えないまま実行しても、その候補が外れるだけで別の場所を
# 読むことはない。Kiro CLI は installer が `.kiro/skills/` へ symlink を張るため、置き換え
# なくてもその位置で当たる。
SKILL_NAME=redash-status
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
SKILL_DIR=
# 明示的に渡されたディレクトリを `.kiro` より先に見る。逆にすると、Kiro の設定を持つ
# リポジトリで Codex や Claude Code を動かしたときに別 runtime の Skill を選ぶ。
for candidate in \
  ${PLUGIN_ROOT:+"$PLUGIN_ROOT/skills/$SKILL_NAME"} \
  "<この Skill のディレクトリ>" \
  ".kiro/skills/$SKILL_NAME" \
  "$HOME/.kiro/skills/$SKILL_NAME"
do
  # この Skill はプラグインルート直下の scripts/ を呼ぶ。Skill ディレクトリの 2 つ上が
  # プラグインルートで、Kiro CLI が張った symlink 越しでも解決先を経由して届く。
  [ -f "$candidate/../../scripts/redash-mcp-config.js" ] || continue
  SKILL_DIR="$(cd "$candidate" && pwd)"
  break
done
[ -n "$SKILL_DIR" ] || { echo "この Skill のディレクトリを解決できない" >&2; exit 1; }
CONFIG="$SKILL_DIR/../../scripts/redash-mcp-config.js"
node "$CONFIG" status
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
