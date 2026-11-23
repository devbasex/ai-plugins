# 4036: 背景の作業を持たないまま何もしていない conductor を、止まりとして知らせる

## 関連リンク

- 要求仕様: [issue-4036-requirements.md](./issue-4036-requirements.md)。受け入れ条件は番号（1〜5）で指す。起票者の条件 S1 は設計の決定 1〜3、S2 は対象範囲の外として設計が扱った
- 設計書: [issue-4036-design.md](./issue-4036-design.md)・詳細設計書: [issue-4036-design-detail.md](./issue-4036-design-detail.md)
- モード: `standard`（機能追加で、Stop の hook・レーンの生死の判定・`lane_stalled` のリクエストの中身を変える安定版の変更。設計承認ゲートを通した設計文書を持つ）

## 修正対象

詳細設計書の「地図」から引いた。表に無い `plugins/ndf/tests/domains/relay/test_relay_hub.py` は Task 4 で足す（理由は Task 4 にある）。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/contracts/limits.json` | `parallel-run.idle` の閾値（600 秒） |
| `plugins/ndf/src/ndf/domains/relay/signals.py` | `activity.json` の読み書き（`write_activity`・`read_activity`・`ACTIVITY_FILE`） |
| `plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py` | Stop のたびに背景の作業の一覧とセッションを書く |
| `plugins/ndf/src/ndf/domains/relay/hub.py` | `ndf relay ls` の行に `session_id`・`cwd`・`pid` |
| `plugins/ndf/src/ndf/domains/relay/observe.py` | `subagent_transcript_path`・`live_shells`・`shell_output_path` |
| `plugins/ndf/src/ndf/progress/lane/liveness.py` | `Activity`・`Probes.activity`・`idle_after`・`judge`・`judge_lanes`・`Liveness`・定数 |
| `plugins/ndf/src/ndf/progress/plan/run.py` | `lane_activity`・`probes`・`_judge`・`Judged` |
| `plugins/ndf/src/ndf/progress/plan/run_decide.py` | `Stall`・`_stall_report`・`with_stalls` |
| `plugins/ndf/tests/domains/hooks/test_relay_mark.py`（新設） | Stop の hook が活動を書く・`activity.json` の読み書き |
| `plugins/ndf/tests/domains/relay/test_relay_observe.py` | サブエージェントの会話の記録のパス・生きているシェル・出力のファイルのパス |
| `plugins/ndf/tests/progress/test_lane_liveness.py` | 新しい判定の条件と境目 |
| `plugins/ndf/tests/progress/test_plan_run_stall.py`（新設） | 活動を読む関数と止まりの知らせの中身 |

#3877・#3908 の触るファイル（[issue-3877-plan.md](./issue-3877-plan.md)・[issue-3908-plan.md](./issue-3908-plan.md) の「修正対象」）と重なるファイルは無い。#3908 が触るのは `progress/run.py`（工程の駆動）で、この課題の `progress/plan/run.py`（計画の実行）とは別のファイルである。

## タスク分解

### Task 1: 実測 — Stop の入力の `background_tasks`・サブエージェントの記録の名前・背景のシェル

- **対象ファイル:** 無し（結果を `4036-u1-record` の Pull Request に残す）
- **変更内容:** 実機の Claude Code で次の 3 つを 1 回ずつ確かめる（設計書の「未確認のまま残ること」の 2〜4 行目）
  - 背景のサブエージェントを待って応答を終えたとき、Stop の hook の入力に `background_tasks` が載るか。載る要素の形（`id`・`type` などのキー）
  - `subagent` の要素の `id` が、会話の記録の `<セッションの記録から .jsonl を除いたもの>/subagents/agent-<id>.jsonl` の `<id>` と同じか
  - `run_in_background` で起動した Bash の Tool のシェルが `claude` の直接の子の `bash -c source …/.claude/shell-snapshots/snapshot-…` の形か
- 違ったときの扱いは設計書が定める。`id` が違えば `activity.json` に会話の記録を引ける値を足す。シェルの形が違えば、生死を確かめられない扱い（一覧のシェルを走っているとみなす）へ落とし、受け入れ条件 1 のシェルの取りこぼしを捕まえられないことを Pull Request に残す。どちらも Task 3・5 の中身が変わるので、結果を Task 3 の前にそろえる
- **満たす受け入れ条件:** 無し（Task 3〜5 の前提）
- **進め方:** テスト駆動を適用しない（実機の入力と `/proc` の形の記録）

### Task 2: 退行テスト — 活動を読まない呼び手の判定

- **対象ファイル:** `plugins/ndf/tests/progress/test_lane_liveness.py`
- **変更内容:** `judge` に活動を渡さないとき、今の 8 つの判定（順 1〜順 8）が今と同じ値を返すことを縛る
- **満たす受け入れ条件:** 3（承認ゲートと回答の待ちが今までどおり止まりにならない）
- **進め方:** 今の振る舞いで通るテストを書く（退行テスト）。範囲のテストは `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/progress/test_lane_liveness.py -q` で打つ

### Task 3: 活動の記録を書く（`activity.json` と Stop の hook）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/relay/signals.py`、`plugins/ndf/src/ndf/domains/hooks/guards/relay_mark.py`、`plugins/ndf/tests/domains/hooks/test_relay_mark.py`（新設）
- **変更内容:** `write_activity`・`read_activity`・`ACTIVITY_FILE`（`signals.py` に置く。Stop の hook の閉包に `jsonschema` を入れないため）と、`check` が中継の下なら早い戻りの前に生の `background_tasks` と `session_id` を書くこと。`write_activity`・`read_activity` の単体テストも書き込み元の hook のテストと同じファイルに置く（書き込み元の hook と読み書きを一緒に確かめるため）
- **満たす受け入れ条件:** 2・4
- **進め方:** 失敗するテスト（詳細設計の「活動の記録」と「Stop の hook が活動を書く」の単体テストの観点の 3 行。明示の空のリストと観測できなかった `null` を読み分ける）→ 通す最小実装 → 整理

### Task 4: 会話の記録を組む材料（`ndf relay ls` の行と `observe` のパス）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/relay/hub.py`（`listing`）、`plugins/ndf/src/ndf/domains/relay/observe.py`（`subagent_transcript_path`・`live_shells`・`shell_output_path`）、`plugins/ndf/tests/domains/relay/test_relay_hub.py`、`plugins/ndf/tests/domains/relay/test_relay_observe.py`
- **変更内容:** 一覧の行に `session_id`・`cwd`・`pid` を足し、3 つのパスと数の関数を足す。`test_relay_hub.py` は地図に無いが、`listing` を覆う既存のテスト（48 件）がここにあるので足す
- **満たす受け入れ条件:** 1
- **進め方:** 失敗するテスト（詳細設計の単体テストの観点の `listing`・`subagent_transcript_path`・`live_shells`・`shell_output_path` の 4 行。`/proc` は一時ディレクトリに組む）→ 通す最小実装 → 整理

### Task 5: 何もしていない conductor の判定（`idle_conductor`）

- **対象ファイル:** `plugins/ndf/contracts/limits.json`、`plugins/ndf/src/ndf/progress/lane/liveness.py`、`plugins/ndf/src/ndf/progress/plan/run.py`（`lane_activity`・`probes`）、`plugins/ndf/tests/progress/test_lane_liveness.py`、`plugins/ndf/tests/progress/test_plan_run_stall.py`（新設）
- **変更内容:** `parallel-run.idle`（600 秒）、`Activity`・`Probes.activity`・`idle_after`・`IDLE_CONDUCTOR`・`STALL_KINDS`・`judge` の順 5 と順 6 の間の条件・`judge_lanes`・`Liveness` の 2 つの値。`lane_activity` の手順 1〜6
- **満たす受け入れ条件:** 1・2・3・5
- **進め方:** 失敗するテスト（詳細設計の「活動を読む」と「何もしていない conductor の判定」の単体テストの観点。時刻はテストが渡し、時間で区切った試験を行わない。閾値ちょうどと 1 秒手前・`limits.json` の値を変えると境目が変わる）→ 通す最小実装 → 整理

### Task 6: 止まりの知らせの中身（`lane_stalled`）

- **対象ファイル:** `plugins/ndf/src/ndf/progress/plan/run.py`（`_judge`・`Judged`）、`plugins/ndf/src/ndf/progress/plan/run_decide.py`（`Stall`・`_stall_report`・`with_stalls`）、`plugins/ndf/tests/progress/test_plan_run_stall.py`
- **変更内容:** 止まりの組を 6 つにし、`moved` が数なら ISO 8601 の文字列へ直し、`background` を `facts` に足す。5 つの組のスナップショットも包めるようにする
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト（詳細設計の `_judge`・`_stall_report` の単体テストの観点）→ 通す最小実装 → 整理。最後に、詳細設計の「判定の値が増えることの当たり先」の表の 7 行を実装の後のコードで読み直し、新しい判定が出たレーンで `ndf lanes status` の行の `liveness.kind` が `idle_conductor` と出ることを 1 回確かめて Pull Request に残す

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| `4036-u1-record` | Task 1・3・4 | 4036:2・4036:4 | 約 410 | （実装の後に書き足す） |
| `4036-u2-judge` | Task 2・5・6 | 4036:1・4036:2・4036:3・4036:4・4036:5 | 約 470 | （実装の後に書き足す） |

行数は追加と削除の和（テストを含む）で、`plugins/ndf/docs/contracts/commands.md` と `uv.lock` は数えない（この課題はどちらも変えない）。課題の見込みの和は約 880 行で 1,000 行を超えないが、1 単位 400 行の目安を超えるので、依存の向き（活動を書く側 → 読んで判定し知らせる側）で 2 つに分けた。`4036-u1-record` だけをマージした状態でも、`activity.json` を読む手が無いだけで判定は今のまま動く。受け入れ条件 2・4 は書く側と読む側の両方にまたがるので、2 つの単位に挙げた。

単位の順序: `4036-u1-record` は最初の波で #3877・#3908 の単位と並列に流してよい。`4036-u2-judge` は `4036-u1-record` の後に流す（`read_activity` と `observe` の関数を使う）。#3877・#3908 の単位とは触るファイルが重ならないので、どの単位とも並列に流してよい。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルの最大は `run_decide.py` の 591 行と `hub.py` の 513 行で、どちらも触るのは 6 タスク中 1 タスク。最も多く触られる `liveness.py`（149 行）は 2 タスク（Task 2・5）で、`test_lane_liveness.py` に 16 件、`test_relay_hub.py` に 48 件のテストがある |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `observe.subagent_transcript_path` と `context_report._subagents_dir`（記録から `subagents` の保存先を組む同じ形の処理）。`signals.write_activity` が使う `signals.replace_json` と #3877 の `worker_paths.write_file`（置き換えで書く同じ形の処理） | 前者は詳細設計が範囲の外とした寄せ先で、同じ組み方が 2 か所に並ぶ。後者は `3877-u1-paths` と `4036-u1-record` の単位をまたぐ |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 閾値の 600 秒が短すぎて正常な行を止まりと知らせるか、長すぎて気づきが遅れる | 値は `limits.json` の 1 行だけが持つので、知らせの実績を見て直す |
| Stop の hook（`relay_mark.py`）は中継の本流で、書き込みの失敗が conductor の応答を止める | `write_activity` は例外を外へ出さずに偽を返し、`check` は今までの判定を続ける（Task 3 の異常系のテスト） |
| `relay_mark.py`・`hub.py` を並行するほかのスプリントも触る | 単位ごとに `main` を取り込んでから Pull Request を出し、衝突はその単位の中で直す |
| Task 1 で `id` か背景のシェルの形が設計の想定と違う | 設計書が定める落とし方（会話の記録を引ける値を足す・シェルを走っているとみなす）で進め、Pull Request に残す。設計書が定めない違いなら `結果: 判断が要る` で supervisor へ返す |

## 切り戻し手順

- 単位ごとの Pull Request の revert で戻る。`activity.json` はラッパーの保存先の診断のファイルで、残っても読む手が無ければ振る舞いを変えない。データ移行は無い
- 知らせだけを止めたいときは、`4036-u2-judge` の revert で判定を今の 8 つへ戻す（`4036-u1-record` は残してよい）

## 完了の定義

- [ ] 受け入れ条件 1〜5 を満たし、条件ごとに検証手段（テスト関数の名前）と CI の結果（`gh pr checks <PR>`）が対応している
- [ ] Task 1 の実測の結果と、Task 6 の `ndf lanes status` の確認の結果を Pull Request に残している
- [ ] `standard` のモードの検証（単位ごとの cross-review の収束・CI の必須のチェック）を通している（quality-gates）
