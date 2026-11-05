# NDF のスクリプトの共通処理の置き場と、意図して寄せない重複

NDF のスクリプト（`plugins/ndf/scripts/` と、Skill の `skill-stats`・`backlog-refinement` のスクリプト）のうち、
文脈の量の記録・トークンの監視の hook・課題の棚卸し・Skill の利用統計で同じ処理が 2 か所以上に現れていたものを、
1 か所の関数へ寄せている。この文書は、寄せた処理の置き場と呼び出しの向き、寄せずに残した重複とその理由、
長い関数を分けた形を残す。

## 概要

- 同じ処理（使用量の行の組み立て・時刻の読み取り・git のリポジトリの根の取得・再起動の行の組み立て・
  正の整数の判定・「実行した」行の判定）は、本体を `plugins/` の中の 1 か所に置く
- 寄せる先は、重複の片方がすでにある既存のモジュールである。共通処理のためだけのモジュールは作らない
- 寄せても、コマンドの引数・終了コード・標準出力の結果 JSON・hook の出力の文面・記録のファイルの形・
  `plugins/ndf/contracts/` の契約は変わらない
- 契約の抽出・パッケージ内の書き方の揃い・呼び出し元の数を理由に、寄せずに残す重複がある（「寄せない重複」）

### 用語

| 用語 | 意味 |
| --- | --- |
| 見送った提案 | cross-refactoring が計画に入れなかった提案と、見送った改善項目を合わせたもの |
| 現状固定テスト | 用語集（[docs/glossary.md](../glossary.md)）の定義に従う |
| 判定の記録 | 文脈の量の方針が工程の境界ごとに書く記録（`*.decisions.jsonl`）。形は [ndf-context-restart-policy.md](ndf-context-restart-policy.md) が持つ |

### コンテキスト

| コンテキスト | この文書で扱う範囲 |
| --- | --- |
| NDF の開発ワークフロー（`ndf-workflow`） | 判定の記録・文脈の量の方針・トークンの監視の hook・スプリントの周回の選定 |
| NDF の課題の棚卸し（`ndf-issue-upkeep`） | 調査・深さ・大整理（`upkeep_*.py`） |
| NDF の cross-refactoring（`ndf-cross-refactoring`） | 見送った提案の出所。この文書の構造はその語と兆候の分類を受け取る（順応者） |

## 仕様

### 常に成り立つ条件

- 同じ処理の本体は `plugins/` の中で 1 か所にある（下の「共通処理の置き場」の表の処理）
- `plugins/` の Python は、リポジトリだけで使う `scripts/` のモジュールを import しない
- 共通処理を使う側を変えても、記録の行のキーと値、標準出力、`plugins/ndf/contracts/` は変わらない。
  変わるなら、その寄せ方は採らない

### 共通処理の置き場

| 処理 | 置き場（関数） | 使う側 |
| --- | --- | --- |
| API 呼び出し 1 回の使用量の行（`ts`・`model`・`inp`・`out`・`read`・`w5`・`w1h`・`context`）を組む | `lib/context_decisions.py` の `usage_call` | 同じモジュールの `read_calls`（会話の記録）、`context-policy.py` の `ledger_calls`（使用量の帳簿。秒の `seconds` だけを呼ぶ側が足す） |
| 時刻の文字列を UNIX 時刻（秒）にする | `lib/clock.py` の `epoch_seconds`（`clock.parse` の上に置く） | `lib/context_decisions.py` の `parse_ts`（`epoch_seconds` の別名）、`backlog-refinement/scripts/upkeep_research.py` |
| git のリポジトリの根を取る | `lib/context_decisions.py` の `toplevel` | `context-policy.py`、`hook_lib/token_guard.py`（文脈の量の判定と、計画の案内の `plan_hint`） |
| 再起動の行と印の行を組む（セッションの読み取り・境目の行・`cut` と再起動の付与） | `lib/context_decisions.py` の `record_row` | `context-policy.py` の `cmd_record`、同じモジュールの `record_restart`（`sprint-state.py next`） |
| 正の整数（`bool` を除く）か判定する | `lib/context_policy.py` の `is_positive_int` | 同じモジュールの方針の解決、`lib/context_decisions.py` の `read_window` |
| 試行方式の拒否の句で使う方針と理由の語 | `lib/context_policy.py` の既存の定数 `TRIAL`・`REASON_CAPACITY`・`REASON_COST` | `hook_lib/token_guard.py` の `_trial_cause` |
| 「その深さを実行した」行の判定と、最後に実行した行の位置 | `backlog-refinement/scripts/upkeep_depth.py` の `_is_ran`・`_last_ran` | 同じファイルの `diffs_since`・`_ran`・`_due_overhaul`・`compare_rows` |

**`clock.parse` は前後の空白と小文字の `z` も読む。** NDF の記録の時刻は `clock.now_iso` が書くため、
この 2 つを含む時刻は記録に現れず、記録を読む結果は変わらない。形の違う時刻の読み取り
（`"null"` も読むもの、UTC へ変えるもの）は同じ処理ではないため寄せない。

**hook は `context_decisions` を関数の中で import する。** トークンの監視の hook は起動のたびに読まれるため、
最上位で import すると、計画の案内が要らない呼び出しでも読み込みの時間がかかる。
応答時間は既存の締切のテスト（`plugins/ndf/scripts/tests/test_hook_deadline.py`）が見る。

### 寄せない重複

次の重複は、意図して残している。

| 対象 | 残す理由 |
| --- | --- |
| `context_decisions.py` の `append_decision`・`write_applied` の書き込み先のパス | 契約のチェック（`scripts/contracts_lib/extract_py.py`）は、書き込みの式に書かれた f 文字列から書き込み先のファイル名（`*.decisions.jsonl`・`*.applied.json`）を拾う。パスを作る関数へ寄せると契約から書き込み先が消える |
| `context-policy.py` の `IDLE_CAP` と `scripts/token-usage.py` の `IDLE_CAP`・`parse_ts` | 相手は配布の外の `scripts/` にある。`plugins/` は `scripts/` を import しないため、共有の仕組みは置かない |
| `context_decisions.py` の `attach_marks` と、`context-policy.py` の区間の作り方 | 本体は重複しない。作業の報告（`report_work`）は帳簿にあるセッションを除くなど、区間の作り方を意図して変えている |
| `relay_lib/terminal.py` の `Terminal` が環境変数で待ち時間を読む形 | 待ち時間を環境変数で調整するのは `relay_lib` 全体の約束で、同じ読み方が他に 12 か所ある。1 クラスだけ引数で受けると同じパッケージに 2 つの書き方が残り、生成の時点で読むと読む時刻も変わる |
| `upkeep_research.py` の `run_research` の 11 個の引数 | 引数は待つ条件・記録の持ち主・試験用の差し替えに分かれ、1 つの組として渡っていない。値の型にまとめても関数が受ける情報は変わらず、呼び出し元（CLI 1 か所・試験 12 か所）だけが変わる |
| `upkeep_research.py` の `research_one` の入れ子 | 入れ子は 1 段で、深い入れ子に当たらない |

### 長い関数の分け方

長い関数は、同じファイルの中の名前の付いた関数の並びにしている。分けた先は次のとおり。

| 関数 | 抜き出した手順 |
| --- | --- |
| `context-policy.py` の `report_work` | 区間の作成（`_session_spans`）・部分の合算（`_sum_sessions`）・再起動の数え上げ（`_restarts_by_reason`）・やり直しの数え上げ（`_rework`） |
| `context-policy.py` の `session_parts` | 印の境目の取得（`_mark_bounds`）・サブエージェントの加算（`_add_subagents`）・帳簿の加算（`_add_ledger`） |
| `context_decisions.py` の `read_calls` | 自動の要約の検出（`_is_auto_compact`）・最初の発言の取得（`_user_text`）・JSON 行の読み取り（`_json_lines`）、使用量の行は `usage_call` |
| `sprint_loop_select.py` の `run_selection` | 課題を選べなかったときの止まり方（`_stop_without_issue`）・周回の行を作って記録する（`_open_lap`） |
| `skill-stats.py` の `aggregate_by_project` | 利用者の発言 1 件の数え上げ（`_count_user_text`） |
| `skill-stats.py` の `emit_json`・`emit_markdown` | `--agents` の報告の値（記録の行・要約・層ごとの合計・総計・役割ごとの使用量・要約から除いた数・上限と出所）を 1 つの値の型 `AgentReport` で受ける。値は `agent_report` が組む |
| `upkeep_overhaul.py` の `count_types` | 型ごとの件数（`_issue_types`）・本文からの部品の拾い出し（`_components`）・月ごとの数え上げ（`_months`）。戻す辞書のキーと順序は変えない |
| `upkeep_research.py` の `run_research` | 空きを待って止まる理由を返す（`_wait_turn`）・経過を書く関数を作る（`_ticker`） |
| `scripts/token-usage.py` の `read_codex`（配布の外） | Codex の記録の行の読み取り（`_codex_lines`）・使用量の組み立て（`_codex_usage`） |

## 運用

- 上の表の処理を直すときは、置き場の関数だけを直す。使う側へ同じ処理を書き足さない
- 寄せない重複の表にある箇所を寄せたくなったら、残す理由が今も成り立つかを先に確かめる。
  書き込み先のパスを寄せる場合は、先に契約の抽出（`scripts/contracts_lib/extract_py.py`）の側を直す

## テスト観点

- 寄せた処理を使う関数の出力が、寄せる前と同じであること。使用量の行・再起動の行と標準出力・
  自動の要約と引継ぎ開始の数え上げは `plugins/ndf/scripts/tests/test_context_policy_hook.py` が、
  時刻の読み取りは `plugins/ndf/scripts/tests/test_lib_clock.py` が見る
- 作業の報告の秒・費用・再起動の数、Skill の利用統計のトリガーとヒットの数とエージェントの報告の JSON、
  直しの周回の印が変わらないこと（`test_context_policy_hook.py`・`skill-stats/tests/test_agents_report.py`・
  `skill-stats/tests/test_skill_stats_wrappers.py`・`plugins/ndf/scripts/tests/test_sprint_loop.py`）
- hook が締切の中で応答すること（`plugins/ndf/scripts/tests/test_hook_deadline.py`）
- `python3 scripts/check-ndf-contracts.py` で `plugins/ndf/contracts/` に差分が出ないこと
- `python3 scripts/check-script-structure.py` が通ること（公開の関数名が他のモジュールの同じ名前と衝突しない）

## 関連リンク

- [ndf-context-restart-policy.md](ndf-context-restart-policy.md) — 判定の記録と `context-policy.py` の契約
- [ndf-token-waits-and-context-cut.md](ndf-token-waits-and-context-cut.md) — トークンの監視の hook の判定
- [ndf-backlog-refinement-depths.md](ndf-backlog-refinement-depths.md) — 課題の棚卸しの深さ
- `plugins/ndf/skills/refactoring/SKILL.md` — テストで守ってから構造を変える手順
