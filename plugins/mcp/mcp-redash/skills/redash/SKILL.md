---
name: redash
description: "Redash MCP を project の .mcp.json に追加・削除し、一覧と設定状況を表示する。明示指示のみで実行する。Use when Redash の環境を足す・消す・確かめるとき（/redash add dev・/redash list・/redash status・/redash remove dev）."
argument-hint: "add|list|remove|status [suffix]"
disable-model-invocation: true
user-invocable: true
arguments:
  - name: action
    description: "操作（add / list / remove / status）"
  - name: suffix
    description: "環境識別子（dev, stg, prod2, sandbox など）。add と remove で指定する"
allowed-tools:
  - Bash
---

# /redash

`/redash <add|list|remove|status> [suffix]` で Redash MCP を操作します。

| 操作 | 内容 |
|------|------|
| `add <suffix>` | 任意 suffix の Redash MCP をプロジェクトの `.mcp.json` に追加する |
| `remove <suffix>` | 指定 suffix の Redash MCP をプロジェクトの `.mcp.json` から削除する |
| `list` | 現在有効な Redash MCP を一覧表示する |
| `status` | 各 MCP が必要とする環境変数と、未設定の警告を表示する |

## 実行方法

以下のコマンドを実行してください。`$ARGUMENTS` にはユーザーが指定した操作と suffix が入ります。

```bash
# この Skill のディレクトリを決める。候補を順に試し、最初に当たったものを絶対パスで採る。
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である（未定義の変数を読まないので `set -u` でも落ちない）。置き換えない runtime では、
# **この bash を実行する前に `<この Skill のディレクトリ>` をランタイムから渡された実際の
# パスへ置き換えること**。置き換えないまま実行しても、その候補が外れるだけで別の場所を
# 読むことはない。Kiro CLI は installer が `.kiro/skills/` へ symlink を張るため、置き換え
# なくてもその位置で当たる。
SKILL_NAME=redash
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
# 操作と suffix を分ける。操作の検査は redash-mcp-config.js が行い、知らない操作なら使い方を
# 出して失敗する。
ACTION= SUFFIX=
read -r ACTION SUFFIX _ <<'EOF' || true
$ARGUMENTS
EOF
node "$CONFIG" "$ACTION" ${SUFFIX:+"$SUFFIX"}
```

## 実行後

コマンドの出力をそのままユーザーに表示してください。
