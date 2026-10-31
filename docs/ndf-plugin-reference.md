# NDF Plugin リファレンス

## 概要

NDF は Claude Code / Codex 向けの開発支援プラグイン群です。marketplace 上の plugin 名はどちらでも `ndf` を維持し、配布物は `plugins/ndf/` の 1 ディレクトリにまとまっています。配布先は Claude Code と Codex の 2 つで、Kiro CLI と agy へは配りません。この 2 つは cross-review / cross-refactoring の相手役として、ホスト（Claude Code・Codex）から CLI プロセスで起動されるだけです（「相手役のランタイム」）。

| ランタイム | 導入方法 | 公開 Skill |
|---|---|---|
| Claude Code | `.claude-plugin/marketplace.json` の `ndf` | 49 個 |
| Codex | `.claude-plugin/marketplace.json` の `ndf` | 45 個 |

Skill の実体は `plugins/ndf/skills/` の 1 箇所だけです。ランタイム別の複製はありません。Skill や
共通スクリプトを変更する場合は `plugins/ndf/` を直接編集します。

## ディレクトリ構造

```text
plugins/ndf/
├── .claude-plugin/plugin.json   # Claude Code（agents / hooks / skills 配列）
├── .codex-plugin/plugin.json    # Codex（hooks / skills 配列）
├── skills/                      # 配布 Skill の唯一の実体（49 個）
├── manifests/                   # ランタイム別の配布 Skill 一覧
├── agents/                      # Claude Code のサブエージェント（専門 8 個と、3 層の定義 3 個（supervisor 2・worker 1））
├── hooks/claude.json            # Claude Code の SessionStart / Stop hook
├── hooks/codex.json             # Codex の Stop hook
└── scripts/                     # hook と Skill から呼ぶスクリプト
```

生成物は
`skills/<名前>/agents/openai.yaml`（Codex の暗黙起動ポリシー）だけで、commit 対象です。利用者が
plugin install 時に build を実行する必要はありません。

## Runtime 別の同梱内容

| Runtime | 読むもの |
|---|---|
| Claude Code | `.claude-plugin/plugin.json`（`agents/` 11 個（専門 8 個と、3 層の定義 3 個（supervisor 2・worker 1））、`hooks/claude.json`、`skills` 配列 49 個）、`scripts/` |
| Codex | `.codex-plugin/plugin.json`（`hooks/codex.json`、`skills` 配列 45 個）、`scripts/` |

同じディレクトリを 2 ランタイムが共有しますが、読む対象はマニフェストが決めるため、
公開される Skill と hook はランタイムごとに異なります。Claude Code 専用の agents / statusline /
transcript retention hook は Codex の `skills` 配列と `hooks/codex.json` には入りません。

## Skills

NDF の Skill 実装は `plugins/ndf/skills/` にあります。公開セットは manifest で管理します。

| Manifest | 読む側 |
|---|---|
| `plugins/ndf/manifests/claude-skills.txt` | `.claude-plugin/plugin.json` の `skills` 配列 |
| `plugins/ndf/manifests/codex-skills.txt` | `.codex-plugin/plugin.json` の `skills` 配列 |

**`skills/` に置く Skill は、少なくとも 1 つの manifest へ載せます。** 配らない Skill の置き場所
（`optional-skills/`）は v10.5.0 で無くしました（#116）。

主な Skill 領域:

- PR / review workflow: `pr`, `pr-tests`, `fix`, `pr-review`, `cross-review`, `cross-refactoring`
- branch / release workflow: `worktree`, `deploy`, `cherry-pick-pr`, `merged`, `release`,
  `release-verification`
- planning / documentation: `implementation-plan`, `issue-plan-strategy`, `plan-to-spec`,
  `document-style`, `investigation-rules`
- development methodology: `development-workflow`, `requirements-design`, `tdd-cycle`,
  `refactoring`, `quality-gates`, `retrospective`, `out-of-scope`
- quality / execution: `docker-container-access`, `qa-security-scan`
  （Playwright による E2E テストは v7.0.0 で `playwright-kit` プラグインへ分離）
- external services: `external-ai`, `official-skills-autoloader`（Claude Code のみ）
- runtime 設定: `statusline`（Claude Code のみ）、`install-wrapper` / `restart`（カットポイントで claude を起動し直すラッパー。Claude Code のみ）
- policy: `ndf-policies`, `problem-solving`, `logging-guidelines`

- 外部サービスと測定: `google-workspace`, `skill-stats`, `ml-model-structure`,
  `notion-writing`（v10.5.0 で配布へ回しました。#116 / #144）

## MCP Plugins

MCP plugin も `plugins/mcp/<plugin-name>/` の 1 ディレクトリにまとまっています。サーバ定義は `.mcp.json` の 1 箇所だけで、Claude Code と Codex が同じファイルを読みます（Codex だけ `.codex.mcp.json` を読ませる例外があります）。

Claude Code と Codex は marketplace から同じ plugin 名で install します。

## Build / Validation

```bash
bash scripts/build-runtime-plugins.sh
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
claude plugin validate plugins/ndf
python3 -m json.tool plugins/ndf/.codex-plugin/plugin.json >/dev/null
bash scripts/runtime-smoke-test.sh --runtime claude
bash scripts/runtime-smoke-test.sh --runtime codex
```

`--check` は生成物（`plugins/<プラグイン名>/skills/<Skill 名>/agents/openai.yaml`）が最新であることを検証します。`validate-runtime-plugins.sh` は生成物同期、JSON / manifest、marketplace source、Markdown ローカルリンクをまとめて確認します。agy・Kiro CLI の導入物（`agy-skills.txt`・`kiro-skills.txt`・`dev.agy/`・`dev.kiro/`）が戻っていないことは `scripts/tests/test_distribution_runtimes.py` が確かめます。

`runtime-smoke-test.sh` は Docker コンテナ内に repo copy、`/tmp/runtime-home`、`/tmp/runtime-project`、`/tmp/runtime-secrets` を分離して作成し、Claude / Codex それぞれで `ndf` と `mcp-bigquery` 相当の実 install、Skill / MCP / hook の assertion、JUnit / log artifact 出力を確認します。PR CI は `--with-secrets=off` の非認証 smoke のみを実行し、認証付き smoke は `runtime-plugin-authenticated-smoke.yml` の protected workflow で実行します。

ローカル hook を使う場合は `bash scripts/install-dev-hooks.sh` で `.githooks/` を有効化します。

## 外部 AI 委譲

Codex MCP サーバは廃止済みです。外部 AI 委譲は `/ndf:external-ai` Skill と Claude Code 版の `corder` エージェントから Codex / agy を直接呼び出す方式を標準とします。

### 相手役のランタイム

Kiro CLI と agy は cross-review のレビュー担当と cross-refactoring の提案・実装の担当（相手役）で、プラグインは配りません。ホストは `plugins/ndf/scripts/lib/launch-cli.sh` で CLI を起動し、`--agent` もプラグインの導入先も渡しません。cross-refactoring の担当が読む手順書（`refactoring`・`tdd-cycle`・`quality-gates`）は、ホストの導入先から担当の worktree（Kiro CLI は `.kiro/skills`、agy は `.agents/skills`）へ写します（`plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh`）。起動の形の正本は `plugins/ndf/skills/external-ai/references/cli-kiro.md`・`cli-agy.md` です。

## 承認ゲートの外の問い合わせ

`AskUserQuestion` は、問いの区分（入力の `metadata.source`）が人へ回してよい区分のときだけ利用者へ出ます。区分の無い・
許されていない問いは `plugins/ndf/scripts/hook.py` の問い合わせの guard（`hook_lib/ask_guard.py`）が PreToolUse で止め、AI は
止めた理由を受け取って自分で決め、`scripts/ask.py decide` で記録して続けます。区分の表・宣言 `.ndf/ask.json` の形・記録の数え方の
正本は [`skills/development-workflow/references/asking.md`](../plugins/ndf/skills/development-workflow/references/asking.md) です。

| 区分 | 宣言で外せるか |
|---|---|
| `ndf-gate:1`・`ndf-gate:2`（承認ゲート） | 外せない |
| `ndf-ask:redline:<番号>`（共通原則の C1〜C8・プロジェクトの P・スプリントの R） | 外せない |
| `ndf-ask:cost`・`ndf-ask:irreversible`・`ndf-ask:human-action`・`ndf-ask:legal` | `allow` で外せる |
| `ndf-ask:<名前>`（`extra` に書いた区分） | — |

宣言が無いリポジトリでも既定（`enforce`・組み込みの 4 区分・費用のしきい値 0 USD）で働きます。止めたくないリポジトリは
`.ndf/ask.json` に `{"version": 1, "mode": "off"}` を書きます。判定と代わりの判断は `${NDF_MVV_STATE_DIR:-~/.local/state/ndf}/ask.jsonl`
へ追記され、`scripts/ask.py summary` で数えます。

手元の版（2026-10-31）で確かめたランタイムごとの扱い:

| ランタイム | 問い合わせの Tool | hook で止められるか | 扱い |
|---|---|---|---|
| Claude Code 2.1.314 | `AskUserQuestion`（`metadata.source` を持つ） | 止められる（PreToolUse の `deny`） | 止める |
| Codex CLI 0.164.0 | `request_user_input`（`codex features list` で `default_mode_request_user_input` が `under development`・`false`。exec では使えない） | 確かめていない（PreToolUse が発火するか、区分の欄があるか） | 対象外。Skill の手順の規則（`asking.md`）だけで守る |

hook の無いランタイムでは、区分に当たる問いを本文か各ランタイムの問い合わせの Tool で出し、区分の名前を問いの先頭に書きます。
止めた問いでは Slack の待ち通知を送りません。

## Slack 通知

利用者の回答か承認を待つときだけ、`plugins/ndf/scripts/wait-notify.py` が Slack へ知らせます。送る時点・本文・
環境変数は [plugins/ndf/README.md の「Slack 通知」](../plugins/ndf/README.md#slack-通知) にあります。

| Runtime | 通知方法 |
|---|---|
| Claude Code | Stop・Notification（`permission_prompt` ほか）・PermissionRequest（`ExitPlanMode`）・PreToolUse（`AskUserQuestion`。問い合わせの guard が止めた問いを除く）hook で `wait-notify.py --runtime claude` を実行 |
| Codex | `NDF_CODEX_SLACK_NOTIFY=true` の場合のみ Stop・PermissionRequest hook で `wait-notify.py --runtime codex` を実行 |

機密情報は環境変数で管理し、リポジトリにはコミットしません。

## 関連ドキュメント

- [Claude Code版 README](../plugins/ndf/README.md)
- [Codex版 README](../plugins/ndf/README.md)
- [NDF プラグイン README](../plugins/ndf/README.md)
- [開発ワークフローの用語集](../plugins/ndf/skills/development-workflow/references/glossary.md) — ステップ・パイプライン・セッション・承認ゲートなど、`development-workflow` とその周りで使う語
- [runtime plugin container smoke 仕様](specifications/runtime-plugin-container-smoke.md)
