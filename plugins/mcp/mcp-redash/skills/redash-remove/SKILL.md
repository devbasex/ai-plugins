---
name: redash-remove
description: 指定 suffix の Redash MCP を project .mcp.json から削除する
disable-model-invocation: true
user-invocable: true
arguments:
  - name: suffix
    description: "削除する環境識別子（dev, stg, prod2, sandbox など）"
allowed-tools:
  - Bash
---

# /redash-remove

指定 suffix の Redash MCP をプロジェクトの `.mcp.json` から削除します。

## 実行方法

以下のコマンドを実行してください。`$ARGUMENTS` にはユーザーが指定した suffix が入ります。

```bash
# この Skill のディレクトリを決める。候補を順に試し、最初に当たったものを絶対パスで採る。
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である（未定義の変数を読まないので `set -u` でも落ちない）。置き換えない runtime では、
# **この bash を実行する前に `<この Skill のディレクトリ>` をランタイムから渡された実際の
# パスへ置き換えること**。置き換えないまま実行しても、その候補が外れるだけで別の場所を
# 読むことはない。Kiro CLI は installer が `.kiro/skills/` へ symlink を張るため、置き換え
# なくてもその位置で当たる。
SKILL_NAME=redash-remove
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
node "$CONFIG" remove "$ARGUMENTS"
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
