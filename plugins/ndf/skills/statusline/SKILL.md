---
name: statusline
description: "Switch, restore, or inspect the NDF statusline in the Claude Code settings. 明示指示のみで実行する。Use when changing the statusline（statusline切り替え・ステータスライン）."
argument-hint: "status | set | restore"
disable-model-invocation: true
allowed-tools:
  - Bash
---

# Statusline 切り替えコマンド

NDF 標準 statusline (project_dir + メインとサブエージェントのコンテキスト使用量) と
既存のカスタム statusline を切り替える。

## 表示内容

```
<project_dir> [Opus5 61k │ 修正:PR 167k · 検証:#8 42k]
```

- コンテナ名・ホスト名は出さない。区別は端末やエディタのウィンドウタイトルに任せる
- 角括弧の先頭は利用中モデルの表示名と、メインセッションのコンテキスト使用量。表示名の括弧と空白は落とす (`Opus 5 (1M context)` → `Opus5`)。取得できない場合は `ctx` にフォールバック
- **上限と使用率は出さない。** 現行モデルの上限は Haiku 4.5 (200K) を除いて 1M で、使用量だけで足りる
- `│` の後に実行中のサブエージェントを並べる。statusLine の JSON はメインセッションの値しか持たないため、`<transcript_path から .jsonl を除いたもの>/subagents/agent-<id>.jsonl` の最後の `usage` から読む
  - 直近 2 分以内に記録が更新され、終わっていないものを実行中とみなす。記録の最後の user / assistant の行が `tool_use` を含まない assistant で、`end_turn` が付いているか 30 秒以上書き足されていなければ終わったとみなす
  - 使用量の多い順に 3 本まで並べ、残りは `+2` のように本数だけを出す。80 桁の端末に収めるため
  - ラベルは `agent-<id>.meta.json` の `description` から空白を除いた先頭 4 文字で、空白を挟んで使用量を続ける。project_dir と 3 本を並べても 80 桁に収めるため。種類名 (`agentType`) はほとんどが `general-purpose` で見分けに使えない。説明が無ければ ID の先頭を出す
- メイン・サブエージェントとも、500k を超えたら使用量を赤で表示する。Haiku 4.5 (200K) のサブエージェントは 150k で赤にする
  - サブエージェントの記録の置き場所と形は公式ドキュメントに無い内部の仕様で、Claude Code の更新で変わりうる。読めなければ何も出さない

## 使用方法

引数に応じて以下のコマンドを実行する:

```bash
# この Skill のディレクトリを決める。候補を順に試し、最初に当たったものを絶対パスで採る。
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である（未定義の変数を読まないので `set -u` でも落ちない）。置き換えない runtime では、
# **この bash を実行する前に `<この Skill のディレクトリ>` をランタイムから渡された実際の
# パスへ置き換えること**。置き換えないまま実行しても、その候補が外れるだけで別の場所を
# 読むことはない。Kiro CLI は installer が `.kiro/skills/` へ symlink を張るため、置き換え
# なくてもその位置で当たる。
SKILL_NAME=statusline
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
  # この Skill だけはプラグインルート直下の scripts/ を呼ぶ。Skill ディレクトリの 2 つ上が
  # プラグインルートで、Kiro CLI が張った symlink 越しでも解決先を経由して届く。
  [ -f "$candidate/../../scripts/statusline-switch.sh" ] || continue
  SKILL_DIR="$(cd "$candidate" && pwd)"
  break
done
[ -n "$SKILL_DIR" ] || { echo "この Skill のディレクトリを解決できない" >&2; exit 1; }
SWITCH="$SKILL_DIR/../../scripts/statusline-switch.sh"

# 状態確認 (引数なし or status)
bash "$SWITCH" status

# NDF 標準 statusline に切り替え (既存設定は自動バックアップ)
bash "$SWITCH" set

# 元の設定に復元 (バックアップが無ければ statusLine 設定を削除)
bash "$SWITCH" restore
```

実行後、スクリプトの出力をそのままユーザーに報告する。
statusline の変更は次回セッション開始時 (または statusline 再描画時) に反映される。

## 自動デフォルト設定 (SessionStart hook)

プラグインインストール後の初回セッション開始時に `statusline-switch.sh ensure` が実行され、
**statusLine が未設定の場合のみ** NDF 標準 statusline が設定される。
既に statusline が設定されている場合はそちらが優先され、何も変更しない。
NDF 標準 statusline の利用中は、プラグイン更新時にスクリプト
(`~/.claude/ndf-statusline.sh`) の内容が自動で追従する。

NDF 標準 statusline は `refreshInterval: 5` (秒) を持つ。メインセッションがバックグラウンドの
サブエージェントを待つ間は再描画のイベントが起きず、サブエージェントの使用量が止まって見える
ためである。既に NDF 標準を使っている設定に `refreshInterval` が無ければ、`ensure` と `set` が
足す。利用者が書いた値は変えない。
