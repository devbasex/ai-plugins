# #1959: 承認した Tool と MCP だけで起動する

## 関連リンク

- 要求仕様: [issue-1959-requirements.md](./issue-1959-requirements.md)（受け入れ条件は番号で指す）
- 設計書: [issue-1959-design.md](./issue-1959-design.md)・詳細設計書: [issue-1959-design-detail.md](./issue-1959-design-detail.md)
- 同じスプリントの後続: [issue-2944-plan.md](./issue-2944-plan.md)。**この計画の全部の単位を先に実装する**
- モード: `standard`（公開インタフェースが変わり、新しい Skill とサブコマンドを足す）

## 修正対象

[詳細設計書の「地図」](./issue-1959-design-detail.md#地図)の 2 つの表と「手順書」のセクションのとおり。触るファイルの一覧は設計書の「設計の結果」が持つ。

## タスク分解

### Task 1: 今の起動の形を縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/adapters/test_claude_args.py`・`tests/core/test_worker_launch.py`（新設）
- **変更内容:** full の起動の引数（`--setting-sources` が無い、許可の 13 個、会話の指定）、worker の再開の呼び出し、読むだけの相手役の引数を固定する
- **満たす受け入れ条件:** 6・8
- **進め方:** 今のコードで通るテストを先に置く

### Task 2: 起動の設定を読み、承認のハッシュと記録を持つ

- **対象ファイル:** `adapters/launch_settings.py`・`adapters/launch_approval.py`・`adapters/secret_forms.py`（新設）、`domains/project/secret.py`（秘密の形の表の移動）、`contracts/schemas/ndf-settings/launch.schema.json`（新設）、`contracts/domains/ndf-files.json`・`locations.json`・`env.json`、`adapters/claude.py`・`adapters/agents.py`（`NDF_AI_CHILD`）、`tests/adapters/test_launch_settings.py`・`test_launch_approval.py`（新設）
- **変更内容:** 詳細設計の「起動の設定の読み取り」と「起動の承認」。`safety_args` は Task 6 で足す。承認の質問・回答・候補の反映の 8 関数（`put_question`・`record_answer`・`approve_by_answer`・`pending_request`・`new_candidate`・`reflect`・`recover`・`reflected`）とその記録の保存先の行は Task 3 へ移した（使うのは Task 3 だけで、A1 が 1,000 行を超える見込みになったため）
- **満たす受け入れ条件:** 5・9・11・12・14
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: Skill と `ndf launch-config` で承認を取る

- **対象ファイル:** `domains/settings/launch_config_cli.py`・`domains/hooks/guards/launch_answer.py`・`skills/launch-config/SKILL.md`（新設）、`cli.py`、`contracts/hooks/guards.json`、`contracts/domains/entrypoints.json`・`skill-io.json`、`manifests/skills.json`、`.codex-plugin/plugin.json`、`contracts/schemas/io/launch-config.*`、`docs/contracts/`、`skills/worktree/references/ndf-settings.md`
- **変更内容:** `draft`・`show`・`approve`・`revoke`、承認の問いと答えの hook、手順書。Task 2 から移した `adapters/launch_approval.py` の 8 関数（`put_question`・`record_answer`・`approve_by_answer`・`pending_request`・`new_candidate`・`reflect`・`recover`・`reflected`）と、その記録（`launch-questions`・`launch-answers`・`launch-candidates`・`launch-reflected`）の `contracts/domains/ndf-files.json` の行。詳細設計の「起動の承認」の該当の関数の仕様に従う
- **満たす受け入れ条件:** 9・10・14
- **進め方:** CLI と hook はテストを先に書く。手順書に書くコマンドは打って確かめる
- **設計承認で持ち越した項目:** `.ndf/launch.json` の書き換えは毎回利用者に確認する（承認の対象が空か承認済みでも問う。利用者の答え）。限界として「書き換え中の手の保存が消えうる」を設計書に書く

### Task 4: full の起動を承認した内容に絞る

- **対象ファイル:** `adapters/claude.py`・`adapters/launch_settings.py`（`prepared`）・`core/worker.py`・`progress/invoke.py`、`contracts/domains/cli-floors.json`、`tests/progress/test_invoke.py` ほか
- **変更内容:** `--tools`・`--strict-mcp-config`・`--mcp-config`、MCP の定義のコピー、起動の直前の照らし直し。`plugins/ndf/skills/` の本文を検索し、工程の中で使う Tool が一覧に無ければ足す（設計書の「壊れうる箇所」）。設計書の決定 3 の数え直しを行い、結果を PR の本文に書く
- **満たす受け入れ条件:** 1・2・4・5・6・7・8・11・12
- **設計承認で持ち越した項目:** プランの実行は工程のモードを子へ環境変数で渡す。ある工程の MCP 定義が読めないと承認の要る起動がすべて止まることを限界に書き、停止の文でそのファイルを示す
- **進め方:** 引数と停止はテストを先に書く。実際の起動の `system/init` と MCP の呼び出し、Skill・サブエージェント・編集は手で確かめ、最低バージョンの `claude` でも打つ

### Task 5: 起動 1 回ごとに使用量ログへ 1 行書く

- **対象ファイル:** `adapters/claude.py`・`core/worker.py`・`progress/invoke.py`・`__main__.py`・`domains/records/usage.py`・`usage_decide.py`・`core/plan/runner.py`・`steps.py`・`transition_decide.py`、`contracts/schemas/internal/usage.schema.json`、`tests/domains/records/test_usage.py`・`tests/core/plan/`
- **変更内容:** 詳細設計の「起動の引数の組み立てと起動の結果」の結果の欄と記録、「プランの実行」、「使用量ログの書き込みと費用」。再開した起動の `usage` と `modelUsage` が累計かを 1 回測る（設計書の「未確認のまま残ること」）
- **満たす受け入れ条件:** 3
- **設計承認で持ち越した項目:** 一部の短い起動（応答の終わりの判定・cross-refactoring の相手役・疎通確認）は使用量の行が残らないことを限界に書く
- **進め方:** 失敗するテスト → 実装。前後の費用は、設計書の決定 6 の比較元で手で測って残す

### Task 6: 安全装置を外すのは承認がある場合だけにする

- **対象ファイル:** `adapters/launch_settings.py`（`safety_args`）・`domains/settings/delegate_run_cli.py`（新設）・`cli.py`・`core/converge/participants.py`・`adapters/agents.py`（`Launch.verify`）・`domains/crossreview/seats.py`、`contracts/domains/runtimes.json`、`skills/external-ai/`・`skills/qa-security-scan/03-report-template.md`・`agents/corder.md`・`skills/cross-review/SKILL.md`
- **変更内容:** 詳細設計の「相手役の起動の引数」と「手順書」。`-s workspace-write` の委譲を 1 回起動して確かめる
- **満たす受け入れ条件:** 13
- **設計承認で持ち越した項目:** `delegate-run` は cwd と違う `-C` を拒む（手順書は移動してから `-C "$PWD"`）。`build_command` は安全装置の引数との競合を拒む。読むだけの委譲に `review` の用途を指定できるようにする。限界として「承認の保存先を共有する別の環境でも安全装置を外す承認が効く」を設計書に書く
- **進め方:** 判定表の各行のテストを先に書く

## レビューの単位

行数は `git diff --shortstat <ベース>...HEAD -- . ':(exclude)uv.lock' ':(exclude)plugins/ndf/docs/contracts' ':(exclude)plugins/ndf/contracts/schemas/io/*.args.schema.json'` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 先に要る単位 | 実際の行数 |
| --- | --- | --- | --- | --- | --- |
| A1 起動の設定と承認の土台 | Task 1・2（承認の質問・回答・候補の反映の 8 関数を除く。A2 へ移した） | 1959:5・1959:9・1959:11・1959:12・1959:14 | 約 800 | — | 875 行足し 36 行消した（設計書と計画の追補を含む） |
| A2 承認を取る Skill とコマンド | Task 3（Task 2 から移した `launch_approval` の 8 関数を含む） | 1959:9・1959:10・1959:14 | 約 900 | A1 | |
| A3 full の起動の絞り込み | Task 4 | 1959:1・1959:2・1959:4・1959:5・1959:6・1959:7・1959:8・1959:11・1959:12 | 約 450 | A1 | |
| A4 使用量ログの行 | Task 5 | 1959:3 | 約 600 | A3 | |
| A5 安全装置の承認 | Task 6 | 1959:13 | 約 400 | A2 | |

- 順序: A1 → （A2 と A3 は並列）→ A4 は A3 の後、A5 は A2 の後。A4 と A5 は並列にできる
- A2 と A3 は、触るファイルが重ならない。A3 だけが先に入ると、指定を書いたリポジトリは承認の手段が無く起動が止まるので、A2 と A3 は同じスプリントブランチにそろえてから `main` へ出す
- A1 が 400 行を超えるのは、読み取り・承認の記録・テストが互いに無しでは読めないためである。実装の途中で 1,000 行を超えそうなら、設定の読み取りと承認の記録で 2 つに分ける。実装では 840 行の時点で残りの 8 関数を足すと超える見込みになり、使う側の Task 3（A2）へ移した

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | `claude.py` 783 行（6 タスク中 4）、`worker.py` 998 行（3）、`invoke.py` 930 行（2）、`runner.py` 731 行（1）。テストは `test_claude_args.py` 19 件、`test_invoke.py` 68 件、`test_runner.py` 29 件がある。足す中身の大半は新しいモジュールに置き、既存のファイルは呼び出しと欄の追加である |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `claude.py` の起動の結果と記録、`worker.py` と `invoke.py` の「指定の取得 → 起動 → 記録」の同じ形 | 2 つの呼び出し元が同じ手順を持つ。#2944 が同じ場所に値の取得を足すので、#2944 の後にまとめて見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 工程の中で使う Tool が一覧から漏れ、工程が欠けたまま進む | Task 4 で Skill の本文を検索し、実際の起動の一覧を見る |
| `Caller` の引数を増やすと、テストの偽の呼び出しが壊れる | 同じ単位で直す |
| サンドボックスが使えない環境の cross-review が止まる | リリースの変更点に承認の手順を載せる。このスプリントのレビューを流す環境で、A5 のマージの前に承認しておく |

## 切り戻し手順

- 単位の PR を revert する。承認の記録と使用量ログの足した欄は残っても、古いコードは読まない（欄を足した行は、古いバージョンでは読めない行として数える）
- 利用先は、`{"version": 1, "permissions": {"steps": {"*": {"mcp": "inherit"}}}}` を承認すると MCP の読み込みを戻せる

## 完了の定義

- [ ] 受け入れ条件 1〜14 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] CI のテスト・静的解析・`claude plugin validate .`・`ndf records glossary render --check` が通る
- [ ] 手動の確認（要求仕様の「検証手段」と設計書の「テスト設計」の手動の行）の結果を PR に書いた
