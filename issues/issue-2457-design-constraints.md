# #2457 の設計: 制約の棚卸しの処置と、予算の呼び出し元

[issue-2457-design.md](issue-2457-design.md) から分けた文書である。受け入れ条件 15（棚卸しの C と D の各行が「置き換えた」「削った」「残した（理由）」のどれかになる）の表と、`--budget-minutes` の呼び出し元の一覧を持つ。

## 今の位置の引き直し

棚卸しの表の位置は v10.27.14 の時点のものである。タグ `ndf--v10.27.14` と HEAD（`0491f91d5`）を `git diff` で比べた。表が挙げるファイルのうち、次のファイルは差分が 0 行だった。

| 置き場 | ファイル |
| --- | --- |
| `refactor_lib/` | `budget.py`・`init_test.py`・`verify.py`・`scope_check.py`・`fix_intake.py`・`wholetest.py`・`undo.py`・`triage.py`・`stop_revert.py`・`gate_ci.py` |
| `refactor_lib/commands/` | `gate.py`・`converge.py`・`plan.py` |
| `references/` | `design-principles.md` |

行がずれたのは `refactor_lib/commands/implement.py` の 325 行より後ろだけで、制約の中身が変わった行は無い。下の表の「今の位置」は HEAD の行番号である。

パスは `plugins/ndf/skills/cross-refactoring/` からの相対で、`refactor_lib/` は `scripts/refactor_lib/` を指す。

## C と D と B の各行の処置

処置の語は受け入れ条件 15 の 3 つ（置き換えた・削った・残した）を使う。実装の Pull Request で、この表の処置が実装と一致することを確かめる。

| # | 制約 | 分類 | 今の位置 | 処置 | 処置の中身 |
| --- | --- | --- | --- | --- | --- |
| 12 | 振る舞いを変える修正が要ると分かった項目は適用しない | B | `prompts/implement.md:78` | 置き換えた | 計画で `risk` と一緒に問い、実装の中で分かったときは適用せずに成立しない理由を答えに書く。オーケストレーターが判定の担い手へ回す（決定 3） |
| 13 | バッファを予算から先に引く | D | `refactor_lib/budget.py:36-59`・`172-174` | 削った | 予算からバッファも経過も引かない。選定は実効の予算と見積りだけで決まる（決定 1） |
| 14 | 使える時間が 0 分以下なら全件を見送って止める | D | `refactor_lib/commands/plan.py:304-305`・`333` | 削った | 使える時間の概念が無くなる。予算が 1 分以上なら止まらない |
| 15 | 提案・計画の枠を予算の割合で決め、時刻で CLI を止める | D | `docs/02-plan-and-implement.md:138-150`・`refactor_lib/timeline.py:26-29`・`171-172` | 置き換えた | 枠を消し、止まった CLI の検知（`limits.json` の `monitor.phase.<手順>` と `monitor.stall.<ランタイム>`）だけを残す |
| 16 | 着手前のテストの上限を予算から決め、超えたら `init-timeout` | D | `refactor_lib/init_test.py:79-88`・`refactor_lib/commands/setup.py:618` | 置き換えた | 上限を予算から決めず、共通ライブラリの予算なしの式（`3·max(w, m)`、所要が無ければ既定の秒）にする。超えたときの `init-timeout`（終了コード 6）はハングの検知として残す |
| 17 | 枠が予算に収まらなければ `SKIP=window` | D | `refactor_lib/init_test.py:96-127`（`SKIP=window` は 113）・`refactor_lib/timeline.py:233` | 削った | 枠と想定最大時間の下限を消す。`window` の種類は結果 JSON から無くなる |
| 18 | テスト追加・実装の終わりで CLI を止め、未コミットを捨て、`not_done` | C | `refactor_lib/commands/implement.py:205-212`・`290`・`refactor_lib/commands/readopt.py:107` | 置き換えた | 実装の終わりを消す。CLI は全項目を終えるか安全網の残りで止まる。止まった CLI の後にコミットの無い項目は、残りの項目だけで同じ手順を起動し直す（差し戻し）。起動し直しても 1 件も進まなければ判定の担い手へ回す |
| 19 | 担当を振り替えても締め切りを延ばさない | C | `docs/02-plan-and-implement.md:215-217` | 削った | 締め切りが無くなる。振り替えの規則（`assignment.after_no_result`）は変えない |
| 20 | 足したテストが今のコードで落ちたら `test_failed` で見送る | C | `refactor_lib/commands/implement.py:165`・`269` | 置き換えた | 差し戻して、今のコードで通るようにテストを直させる。前進の判定（落ちたテストの集合）を同じに当て、停滞したら判定の担い手へ回す。`test_failed` の見送りの理由は使わなくなる（語彙は旧い記録を読むために残す） |
| 21 | テストの追加はテストのファイルだけ・1 項目 1 コミット。外れたら取り消す | D | `refactor_lib/commands/implement.py:323`・`331` | 削った | 取り消さない。テスト以外のファイルの変更とコミットの数は報告へ載せ、コードレビューへ引き継ぐ |
| 22 | `Item-Id` の無い・計画に無いコミットを取り消す | C | `prompts/implement.md:52-53` | 置き換えた | 差し戻して付け直させる。未公開のコミットなので、担当が手元で記名を直してよい。push の直前の照らし（#50）は変えない |
| 23 | トレーラーが欠けたら項目を取り消す | D | `refactor_lib/verify.py:27-41` | 削った | 取り消さない。`Impl-Runtime` / `Impl-Model` はオーケストレーターが起動した担当から状態ファイルへ記録し、欠けは報告に 1 行出す |
| 24 | 範囲の外の既存ファイルを変えた項目を取り消す | D | `refactor_lib/scope_check.py:197` | 削った | 範囲（`--scope`）は案出しにだけ使う。範囲の外で変えたファイルは `items[].outside_scope` に分類と一緒に残し、報告とコードレビューへ引き継ぐ |
| 25 | テストの期待値が残らなければ取り消す | C | `refactor_lib/verify.py:226-239` | 置き換えた | 差し戻して期待値を戻させる。決まらない差分を最終ゲートのレビューへ引き継ぐ今の扱いは変えない |
| 26 | 文書の文言を固定するテストを足したら取り消す | C | `refactor_lib/commands/implement.py:410-411`（呼び出しは 332-335・435） | 置き換えた | 差し戻して、そのテストだけを外させる |
| 27 | 実装の中でテストが落ちて直せなければ戻して次へ | C | `prompts/implement.md:32-33` | 置き換えた | 直し切れないときもコミットして次へ進み、答えに「成立しない」と思う理由を書く。合否は検証と成立の判定が決める |
| 28 | 修正の残り時間が足りなければ落ちた項目を取り消す | C | `refactor_lib/commands/converge.py:69-79`・`89-104` | 置き換えた | 時計を見ない。前進の判定と、停滞・振動での判定し直しに置き換える（決定 4） |
| 29 | 修正の範囲にコミットが無ければ `unfixed` で取り消す | C | `refactor_lib/fix_intake.py:134-157`・`refactor_lib/commands/converge.py:107` | 置き換えた | 取り消さない。落ちたテストの集合が変わらないので停滞として扱い、判定し直しへ回す |
| 30 | 範囲テストを共有した項目を新しい順に取り消す | C | `docs/04-verify-and-report.md:37-39`・`refactor_lib/commands/converge.py:38` | 置き換えた | 共有した範囲テストが落ちたら、共有した項目すべてを修正へ回し、成立の判定も項目ごとに行う |
| 31 | 手順を外れたコミットが 1 件でもあれば修正の範囲ごと取り消す | C | `refactor_lib/fix_intake.py:42-76` | 置き換えた | 外れたコミットだけを差し戻す。手順どおりのコミットは受け取る |
| 32 | 振る舞いを変えないと直せないなら直さずに終える | C | `prompts/fix.md:39-40`・`prompts/final-fix.md:39` | 置き換えた | 直さずに終え、答えに成立しない理由を書く。オーケストレーターが判定の担い手へ回し、成立しないと判定した項目だけを打ち切る（受け入れ条件 9） |
| 33 | 危険フラグの全体テストは 1 回だけ | D | `docs/04-verify-and-report.md:42-45`・`refactor_lib/commands/converge.py:194` | 削った | 直した後と打ち消した後は、落ちたテストを確かめ直すために全体テストを走らせ直してよい |
| 34 | 全体テストの変更起因を締め切りまで直し、通らなければ取り消す | C | `docs/04-verify-and-report.md:52-68` | 置き換えた | 原因の項目（`culprit.judge`）へ結んで成立の判定と修正の繰り返しに入れる。原因が決まらない失敗は最終ゲート修正と同じ扱い（どの項目にも結ばない修正）にする |
| 35 | JUnit を読めず着手前も red なら危険フラグの項目をまとめて取り消す | C | `refactor_lib/wholetest.py:124-130` | 置き換えた | 落ちたテストの材料をログから組み、比較の木で同じテストを走らせて無関係な失敗を見分ける。まとめて取り消す経路を消す |
| 36 | 積み直しが衝突したらその項目も外す | D | `docs/04-verify-and-report.md:160-180`・`refactor_lib/undo.py:215-251` | 置き換えた | 積み直しを使わない（打ち消しは `git revert` だけ）。衝突したら止めて人へ渡す（受け入れ条件 19） |
| 37 | 取り消しが衝突したら終了コード 4 | D | `refactor_lib/undo.py:212`・`224`・`247` | 置き換えた | 打ち消しの前の HEAD へ戻し、結果 JSON を `gate`・終了コード 10 にして、安全網と同じ材料を載せる（受け入れ条件 19） |
| 38 | 最終ゲートで JUnit を読めないと、変更起因を判定せずに不合格 | C | `refactor_lib/commands/gate.py:250-262`・`refactor_lib/triage.py:49-72` | 置き換えた | 落ちたテストの材料（ID・ログの置き場・走らせ直すコマンド）を組み、比較の木で見分ける。材料は修正担当へ渡る（受け入れ条件 13） |
| 39 | 最終ゲート修正は締め切りに入るときだけ始める | C | `refactor_lib/commands/gate.py:205-217` | 削った | 最終ゲート修正は前進する限り続ける |
| 40 | 確かめを締め切りまでに始められなければ起点の木へ戻す | C | `refactor_lib/commands/gate.py:76-79`・`114-138` | 削った | 確かめの開始の締め切りが無くなる。木が起点と同じときに確かめを省く経路（`131-133`）も消す（受け入れ条件 21） |
| 41 | 打ち切りの後、原因の項目を 1 件ずつ取り消す。挙げられなければ起点へ戻す | C | `refactor_lib/stop_revert.py:58-83` | 削った | `stop_revert.py` を消す。最終ゲートの失敗は検証と同じ仕分け・成立の判定・修正の繰り返しへ入る |
| 42 | 起点の木へ戻すコミットを積み、全項目を取り消した記録にする | C | `refactor_lib/stop_revert.py:34-48`・`86-116` | 削った | 起点への戻しを消す。打ち消すのは成立しない項目のコミットだけである（受け入れ条件 12） |
| 43 | 単独起動で時間により打ち切ったら終了コード 1 | C | `refactor_lib/commands/gate.py:100-103`・`324-332` | 削った | 時間で打ち切らない。止まるのは安全網と打ち消しの衝突と判定の担い手が居ないときで、どれも `gate` |
| 44 | 最終ゲート修正の申告漏れ・範囲外の変更で修正の範囲ごと取り消す | C／D | `prompts/final-fix.md:60-61`・`94-96`・`refactor_lib/commands/final_fix.py` | 置き換えた | 範囲外の変更の部分は削る（#24 と同じ）。申告漏れのコミットだけを差し戻す（#31 と同じ） |
| 45 | プロンプトの「取り消さずに失敗として報告」が実装の取り消しと食い違う | D | `prompts/final-fix.md:40-41` | 置き換えた | 「成立しない理由を答えに書く」に揃え、取り消しはオーケストレーターが成立の判定の後にだけ行うと書く |
| 46 | CI の待ちの上限を予算から決める | D | `refactor_lib/gate_ci.py:59`・`plugins/ndf/scripts/lib/test_strategy.py:835` | 置き換えた | 共通ライブラリの予算なしの式（`3·c`、c が無ければ既定の秒）にする |
| 47 | 計画の後は判断のために LLM を呼ばない | D | `references/design-principles.md:11` | 削った | 成立の判定と判定し直しを、実装担当と別のランタイムの LLM に行わせる（決定 3）。取り消し・前進の判定・仕分けの機械の規則はスクリプトが決めるまま |

分類 A（#1〜#11）と E（#48〜#53）は中身を変えない。A の #10 だけは式を「経過とバッファを引かず、実装時間の見積りで数える」へ変える（決定 1）。#11 の採り直しは、残りを「実効の予算 − 採った項目の実装時間の実績の和」で数える。

処置の件数: 置き換えた 22 件・削った 14 件・残した 0 件（#12〜#47 の 36 行）。

## `--budget-minutes` の呼び出し元

リポジトリ全体を次のコマンドで探した結果である（2026-11-09、HEAD `0491f91d5`）。

```bash
grep -rn "budget-minutes\|budget_minutes\|refactor_budget_minutes"
```

| 呼び出し元 | 渡している値 | この変更での扱い |
| --- | --- | --- |
| `plugins/ndf/scripts/supervise_lib/templates.py:233-234`・`255` | `refactor_budget_minutes` があるときだけ ` --budget-minutes N` を付ける | 渡し方は変えない。値は実装の予算として読まれる |
| `plugins/ndf/scripts/supervise_lib/templates.py:254` | ステップの `timeout` = `with_margin((予算 または 既定) × 60)` | 「実効の予算 ×（1 + 安全網の倍率）」へ変える（決定 8） |
| `plugins/ndf/scripts/supervise_lib/paths.py:44-46` | 既定の予算を cross-refactoring の `DEFAULT_BUDGET_MINUTES` から読む | 変えない（値が 60 分になる） |
| `plugins/ndf/scripts/supervise_lib/new_args.py:20-27`・`186-190`・`decl.py:23`・`60-77`・`250`・`plan.py:83-85` | `--refactor-budget-minutes` と `.ndf/supervise.json` の `refactor_budget_minutes` を受ける | 変えない。説明の文（`plan.py` の説明）を実装の予算の語に合わせる |
| `plugins/ndf/skills/development-workflow/references/stage-notes.md:122` | 「必要なら `--budget-minutes`（既定 30 分）」と書くだけで、コードは渡していない | 文を「実装の予算（既定 60 分）」へ直す |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/` | プランの雛形 6 件は `"budget_minutes": null` | 変えない |

`plugins/ndf/scripts/supervise_lib/steps.py:140` の `budget_minutes is None` は変えない。supervise 自身のテストの上限の式（予算なし）を指し、cross-refactoring の予算ではない。
