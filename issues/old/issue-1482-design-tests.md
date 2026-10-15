# #1482: テスト設計と未確認のまま残ること

設計の本体は [issue-1482-design.md](issue-1482-design.md) にある。

## テスト設計

置き場は `plugins/ndf/skills/cross-refactoring/tests/`（要求の AC-R3 と検証手段は `scripts/tests/` と書くが、
実際の置き場はここである。「未確認のまま残ること」）。再現手順は実物の git（一時ディレクトリのリポジトリと bare の
origin）で組む。フィクスチャと起動の形は実装（`tdd-cycle`）で決める。

### 受け入れ条件

| 受け入れ条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| AC-1 | `undo.py` と `publish.py` の構文木に、`status` と `LIVE` / `REVERTED` の比較・代入が無い。判定の関数は `ledger` にだけ定義がある | `undo.py` に `item.get("status") not in LIVE` を戻す |
| AC-2 | `refactor_lib/commands/` の構文木に `revert_range` / `revert_item_commits` の呼び出しが無い。4 つの経路はどれも `undo.drop` か `undo.discard` を通る | `converge._apply_fix_result` に `revert_range(...)` を戻す |
| AC-3 | 取り消しの途中（積み直しの後・記録の前）で落として再開すると、HEAD の内容・`items[].status`・`drops` の件数が、落とさずに終えたときと一致する。取り消し済みの改善項目は 2 度目の `drops` に現れない | `pending_drop.before` へ戻さずに今の HEAD から逆再生する |
| AC-817-1 | 採用 I-001・取り消し I-002 の後に `Item-Id: I-002` のコミットを積み、公開の入口を呼ぶと、終了コード 4・origin の head は手順 1 の後の値のまま・出力にその SHA と `Item-Id=I-002` が出る | 照合を外す／照合を `Item-Id` の値で決める（I-002 を採用済みと扱うように壊す） |
| AC-817-2 | `Item-Id` の無いコミット・計画に無い `Item-Id` のコミットでも同じく止まる | `Item-Id` の無いコミットを `orchestrator` と分類する |
| AC-817-3 | 採用した項目のコミットと、同期・計画の記録・取り消しの revert だけなら push する | 同期のコミットを台帳へ記録し忘れる |
| AC-817-4 | 最終ゲートの後の公開（`recheck`・`merge-final-fix`・`flush_pending_push(gate)`）でも、残留コミットがあれば止まる | 照合を `enter_final_gate` の中にだけ置く |
| AC-1399-1 | 13 項目の履歴で `undo.drop` を 5 回（うち 1 回は広げる）呼ぶと、どの回も `git revert` が 1 度も打たれず、`cherry-pick` の対象に前の取り消しの積み直しのコミットが 2 重に入らない | 起点を毎回 `plan.base_sha` に固定し、範囲全体を逆再生する |
| AC-1399-2 | 上の 5 回の後、起点から HEAD までのコミット数が「改善項目のコミットの数 × 2 + 同期・記録のコミットの数」以下 | 積み直しの前に `reset` しない |
| AC-1399-3 | 上の各回の後、起点に「取り消されていない改善項目のコミット」を古い順に当てた木と HEAD の木が一致する | `replay` の並びを新しい順にする |
| AC-1399-4 | 実行の全体（テスト追加・実装・修正・結果なしの起動を閉じる・取り消し）を通して `push_head` の最初の呼び出しが `enter_final_gate` から来る。最終ゲートへ入る前の `push_head` の呼び出しは 0 回 | `implement._finish` の push を戻す |
| AC-1399-5 | 最終ゲートへ入る前に、取り消しの失敗（終了コード 4）・push の失敗・打ち切りで止めると、origin の head は開始時の値のまま | `undo.drop` に `pending_push = True` を戻し、`_prepare` の `flush_pending_push` を戻す |
| AC-1399-6 | `final_gate` が無い・`status` が `passed` でない状態で drive を終えると、`status: stopped`・終了コード 1・`metrics.adopted` が 0。`report` に「採用: 未確定」が出る | `counts` の採用を `verified` の数のままにする |
| AC-1237-1 | 6 項目の計画で I-002 を取り消すと、`reverted` は I-002 と I-004 だけで、I-003・I-005・I-006 の変更は HEAD に残る | 広げる基準を「消すコミットのファイル」でなく「全ファイル」にする／`all` を戻す |
| AC-1237-2 | `drops[-1]` が `mode: widened`・`dropped: ["I-002", "I-004"]`。I-003・I-005・I-006 の `failure_reason` に「巻き込まれた」が無い | 広げた項目の集合に `live` 全体を入れる |
| AC-1237-3 | 広げた後に別の項目を 2 回取り消しても、AC-1399-2 の上限を超えない | 2 回目以降の範囲に 1 回目の積み直しを逆再生の対象として含める |
| AC-1237-4 | 同じファイルまで広げても衝突する履歴では、終了コード 4・HEAD は取り消しの前・出力に衝突した SHA と広げた項目の ID | 2 度目の衝突で `all` へ進む |
| AC-1237-5 | 上の 6 項目の計画を最終ゲートの合格まで通すと、採用が I-003・I-005・I-006 の 3 件 | 積み直しの衝突で `all`（全件の取り消し）へ進む |
| AC-R1 | 対象の無い取り消しは `mode: skip` で git に触れない。コミットの無い項目は git に触れずに `reverted` になる | `_close_commitless` の前に計画を作り `reset` を打つ |
| AC-R2 | `push_head` が `git` へ渡す引数に `--force` / `--force-with-lease` / `+` で始まる参照が無い | refspec を `+HEAD:<head>` にする |
| AC-R3 | `cross-refactoring` の既存のテストがすべて通る（上の 3 本の変更を除く） | — |

### 不変条件

| 不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| I1 | どの取り消しの後も、`ledger.classify(公開した地点..HEAD)` に `stray` が無い | `stray` を「残す」に分類する |
| I2 | AC-817-1〜4 と同じ | 同上 |
| I3 | 最終ゲートの後に危険フラグの項目を 2 件、1 件ずつ取り消すと、`git revert` の数は 2 件の項目のコミットの数と等しく、同じ SHA を 2 度戻さない | 公開済みの範囲の `revert` の並びに、前の取り消しの revert を含める |
| I4 | AC-1237-4 と同じ。加えて、広げた項目のコミットだけが触るファイルを触った項目は広がらない（2 段目が無い） | 広げる処理をくり返しにする |
| I5 | AC-1399-3 と同じ。最終ゲートの後の取り消しでは「起点に残すコミットを当て、戻すコミットを逆に当てた木」と一致する | 公開済みの取り消すコミットを `revert` せずに残す |
| I6 | `phase` が `final` でない状態で `push_with_retry_marker` / `flush_pending_push` を呼ぶと、`push_head` を呼ばずに終了コード 4 | 入口の条件を外す |
| I7 | AC-3 と同じ | 同上 |
| I8 | AC-1399-6 と同じ。加えて、最終ゲートが `passed` の実行では `adopted` が `verified` の数に等しい（退行しない） | `passed` でも 0 にする |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 最終ゲート修正のコミットの扱い | 決定 4 は、受け入れた最終ゲート修正のコミットを「残すコミット」に含める。要求の前提 3 の 2 種類の読みを広げているため、設計 Pull Request のレビューで要求の側を直すか確かめる |
| テストの置き場のパス | 要求の AC-R3 と検証手段は `plugins/ndf/skills/cross-refactoring/scripts/tests/` と書くが、実際は `plugins/ndf/skills/cross-refactoring/tests/` にある。仕様のコピーは手で直さないため、課題の本文を直して `spec-copy.py write` で作り直す |
| 旧い版で途中の push を済ませた状態ファイルからの再開 | `ledger` が無いと公開した地点を `plan.base_sha` とみなし、push 済みのコミットを積み直しで置き換えうる。次の push は fast-forward にならずに失敗し、origin は壊れない。版を上げたのと同じ実行を再開する場面でだけ起きるため、失敗の出力で人へ戻す扱いにとどめる。起きる頻度は測っていない |
| `reset --hard` の後の worktree の未追跡ファイル | `reset --hard` は未追跡のファイルを消さない。取り消しの前に `discard_impl_leftovers` が worktree を掃除する今の順序に頼る。掃除の後に担当のプロセスが未追跡のファイルを書くと、次の同期の前の清浄性のチェックで止まる（今と同じ） |
| 広げる基準のファイルが大きいとき | 同期の `stray` コミット（生成物）が多くのファイルを触ると、広げる対象が増える。最終ゲートより前は同期のコミットが無いため起きないと見込むが、最終ゲートの後の取り消しでの件数は測っていない |
| `CLAUDE.md` の cross-refactoring の節 | 今の記述（公開するのはオーケストレーターだけ・同じファイルまで広げる）は変えた後の振る舞いと食い違わないため、書き換えない。公開の時点を足すなら指示書の運用の節の変更（共通原則 C7）になり、人の承認が要る |
| `docs/specifications/cross-refactoring-verify-and-final-gate.md` の `all` の記述 | 確定仕様の記録であり、この変更の実装計画では直さない。完了後の `plan-to-spec` で新しい振る舞いへ書き直す |
