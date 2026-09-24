# #933: cross-refactoring を想定最大時間に収める 1 回の計画実行へ改める — 実装計画

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/933（あわせて #754 は実装のマージで閉じ、#755 は「やらない」で閉じる。閉じるのはまとまりの終わり）
- 要求: [issue-933-requirements.md](issue-933-requirements.md)
- 設計: [issue-933-design.md](issue-933-design.md)・[決定の記録](issue-933-design-decisions.md)・[テスト設計](issue-933-design-tests.md)
- 設計の Pull Request: https://github.com/devbasex/ai-plugins/pull/941（develop へマージ済み）

## モード

standard（関門 1 は 2026-09-24 に承認済み。公開インタフェースと状態ファイルの形を変えるが、利用者は手元の実行だけで本番の系へ届かない）。

## 目的と非目的

達成したい状態:

- `/ndf:cross-refactoring` が、ラウンドを上限まで回す形から、`--budget-minutes` に収まる計画を 1 回だけ実行する形になる（提案 → 計画 → テスト追加 → 実装 → 検証/修正）
- 配分テーブルを履歴から計画のたびに集計し、実行の終わりに履歴へ 1 行追記する
- 使えるときは Jev に段・同じ変更か・D5 を問う

やらないこと:

- 確定仕様（`docs/specifications/`）の改訂。`plan-to-spec` の工程で行う（要求の対象範囲のとおり）
- `cross-review` の中身、駆動の bash の `scripts/` への移動（#560 #870）、版ごとの測定（#893）
- `lib/metrics.py`（cross-review と共有する `--metrics` の集計）の改修。cross-refactoring の報告はこの変更で新しい表へ置き換え、`metrics.py` は cross-review 側だけが使う形のまま残す

## 前提

- 前提 1: #930 は develop へマージ済み（707dc79d）。作業ツリーは 46272ed9 から作った
- 前提 2: Jev の応答の形は 2026-09-24 に実測で確かめた。`POST https://ai-gateway.vercel.sh/v1/evaluate` へ `{"model": "typesafe-ai/jev", "state": <文>, "questions": {<名前>: {"type": "boolean", "instructions": ...} | {"type": "score", "criteria": [...], "instructions": ...}}}` を送ると、`answers.<名前>` に `boolean` は `probability`、`score` は `probabilities`（0 始まりの位置 → 確率）と `confidence` が返る（設計のテスト設計の「未確認のまま残ること」の 1 行目が解ける）
- 前提 3: テストは根の `conftest.py` が `NDF_METRICS_DIR` を一時ディレクトリへ向ける。履歴を書くテストは、これに加えて `tmp_path` を根として明示的に渡し、`metrics_dir()` を経由しない（#938 の汚染を繰り返さない）

## 受け入れ条件

要求の AC1〜AC28 と非機能の条件をそのまま使う。検証手段は [テスト設計](issue-933-design-tests.md) の表に従う。ここには実装で足す確かめだけを書く。

- [ ] 設計レビューの未解決 3 件を設計文書で直した（20b4028d。D4 は決定 21）
- [ ] 新しいテストのどれも、利用者の `~/.local/state/ndf/metrics` を読み書きしない（履歴のテストは根を引数で渡す）
- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest plugins/ndf/skills/cross-refactoring plugins/ndf/scripts/tests -q -n 4` が通る
- [ ] `python3 scripts/check-markdown-links.py` / `check-doc-line-limit.py` / `check-skill-frontmatter.py` / `check-skill-shell-vars.py` と `claude plugin validate .` が終了コード 0

## 実装で決めたこと（設計の範囲の細部）

設計が形を決め、実装で細部を詰めたもの。確定仕様化の工程で決定の記録へ移す。

| # | 決めたこと | 理由 |
| --- | --- | --- |
| I1 | フェーズの開始は `refactor.py start-phase <ID> <フェーズ>` が記録する。`phases.<名前>.started_at` を書き、`add-tests` / `implement` では監視と CLI の上限（`max(表の値, T − 今) + 600`）を `PHASE_TIMEOUT` として返し、`phases.<名前>.timeout` に残す。終わりは各 `merge-*` が書く | 設計は「進行側の時計で測る」と定め、開始の記録の置き場所を決めていない。CLI の起動の直前に 1 回だけ打つ形にすると、再開でも開始が上書きされない（記録済みなら書き換えない） |
| I2 | CLI の上限は、skill 側の `launch-cli.sh` が状態の `phases.<フェーズ>.timeout` を読み、あれば数字で共通層へ渡す | 共通層の `launch-cli.sh` は数字の上限を既に受ける。表の値より長い監視の上限を渡すと CLI（agy）が先に打ち切るため |
| I3 | 結果ファイルの名前は `<ランタイム>-<フェーズ>-rf<ID>`（提案・計画・テスト追加・実装・修正・段 2 の判定）。最終ゲートの修正は今の `<ランタイム>-final-fix` のまま | ラウンドの番号が無くなる。修正は何度起動しても同じ名前で、取り込み済みの判定は今と同じく試行の番号と結果の中身の組で行う |
| I4 | 項目とコミットの対応は git のトレーラー `Item-Id` だけで決める。求めるトレーラーは `Item-Id` / `Impl-Runtime` / `Impl-Model`（`Round` は外す） | 設計は「コミットと項目の対応を git から検査する」と定める。結果ファイルの申告は所要と同じく使わない |
| I5 | 手順を外れたコミット（範囲の外・トレーラー欠け・計画に無い `Item-Id`・1 フェーズで 1 項目に 2 コミット以上・差分予算の超過・文言固定テスト・期待値の変更）を持つ項目は、そのフェーズのコミットを取り消し、項目を `status: reverted`（`failure_reason` に理由）にする。見送り（`deferred_items`）には入れない。どの項目にも属さないコミットは、それだけを取り消す | 要求 AC9 の見送りの理由は 8 つに限る。検証で取り消した項目（`reverted`）と同じ扱いにすれば理由を増やさずに済む |
| I6 | 取り消しは起点（`plan.base_sha` = `merge-plan` の時点の HEAD）から HEAD までを新しい順に取り消し、残す項目のコミットを古い順に積み直す（今の `drop_items` の方式を、群ではなく計画の単位で使う）。積み直しが競合したら、取り消す項目と同じファイルを触った項目までを取り消しへ広げて 1 度だけやり直し、それも競合したら全件の取り消しへ退避する（`plan.drops[].mode` に `item` / `widened` / `all`） | AC15 の「項目の単位で戻せないとき（隣接する変更）は全件の取り消しへ退避」を保ちつつ、1 回目の競合で全件を捨てないため |
| I7 | テストの差分の判定（段 1 と段 2）は、実装と修正のコミットに掛ける。保留は項目ごとに `items[].pending_test_judgements` へ持ち、段 2 は `judge-test-changes` を 1 回起動して全項目の保留をまとめて問う。`changed` の項目は取り消す | 設計は `merge-test-judgements` を残すと定める。群が無くなったため、保留の持ち主を項目にする |
| I8 | 限ったテストの組み立ては新しい `refactor_lib/testcmd.py` が持つ（既知の実行器の判定・対象の語の差し替え・`test_targets` の検査）。`init` の AC3b もここを使う | `scope.py` は範囲の関門を持ち、組み立ては別の責務である |
| I9 | 修正の雛形へは、落ちた項目ごとに語の並びと、最後に走らせた出力の末尾（`<TMP_DIR>/verify-<項目>.log`）のパスを渡す | 修正担当が失敗を再現する材料。テストの出力は Jev へは送らない（設計の方針） |
| I10 | `report --metrics` は、共有の `metrics.py` の代わりに種類別の件数と所要の表を出す | 状態がラウンドを持たなくなり、`metrics.py` の集計は空になる |
| I11 | `test_targets` の実在の検査は、その項目が足すテスト（計画の `tests`）を除外する | 計画の時点では足すテストがまだ無い。実在を求めると、新しいテストのファイルを対象にした項目がすべて `no_target` になる（通しのテストで実測）。足さなければ項目ごと `not_done` で見送られるため、実在しない対象で検証が走ることはない |
| I12 | 修正の取り込みが残ったまま `verify` を呼ぶと、先に `merge-fix` を行う | 修正の後に落ちて再開すると、修正のコミットが項目に結ばれないまま HEAD に残り、検証だけが先に進む |
| I13 | 取り込みで採らないコミット（手順違反・締め切り超え・`test_failed`）は、項目の記録に載せずに取り消しの対象へ明示する。コミットを持たない項目の取り消しは git を触らない | 範囲を逆再生して積み直すと、残す項目の SHA が変わるだけで木は変わらない |
| I14 | 危険の印の全体のテストが落ちたら、pytest の要約（`FAILED` / `ERROR`）から落ちたテストの ID を取り出し（`testcmd.failed_nodes`）、`--baseline-test` の対象を差し替えた語の並び（`testcmd.rerun_command`）で今の HEAD と着手前の HEAD（一時の detach の作業ツリー）で走らせ直して分ける（`triage.classify`）。変更が原因のものは印の項目を `failing` にして既存の修正へ回し（`whole_test_command` と `last_log` を修正の雛形へ渡す）、次の `verify` は落ちたテストだけを走らせ直す。締め切りか上限で `_revert_shared` の形で新しい順に絞って取り消す。`init` は着手前の HEAD を `baseline_test.head` に残す | 決定 22。取り出せないときは決定 15 のまとめての取り消しへ倒し、理由を `whole_test.unparsed_reason` に残す |
| I15 | `start-phase` は全段（提案・計画・テストの追加・実装・修正・最終ゲートの修正）で、`state["limits"]` のその段の終わりまでの残り + 余裕を `PHASE_TIMEOUT` として返し、`phases.<段>.timeout` と `cli_timeout`（+ 余裕）を残す。駆動は `--timeout` と `--stall-timeout` の両方へ渡し、`launch-cli.sh` は `cli_timeout` を CLI の上限にする。修正は `_fix_stop`（時計だけ）で打ち切り、最終ゲートの修正は `final_end_at` を過ぎたか `no_relaunch` で打ち切る。取り込み（`merge-tests` / `merge-implement` / `merge-fix`）は監視の結果ファイルの `reason` が `timeout` / `stalled` なら `phases.<段>.stopped` に残し、報告が「監視が止めた段」を出す | 決定 23 |
| I16 | 時間の値は `refactor_lib/timeline.py`（純粋な処理）が B と w から出す。`init` と計画の前の再開が計画の前の値を、`merge-plan` が計画の後の値を `state["limits"]` へ書き、改修計画のコメントに「時間の上限」の表と項目ごとの締め切りを載せる。`--max-fix-rounds` / `--test-timeout` は廃止の知らせだけにし、`DEFAULT_TEST_TIMEOUT` / `DEFAULT_MAX_FIX_ROUNDS` / `IMPL_STALL_MARGIN` と `init` の `IMPL_STALL_TIMEOUT` を外す。報告は固定のまま残した値を並べる | 決定 24。`limits.py` の表は変えない（cross-review と共有） |
| I17 | `merge-plan` が採った項目の D5 を Jev に問い（使えなければ `risk`）、`items[].public_io` に残す。検証の `_d5` は読むだけ。`merge-implement` は段 1 で決まらないテストの差分を `review_test_judgements` へ直接残し、`judge-test-changes` の起動・`merge-test-judgements`・雛形・差分の書き出しを外す。改修計画の項目の節に「レビューで確かめるテストの差分」を載せる | 決定 25。計画の後に Jev と LLM の CLI を呼ばないことは、呼び出し口を差し替えて呼ばれたら落とすテストで固定する |

## 修正対象

- `plugins/ndf/skills/cross-refactoring/scripts/refactor.py`
- `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/`: 新 `budget.py` / `allocation.py` / `danger.py` / `items.py` / `testcmd.py` / `undo.py`、`commands/propose.py` / `plan.py` / `implement.py`、改 `commands/setup.py` / `converge.py` / `gate.py` / `report.py`、`proposals.py` / `plan.py` / `measure.py` / `outbound.py` / `paths.py` / `gitfacts.py` / `verify.py` / `vocabulary.py` / `intake.py`、外す `rounds.py` / `commands/apply.py`
- `plugins/ndf/skills/cross-refactoring/data/allocation-defaults.json`（新）
- `plugins/ndf/skills/cross-refactoring/prompts/`: 改 `propose.md` / `fix.md` / `judge-test-changes.md`、新 `plan.md` / `add-tests.md` / `implement.md`、外す `propose-tests.md` / `apply.md`
- `plugins/ndf/skills/cross-refactoring/scripts/launch-cli.sh`
- `plugins/ndf/skills/cross-refactoring/SKILL.md` と `docs/01〜04`（02・04 は置き換え）
- 共通層 `plugins/ndf/scripts/lib/jev.py`（新）・`limits.py`・`assignment.py`
- テスト: `plugins/ndf/skills/cross-refactoring/tests/`・`plugins/ndf/scripts/tests/`
- 呼び出し元の文書: `CLAUDE.md` の cross-refactoring の節、`development-workflow` の工程表で引数を並べている箇所

## タスク分解

各タスクは失敗するテスト → 通す最小実装 → 整理の順で進め、タスクの終わりに 1 コミットを積む。

### Task 1: 純粋な判定（見積り・配分・危険の印・語の組み立て）

- **対象:** `refactor_lib/budget.py`・`allocation.py`・`danger.py`・`testcmd.py`・`data/allocation-defaults.json` とそれぞれのテスト
- **変更内容:** 設計の「時間の決め方」「データ構造: 履歴と配分テーブル」「危険の印」「実装担当の `plan` の結果ファイル」の組み立ての表をそのまま関数にする。`allocation` は根を引数で受ける
- **満たす受け入れ条件:** AC8 AC10b AC12（締め切りの計算）AC13 AC14（印の判定）AC17〜AC20 AC3b（判定の部品）
- **進め方:** 単体のテスト駆動。worker（修正）へ 1 モジュールずつ出す

### Task 2: 共通層（Jev・実装担当・上限の表）

- **対象:** `scripts/lib/jev.py`・`assignment.py`・`limits.py` とテスト
- **変更内容:** `jev.py` は使えるかの判定（鍵・`NDF_JEV`・公開・疎通）と `score` / `boolean` の 1 問、失敗時は `None`。`assignment.choose_implementer`（名指し → ホスト → 先頭）を足し `impl_assign` を外す。`limits.py` は `apply` を `implement` へ改め `plan` / `add-tests` を足す
- **満たす受け入れ条件:** AC21 AC22 AC23（部品）
- **進め方:** 単体のテスト駆動。HTTP は偽の応答へ差し替える

### Task 3: 状態の版 2 と `init` / `start-phase`

- **対象:** `commands/setup.py`・`refactor.py`・`items.py`（`rounds.py` から移す）・テスト
- **変更内容:** `--budget-minutes` / `--implementer` / 廃止 3 引数の知らせ・AC3b・`schema: 2` の初期状態・旧い状態で止める／作り直す・再開の `PHASE`・Jev の判定を 1 度・`start-phase`
- **満たす受け入れ条件:** AC1〜AC3b AC6（記録） AC21 AC24 AC25
- **進め方:** 既存の `test_init.py` のうちラウンドに依らない部分を保ち、新しい形のテストを足す

### Task 4: 提案と計画（`merge-proposals` / `merge-plan`）

- **対象:** `commands/propose.py`・`commands/plan.py`・`proposals.py`・テスト
- **変更内容:** 鍵が同じ提案の統合・語彙としきい値・30 組 × 組の中 3 件の切り出し。計画の取り込み・段と `merge_into` の Jev 経由の決定・順位・見積り・飛ばして詰める・締め切り・`test_targets` の検査と語の並び・`no_target`
- **満たす受け入れ条件:** AC7 AC8 AC9 AC10b AC22（段・同じ変更か）
- **進め方:** 単体のテスト駆動

### Task 5: テスト追加と実装の取り込み（`merge-tests` / `merge-implement`）と取り消し

- **対象:** `commands/implement.py`・`undo.py`・`verify.py`（検査の部品の再利用）・`intake.py`・`gitfacts.py`・テスト（git を使う結合）
- **変更内容:** フェーズの範囲のコミットを `Item-Id` で項目へ対応づけ、手順の検査（I5）・`test_failed`（足したテストを今のコードで走らせる）・`not_done`（締め切り）・項目の所要（コミットの時刻）・取り消し（I6）・段 1 の保留（I7）
- **満たす受け入れ条件:** AC10 AC11 AC12
- **進め方:** git の一時リポジトリを使う結合テスト

### Task 6: 検証と修正（`verify` / `merge-fix` / `merge-test-judgements`）

- **対象:** `commands/converge.py`・テスト
- **変更内容:** 限ったテストを語の並びごとに 1 回・共有した項目の扱い・上限と残り時間での取り消し（新しい順に 1 件ずつ）・危険の印と全体のテスト 1 回・落ちたら印を持つ項目の取り消しと `whole_test.reverted`・修正の取り込み
- **満たす受け入れ条件:** AC13〜AC16 AC22（D5）
- **進め方:** 結合テスト（git と偽のテストコマンド）

### Task 7: 最終ゲート・履歴への追記・報告

- **対象:** `commands/gate.py`・`commands/report.py`・`measure.py`・`plan.py`（改修計画の本文）・`outbound.py`・テスト
- **変更内容:** 検証の全体のテストの使い回し・`whole_test.reverted`・`final-fix` の担当を実装担当に。`finalize --review-status`。報告（フェーズ別の所要・予算との差・見送りの理由別・全体のテスト・Jev）
- **満たす受け入れ条件:** AC16b AC17 AC26 AC27
- **進め方:** 単体のテスト駆動

### Task 8: 雛形と起動（`launch-cli.sh` と `prompts/`）

- **対象:** `launch-cli.sh`・`prompts/{propose,plan,add-tests,implement,fix,judge-test-changes}.md`・テスト
- **変更内容:** フェーズの名前と雛形の対応・締め切りと項目の受け渡し・観点の一覧（語彙の値）・上限の数字の受け渡し
- **満たす受け入れ条件:** AC4 AC5 AC6 AC12（雛形に締め切りが渡る）
- **進め方:** 展開の単体テスト（文言は照合しない。渡った値と語彙の列挙を見る）

### Task 9: 手順書と呼び出し元の文書

- **対象:** `SKILL.md`・`docs/01〜04`・`CLAUDE.md` の節・`development-workflow` の該当箇所
- **変更内容:** 5 フェーズの駆動の bash（繰り返しは検証と修正の 1 つだけ）・引数の表・終了コード・完了報告
- **満たす受け入れ条件:** AC27（手順として） 要求の対象範囲の文書の改訂
- **進め方:** 文書の検査スクリプトと、駆動の bash の変数の出所の検査（`check-skill-shell-vars.py`）

### Task 10: 旧い形の除去と全体の確かめ

- **対象:** `rounds.py`・`commands/apply.py`・旧い雛形・ラウンド制に依るテスト
- **変更内容:** 使われなくなったものを外し、ラウンド制に依るテストを新しい形のテストへ置き換え済みであることを確かめる
- **満たす受け入れ条件:** AC28
- **進め方:** 全体のテストと検査スクリプト

## 影響範囲

- cross-refactoring の利用者の手順（引数・状態ファイル・報告）。旧い引数は知らせて無視する
- `development-workflow` の構造改善の工程（引数の並びだけ）
- 共通層 `assignment.py` / `limits.py` を読む cross-review（`impl_assign` は cross-refactoring だけが使う。`limits.py` の `apply` は cross-refactoring だけが使う。どちらも grep で確かめてから外す）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `commands/apply.py`（1224 行）と `converge.py` を大きく書き換える。取り消し・公開の保留・再開の防御が多い | 取り消しと公開の部品（`push_with_retry_marker` / `flush_pending_push` / `discard_impl_leftovers` / `_revert_range` / `_replay_commits`）は流用し、群の扱いだけを捨てる。タスクごとにテストを通す |
| 旧いテストの大半がラウンド制に依る | Task 10 でまとめて外す前に、ラウンドに依らない検査（範囲・トレーラー・差分予算・期待値の変更・文言固定テスト・公開・同期）のテストが新しい経路でも残っていることを表で確かめる |
| 実機の CLI の振る舞い（締め切りを守るか） | 設計のとおり手動の 1 回（`--budget-minutes 30`）で確かめる。この Pull Request の検査の工程で行う |

## 切り戻し手順

- この Pull Request を revert すれば v10.17.6-dev.1 の形へ戻る。利用者の手元に残るのは履歴の JSONL（`<metrics>/<owner>--<repo>/cross-refactoring-allocation.jsonl`）だけで、旧い版は読まない

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している（Pull Request の本文に表で残す）
- [ ] standard の検査（実装レビュー・構造改善・手動の 1 回）は次の持ち場が通す
