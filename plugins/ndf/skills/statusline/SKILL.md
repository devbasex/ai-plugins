---
name: statusline
description: "Switch, restore, or inspect the NDF statusline in the Claude Code settings. 明示指示のみで実行する。Use when changing the statusline（statusline切り替え・ステータスライン）."
argument-hint: "status | set | restore"
disable-model-invocation: true
allowed-tools:
  - Bash
---

# Statusline 切り替えコマンド

NDF 標準 statusline (コンテナ名/ホスト名 + project_dir + コンテキスト使用率) と
既存のカスタム statusline を切り替える。

## 表示内容

```
<コンテナ名|ホスト名> <project_dir> [<モデル名>: 12.3k / 200k tokens (6%)]
```

- コンテナ環境 (`/.dockerenv` あり) ではコンテナ名、それ以外ではホスト名を表示
- `CONTAINER_NAME` 環境変数があればそちらを優先
- 角括弧内のラベルは利用中モデルの表示名 (例: `Opus 4.8`)。取得できない場合は `ctx` にフォールバック

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
