---
name: statusline
description: "Switch, restore, or inspect the NDF statusline."
when_to_use: "statuslineを切り替え/復元/確認したいとき。Triggers: 'statusline', 'ステータスライン', 'statusline 切り替え', 'statusline 戻す'"
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
PLUGIN_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}"

# 状態確認 (引数なし or status)
bash "${PLUGIN_ROOT}/scripts/statusline-switch.sh" status

# NDF 標準 statusline に切り替え (既存設定は自動バックアップ)
bash "${PLUGIN_ROOT}/scripts/statusline-switch.sh" set

# 元の設定に復元 (バックアップが無ければ statusLine 設定を削除)
bash "${PLUGIN_ROOT}/scripts/statusline-switch.sh" restore
```

実行後、スクリプトの出力をそのままユーザーに報告する。
statusline の変更は次回セッション開始時 (または statusline 再描画時) に反映される。

## 自動デフォルト設定 (SessionStart hook)

プラグインインストール後の初回セッション開始時に `statusline-switch.sh ensure` が実行され、
**statusLine が未設定の場合のみ** NDF 標準 statusline が設定される。
既に statusline が設定されている場合はそちらが優先され、何も変更しない。
NDF 標準 statusline の利用中は、プラグイン更新時にスクリプト
(`~/.claude/ndf-statusline.sh`) の内容が自動で追従する。
