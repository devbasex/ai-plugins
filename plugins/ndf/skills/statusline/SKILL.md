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
<project_dir> [Opus5 61k │ 修正:PR 167k · 検証:#8 42k] takemi@example.com (team)
```

- コンテナ名・ホスト名は出さない。区別は端末やエディタのウィンドウタイトルに任せる
- 角括弧の先頭は利用中モデルの表示名と、メインセッションのコンテキスト使用量。表示名の括弧と空白は落とす (`Opus 5 (1M context)` → `Opus5`)。取得できない場合は `ctx` にフォールバック
- **上限と使用率は出さない。** 現行モデルの上限は Haiku 4.5 (200K) を除いて 1M で、使用量だけで足りる
- `│` の後に実行中のサブエージェントを並べる。statusLine の JSON はメインセッションの値しか持たないため、`<transcript_path から .jsonl を除いたもの>/subagents/agent-<id>.jsonl` の最後の `usage` から読む
  - 直近 60 分以内に記録が更新され、終わっていないものを実行中とみなす。更新の時刻では決めない (子を待つ supervisor や長いコマンドを待つ担当は、実行中でも何分も書き足さない)。止められた担当は、記録が `tool_use` で終わったまま 60 分残ることがある。記録の最後の user / assistant の行が `tool_use` を含まない assistant で、`end_turn` が付いているか 30 秒以上書き足されていなければ終わったとみなす
  - 使用量の多い順に 3 本まで並べ、残りは `+2` のように本数だけを出す。80 桁の端末に収めるため
  - ラベルは `agent-<id>.meta.json` の `description` から空白を除いた先頭 4 文字で、空白を挟んで使用量を続ける。project_dir と 3 本を並べても 80 桁に収めるため。種類名 (`agentType`) はほとんどが `general-purpose` で見分けに使えない。説明が無ければ ID の先頭を出す
- 末尾に接続先を薄い色で出す。Anthropic のアカウントは `claude auth status` の `email` にコントラクトの種別 `subscriptionType`（team / pro / max。同じメールアドレスに Team と個人のコントラクトがありうるため）を括弧で添えたもの（0.4 秒かかるので `~/.claude/.ndf-statusline-auth.json` に 60 秒メモする。呼び出しは 1 秒で打ち切る）。`CLAUDE_CODE_USE_BEDROCK` が空でなければ `bedrock`（`AWS_PROFILE` があれば `bedrock:<profile>`）。取れなければ何も出さない
- メイン・サブエージェントとも、500k を超えたら使用量を赤で表示する。上限が 200K 以下のモデル (Haiku 4.5) は 150k で赤にする。メインは入力の `context_window.context_window_size`、サブエージェントは記録のモデル名で判定する
- この色は窓に対して危険な水準を示す表示の警告で、hook が Tool の呼び出しを止める運用上限（既定 600,000。`NDF_CONTEXT_LIMIT` で変わる）とは意味が違う（`development-workflow` の `references/context-window.md` の「運用上限で hook が止める」）
- 入力の窓の大きさとモデルを状態ディレクトリ（`NDF_CONTEXT_DIR` → `${XDG_STATE_HOME}/ndf/context` → `~/.local/state/ndf/context`）の `sessions/<セッション>.window.json` へ書く（変わったときだけ）。今の hook はこのファイルを読まない
  - サブエージェントの記録の置く場所と形は公式ドキュメントに無い内部の仕様で、Claude Code の更新で変わりうる。読めなければ何も出さない

## 使用方法

引数に応じて以下のコマンドを実行する:

```bash
# ndf の有無の確かめ（development-workflow/references/scripts-lookup.md）
command -v ndf >/dev/null 2>&1 || { echo "ndf が PATH に無い" >&2; exit 3; }

# 状態確認 (引数なし or status)
ndf statusline status

# NDF 標準 statusline に切り替え (既存設定は自動バックアップ)
ndf statusline set

# 元の設定に復元 (バックアップが無く NDF 標準を使っていれば statusLine 設定を削除)
ndf statusline restore
```

実行後、結果 JSON の `summary` をそのままユーザーに報告する。
statusline の変更は次回セッション開始時 (または statusline 再描画時) に反映される。

## 自動デフォルト設定 (SessionStart hook)

プラグインインストール後の初回セッション開始時に `ndf statusline ensure` が実行され、
**statusLine が未設定の場合のみ** NDF 標準 statusline が設定される。
既に statusline が設定されている場合はそちらが優先され、何も変更しない。
NDF 標準 statusline の利用中は、プラグイン更新時にスクリプト
(`~/.claude/ndf-statusline.sh`) の内容が自動で追従する。

NDF 標準 statusline は `refreshInterval: 5` (秒) を持つ。メインセッションがバックグラウンドの
サブエージェントを待つ間は再描画のイベントが起きず、サブエージェントの使用量が止まって見える
ためである。既に NDF 標準を使っている設定に `refreshInterval` が無ければ、`ensure` と `set` が
足す。利用者が書いた値は変えない。
