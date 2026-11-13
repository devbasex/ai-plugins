# #2963 の詳細設計: 判断の関数・耐久の層・検査の規則と、11 本の子 Issue のファイルと関数

設計文書 [issue-2963-design.md](issue-2963-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。決定の番号（決定 1〜16）は [issue-2963-design-decisions.md](issue-2963-design-decisions.md) を、子 Issue の名前（`RF1`〜`RF8`）と不変条件（I1〜I13）は設計文書を指す。

子 Issue の行と流す順序は、#1142 の頭の詳細設計 [issue-1142-design-detail.md](issue-1142-design-detail.md) の「実装計画」と「流す順序」へ入れた。この文書は 11 本の子 Issue の中のファイルと関数だけを持つ。

行番号は 2026-11-13 のworktree（develop c256d27a8 の上の設計のブランチ）のもので、着手の時点で数え直す。

| 語 | この文書での意味 |
| --- | --- |
| 次の手 | 判断の関数が返す値（`decide_loop.Next`）。実行する手 1 つか、終わり（結果の状態）のどちらか |
| 実行の結果 | 手を実行する関数が返す値。判断の関数の次の呼び出しへ渡る |
| 項目のファイル | 例外リスト `scripts/script-structure-allow/` の中の 1 項目 1 ファイルの JSON |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除）:

```text
plugins/ndf/scripts/
├── lib/
│   ├── decide_loop.py*            (RF1)
│   ├── durable.py・post_queue.py  (RF1b)
│   ├── parallel_decide.py*        (RF6a)
│   ├── parallel_plan_decide.py*   (RF6b)
│   ├── parallel_{run,reply,answer,route,launch,observe,machine}.py (RF6a)
│   ├── parallel_{files,homes,lane,external,plan,lane_cli}.py       (RF6b)
│   ├── sprint_loop_decide.py*     (RF7)
│   └── sprint_loop{,_select,_record,_gate,_gate2,_exclusion,_cli}.py (RF7)
├── supervise_lib/
│   ├── step_decide.py*・queue_decide.py*   (RF4。queue_decide は RF5a が広げる)
│   ├── plan_decide.py*            (RF5a)
│   ├── sprint_decide.py*          (RF5b)
│   ├── flow.py・queue.py          (RF4・RF5a)
│   ├── engine.py・queue_fill.py・queue_resume.py・admission.py・commands.py・state.py (RF5a)
│   └── sprint.py・sprint_waves.py・sprint_routes.py・sprint_lane_close.py (RF5b)
├── release_lib/
│   ├── stage_{present,approve,production,cd,verify}.py*  (RF8)
│   └── included.py・instructions.py・materials.py       (RF8)
├── release-steps.py               (RF8)
├── parallel.py                    (RF6a・RF6b)
├── supervise.py                   (RF5a)
└── tests/                         (各子 Issue)
plugins/ndf/skills/{cross-review,cross-refactoring}/scripts/drive.py (RF1b)
scripts/
├── lib/parsed.py*・py_imports.py  (RF3)
├── structure_checks/limits_rules.py (RF3)
├── contracts_lib/{limits_decl,core_checks,extract_py}.py (RF3)
├── check-script-structure.py      (RF2・RF3)
├── check-ndf-contracts.py         (RF3)
├── script-structure-allow/        (RF2 がディレクトリで、ほかは項目のファイルの名前で触る)
└── ai-smells-baseline/            (RF1b)
docs/specifications/ndf-scripts-import-cycles.md・ndf-script-structure-criteria.md (RF2)
```

**依存の向き。** 判断のモジュール（名前が `_decide.py` で終わるもの）と `lib/decide_loop.py` は、`durable`・`dbos`・プロセスとファイルを扱う部品を読まず、ほかのどのモジュールからも読まれる側に立つ。耐久の層（`supervise_lib/flow.py`・`lib/parallel_run.py`）は `durable` と判断のループと判断のモジュールの 3 つを読み、判断のモジュールは耐久の層を読まない。`supervise_lib` は `commands → flow → engine・queue → _decide のモジュール` の一方向で、`engine` と `queue` は `flow` を読まない（決定 3）。検査の側は `check-script-structure.py`・`check-ndf-contracts.py → scripts/lib/parsed.py` の一方向で、`parsed.py` はほかの検査のモジュールを読まない。この向きで輪は生じない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/decide_loop.py` | 新設 | 判断の関数と手を実行する関数を受けて、終わりの手が出るまで回す | 無し |
| `plugins/ndf/scripts/tests/test_decide_loop.py` | 新設 | 判断のループを DBOS を起こさずに確かめる | `plugins/ndf/scripts/lib/decide_loop.py` |
| `plugins/ndf/scripts/tests/test_decide_loop_durable.py` | 新設 | 耐久ステップを渡したときに記録から再実行で続くことを代表の 1 本で確かめる | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | 着手の時点の兆候の一覧に載る関数の握りつぶし・既定値への倒れを直す。公開の関数と `FORMAT` は変えない | `dbos` |
| `plugins/ndf/scripts/lib/post_queue.py` | 変更 | 同上。`lines` の項目は RF2 が消す（841 行） | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/skills/cross-review/scripts/drive.py` | 変更 | 同上（69 行目の `except Exception` を含む） | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 変更 | 同上 | `plugins/ndf/scripts/lib/durable.py` |
| `scripts/ai-smells-baseline/` | 変更 | 兆候の基準を、直した後の数で書き直す | 無し |
| `plugins/ndf/scripts/tests/test_durable.py` | 変更 | 握りつぶしを期待値に持つテストを、失敗を返す期待値へ直す | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/tests/test_post_queue.py` | 変更 | 同上 | `plugins/ndf/scripts/lib/post_queue.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/tests/test_drive_deferred.py` | 変更 | 同上 | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` |
| `scripts/check-script-structure.py` | 変更 | 行数の上限を 1,000 にし、規則 `lazy-cycle`・`decide-pure` を足し、`durable-boundary` を 2 つの耐久の層へ広げる（RF2）。構文木を `parsed` から受ける（RF3） | `scripts/lib/parsed.py` |
| `scripts/tests/test_check_script_structure.py` | 変更 | 3 つの規則と行数の上限の入力の木を足す | `scripts/check-script-structure.py` |
| `scripts/script-structure-allow/` | 変更 | 1,000 行以下の `lines` の項目 24 件を消し、輪 10 組と耐久の層の 2 件の項目を置く | 無し |
| `plugins/ndf/scripts/tests/test_import_cycles.py` | 削除 | 確かめていた 2 組を規則 `lazy-cycle` が全体で確かめる | — |
| `docs/specifications/ndf-scripts-import-cycles.md` | 変更 | 輪の確かめ方を規則 `lazy-cycle` と例外リストへ書き替える | 無し |
| `docs/specifications/ndf-script-structure-criteria.md` | 変更 | 1,000 行の安全網と、300 行・500 行の目安・責務で分ける基準を書く | 無し |
| `scripts/lib/parsed.py` | 新設 | 1 回の実行の中で、1 つのファイルの本文と構文木を 1 回だけ読んで返す | 無し |
| `scripts/tests/test_parsed.py` | 新設 | 解析の回数と、2 本の検査の違反の一覧が共有の前後で同じことを確かめる | `scripts/lib/parsed.py` |
| `scripts/lib/py_imports.py` | 変更 | 本文を受けて解析する `imported` を消し、構文木を受ける `imported_tree` だけにする | 無し |
| `scripts/structure_checks/limits_rules.py` | 変更 | 本文の代わりに構文木を受ける | 無し |
| `scripts/check-ndf-contracts.py` | 変更 | 構文木を `parsed` から受ける | `scripts/lib/parsed.py` |
| `scripts/contracts_lib/limits_decl.py` | 変更 | 上限の宣言の参照を 1 回の実行で 1 回だけ集める | `scripts/lib/parsed.py` |
| `scripts/contracts_lib/core_checks.py` | 変更 | 構文木を `parsed` から受ける | `scripts/lib/parsed.py` |
| `scripts/contracts_lib/extract_py.py` | 変更 | 同上 | `scripts/lib/parsed.py` |
| `plugins/ndf/scripts/supervise_lib/step_decide.py` | 新設 | プランの次のステップ・上限・遅れの再試行・知らないステップ・再開の前置きを決める | `plugins/ndf/scripts/lib/decide_loop.py` |
| `plugins/ndf/scripts/supervise_lib/queue_decide.py` | 新設 | 前提のプランの判定（RF4）と、再開の前置き・同時に流す組（RF5a）を決める | 無し |
| `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | 遷移のループを判断のループへ渡し（RF4）、キューの耐久ワークフローの起動を受け持つ（RF5a） | `plugins/ndf/scripts/supervise_lib/step_decide.py` |
| `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | 前提の判定を `queue_decide` へ渡す（RF4）。`flow` を読まない（RF5a） | `plugins/ndf/scripts/supervise_lib/queue_decide.py` |
| `plugins/ndf/scripts/tests/test_step_decide.py` | 新設 | 遷移の判断を DBOS を起こさずに確かめる | `plugins/ndf/scripts/supervise_lib/step_decide.py` |
| `plugins/ndf/scripts/tests/test_queue_decide.py` | 新設 | 前提の判定（RF4）と再開の前置き・同時に流す組（RF5a）を確かめる | `plugins/ndf/scripts/supervise_lib/queue_decide.py` |
| `plugins/ndf/scripts/tests/test_supervise_limits.py` | 変更 | 遷移だけを見るテストを `test_step_decide.py` へ移して消し、耐久を通す代表の 1 本を残す | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `plugins/ndf/scripts/tests/test_design_review_settled.py` | 変更 | 遷移だけを見るテストを消す | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `plugins/ndf/scripts/tests/test_base_sync.py` | 変更 | 同上 | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `plugins/ndf/scripts/tests/test_supervise_stop_exit.py` | 変更 | 同上 | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__supervise_lib__flow.py--_plan_steps--durable-boundary.json` | 削除 | 遷移のループを判断のループへ移したので項目を消す（RF4） | — |
| `plugins/ndf/scripts/supervise_lib/plan_decide.py` | 新設 | 差し込み `{state_dir}` に使う置き場を絶対のパスで決める | 無し |
| `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `Engine.run` を消し、`flow` を読まない | `plugins/ndf/scripts/supervise_lib/queue.py` |
| `plugins/ndf/scripts/supervise_lib/queue_fill.py` | 変更 | 着手の時点の兆候を直す。前提の結果の読み取り `results_named` は変えない | `plugins/ndf/scripts/supervise_lib/state.py` |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 変更 | 再開の開始点に前置きのステップを添える | `plugins/ndf/scripts/supervise_lib/queue_decide.py` |
| `plugins/ndf/scripts/supervise_lib/admission.py` | 変更 | 同時に流す組の判定を `queue_decide` へ移し、資源の枠のタグだけを持つ | 無し |
| `plugins/ndf/scripts/supervise_lib/commands.py` | 変更 | プランの実行とキューの起動を `flow` から呼び、置き場を `plan_decide` から受ける | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `plugins/ndf/scripts/supervise_lib/state.py` | 変更 | 着手の時点の兆候を直す | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/supervise.py` | 変更 | キューの起動の呼び先を `flow.cmd_queue` へ替える | `plugins/ndf/scripts/supervise_lib/commands.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__supervise_lib__engine.py--cycle--lazy-cycle.json` | 削除 | 輪 L5 をほどいたので項目を消す（RF5a） | — |
| `plugins/ndf/scripts/tests/test_plan_decide.py` | 新設 | 置き場の決め方を確かめる | `plugins/ndf/scripts/supervise_lib/plan_decide.py` |
| `plugins/ndf/scripts/tests/test_supervise_queue.py` | 変更 | 判断だけを見るテストを `test_queue_decide.py` へ移して消す | `plugins/ndf/scripts/supervise_lib/flow.py` |
| `plugins/ndf/scripts/tests/test_supervise_resume.py` | 変更 | 同上 | `plugins/ndf/scripts/supervise_lib/queue_resume.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_decide.py` | 新設 | リリース記録の番号・導入の確認の要否・閉じ方の判断を決める | 無し |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | `close_plan` が番号を `sprint_decide` から受ける | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | 着手の時点の兆候を直し、判断に当たる関数を `sprint_decide` へ移す | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 変更 | 導入の確認の要否を `sprint_decide` から受ける | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_lane_close.py` | 変更 | `close_plan` と同じ番号の読み方（158 行目）を `sprint_decide` から受ける | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` |
| `plugins/ndf/scripts/tests/test_sprint_decide.py` | 新設 | スプリントの判断を確かめる | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` |
| `plugins/ndf/scripts/tests/test_supervise_sprint_chain.py` | 変更 | 判断だけを見るテストを `test_sprint_decide.py` へ移して消す | `plugins/ndf/scripts/supervise_lib/sprint.py` |
| `plugins/ndf/scripts/tests/test_supervise_close_auto.py` | 変更 | 同上 | `plugins/ndf/scripts/supervise_lib/sprint.py` |
| `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | 見回りの手順の順・答えの組み立て・予約の数え方・止まった時間の起点を決める | `plugins/ndf/scripts/lib/decide_loop.py` |
| `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | 見回りのループを判断のループへ渡し、`_Run` の判断のメソッドを `parallel_decide` へ移す | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `plugins/ndf/scripts/lib/parallel_reply.py` | 変更 | `parallel_answer` を読まずに、`lane_repo`・`repo_lane` を `parallel_route` から読む | `plugins/ndf/scripts/lib/parallel_route.py` |
| `plugins/ndf/scripts/lib/parallel_answer.py` | 変更 | `parallel_run` を読まずに `relay`・`RELAY` を持ち主から読む。`lane_repo`・`repo_lane` を `parallel_route` へ移す | `plugins/ndf/scripts/lib/parallel_route.py` |
| `plugins/ndf/scripts/lib/parallel_route.py` | 変更 | `lane_repo`・`repo_lane` の置き場になる | `plugins/ndf/scripts/lib/parallel_files.py` |
| `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | 着手の時点の兆候を直し、起動の判断（`came_up`）を `parallel_decide` へ移す | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `plugins/ndf/scripts/lib/parallel_observe.py` | 変更 | 問われているレーンの判断を `parallel_decide` へ移す | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `plugins/ndf/scripts/lib/parallel_machine.py` | 変更 | 予約の数え方を `parallel_decide` から受ける | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `plugins/ndf/scripts/parallel.py` | 変更 | 答えの組み立てを `parallel_decide` から受け（RF6a）、提示で上限を検査し直さない（RF6b） | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_answer.py--cycle--lazy-cycle.json` | 削除 | 輪 L3 をほどいたので項目を消す（RF6a） | — |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_run.py--parallel_workflow--durable-boundary.json` | 削除 | 見回りのループを判断のループへ移したので項目を消す（RF6a） | — |
| `plugins/ndf/scripts/tests/test_parallel_decide.py` | 新設 | 並列の計画の判断を確かめる | `plugins/ndf/scripts/lib/parallel_decide.py` |
| `plugins/ndf/scripts/tests/test_parallel_run.py` | 変更 | 判断だけを見るテストを移して消す | `plugins/ndf/scripts/lib/parallel_run.py` |
| `plugins/ndf/scripts/tests/test_parallel_answer.py` | 変更 | 同上 | `plugins/ndf/scripts/lib/parallel_answer.py` |
| `plugins/ndf/scripts/tests/test_parallel_machine.py` | 変更 | 答えた後で起こし直す前のレーンを予約に数える入力を足す | `plugins/ndf/scripts/lib/parallel_machine.py` |
| `plugins/ndf/scripts/lib/parallel_plan_decide.py` | 新設 | 同時に動きうるレーンの最大の負荷と、上限の誤りを決める | 無し |
| `plugins/ndf/scripts/lib/parallel_files.py` | 変更 | 定数 `PARALLEL_DIR` を `parallel_homes` から読む | `plugins/ndf/scripts/lib/parallel_homes.py` |
| `plugins/ndf/scripts/lib/parallel_homes.py` | 変更 | `PARALLEL_DIR` の持ち主になり、`parallel_files` を読まない | 無し |
| `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | 着手の時点の兆候を直す | `plugins/ndf/scripts/lib/parallel_external.py` |
| `plugins/ndf/scripts/lib/parallel_external.py` | 変更 | `request_errors` を `parallel_request_form` から読み、`parallel_lane` を読まない | 無し |
| `plugins/ndf/scripts/lib/parallel_plan.py` | 変更 | 上限の検査を `parallel_plan_decide` から受け、終わったレーンを数えない | `plugins/ndf/scripts/lib/parallel_plan_decide.py` |
| `plugins/ndf/scripts/lib/parallel_lane_cli.py` | 変更 | レーンの追加の検査へ、終わったレーンの名前を渡す | `plugins/ndf/scripts/lib/parallel_plan.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_files.py--cycle--lazy-cycle.json` | 削除 | 輪 L7 をほどいたので項目を消す（RF6b） | — |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_external.py--cycle--lazy-cycle.json` | 削除 | 輪 L9 をほどいたので項目を消す（RF6b） | — |
| `plugins/ndf/scripts/tests/test_parallel_plan_decide.py` | 新設 | 最大の負荷と上限の誤りを確かめる | `plugins/ndf/scripts/lib/parallel_plan_decide.py` |
| `plugins/ndf/scripts/tests/test_parallel_plan.py` | 変更 | 判断だけを見るテストを移して消す | `plugins/ndf/scripts/lib/parallel_plan.py` |
| `plugins/ndf/scripts/tests/test_parallel_lane.py` | 変更 | 同上 | `plugins/ndf/scripts/lib/parallel_lane.py` |
| `plugins/ndf/scripts/tests/test_parallel_external.py` | 変更 | 同上 | `plugins/ndf/scripts/lib/parallel_external.py` |
| `plugins/ndf/scripts/lib/sprint_loop_decide.py` | 新設 | 周の結果と選定の除外の理由を、事実の値から決める | 無し |
| `plugins/ndf/scripts/lib/sprint_loop.py` | 変更 | 周を締めるときにリリースの事実を集めて周の結果の判断へ渡す | `plugins/ndf/scripts/lib/sprint_loop_decide.py` |
| `plugins/ndf/scripts/lib/sprint_loop_select.py` | 変更 | `sprint_loop` を読まずに、設計承認ゲート の答えを当てる関数を引数で受ける | `plugins/ndf/scripts/lib/sprint_loop_exclusion.py` |
| `plugins/ndf/scripts/lib/sprint_loop_record.py` | 変更 | 着手の時点の兆候を直す。周の結果の値の定義は変えない | 無し |
| `plugins/ndf/scripts/lib/sprint_loop_gate.py` | 変更 | 着手の時点の兆候を直す | `plugins/ndf/scripts/lib/sprint_loop_record.py` |
| `plugins/ndf/scripts/lib/sprint_loop_gate2.py` | 変更 | 保留した周の結果を周の結果の判断から受ける | `plugins/ndf/scripts/lib/sprint_loop_decide.py` |
| `plugins/ndf/scripts/lib/sprint_loop_exclusion.py` | 変更 | 除外の事実を集めて、理由を `sprint_loop_decide` から受ける | `plugins/ndf/scripts/lib/sprint_loop_decide.py` |
| `plugins/ndf/scripts/lib/sprint_loop_cli.py` | 変更 | 着手の時点の兆候を直す | `plugins/ndf/scripts/lib/sprint_loop.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__sprint_loop.py--cycle--lazy-cycle.json` | 削除 | 輪 L10 をほどいたので項目を消す（RF7） | — |
| `plugins/ndf/scripts/tests/test_sprint_loop_decide.py` | 新設 | 周の結果と除外の理由を確かめる | `plugins/ndf/scripts/lib/sprint_loop_decide.py` |
| `plugins/ndf/scripts/tests/test_sprint_loop.py` | 変更 | 判断だけを見るテストを移して消す | `plugins/ndf/scripts/lib/sprint_loop.py` |
| `plugins/ndf/scripts/release-steps.py` | 変更 | 引数と副命令の振り分けだけを持つ | `plugins/ndf/scripts/release_lib/stage_present.py` |
| `plugins/ndf/scripts/release_lib/stage_present.py` | 新設 | 提示の段階の副命令（承認資料の事実・版の候補・変更履歴・利用者向けの節）を持つ | `plugins/ndf/scripts/release_lib/included.py` |
| `plugins/ndf/scripts/release_lib/stage_approve.py` | 新設 | 承認の段階の副命令を持つ | `plugins/ndf/scripts/release_lib/approval.py` |
| `plugins/ndf/scripts/release_lib/stage_production.py` | 新設 | 本番の段階の副命令（版の引き上げ・リリース・リリースのブランチ・公開）を持つ | `plugins/ndf/scripts/release_lib/promotion.py` |
| `plugins/ndf/scripts/release_lib/stage_cd.py` | 新設 | CD の結果の段階の副命令（結果の読み取り・案内・記録）を持つ | `plugins/ndf/scripts/release_lib/cd_result.py` |
| `plugins/ndf/scripts/release_lib/stage_verify.py` | 新設 | 宣言の手順を確かめて流す副命令を持つ | `plugins/ndf/scripts/release_lib/declared_steps.py` |
| `plugins/ndf/scripts/release_lib/included.py` | 変更 | リリース対象の変化を、リリース対象のファイルの差分からも決める | 無し |
| `plugins/ndf/scripts/release_lib/instructions.py` | 変更 | 指示書の変更に `.ndf/` の下を含める | 無し |
| `plugins/ndf/scripts/release_lib/materials.py` | 変更 | 着手の時点の兆候を直す | `plugins/ndf/scripts/release_lib/included.py` |
| `scripts/script-structure-allow/plugins__ndf__scripts__release-steps.py--lines.json` | 削除 | `release-steps.py` が 1,000 行以下になったので項目を消す（RF8） | — |
| `plugins/ndf/scripts/tests/test_release_stages.py` | 新設 | 段階ごとの判断と、エントリポイントの形（I12）を確かめる | `plugins/ndf/scripts/release_lib/stage_present.py` |
| `plugins/ndf/scripts/tests/test_release_steps.py` | 変更 | 段階の判断だけを見るテストを移して消す | `plugins/ndf/scripts/release-steps.py` |
| `plugins/ndf/scripts/tests/test_release_included.py` | 変更 | リリース対象のファイルを変えた PR に「利用者向けの変化なし」を付けない入力を足す | `plugins/ndf/scripts/release_lib/included.py` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。置き場だけを変える関数（シグネチャと責務を変えないもの）は、この表に「移す」として名前だけを置き、「関数の仕様」には書かない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/decide_loop.py` | モジュール | `Next`（新設。`step`・`finish`）・`run`（新設）・`DecideLoopError`（新設） |
| `plugins/ndf/scripts/lib/durable.py`・`post_queue.py`・2 本の `drive.py` | モジュール | A0 の兆候の一覧に載る関数（着手の時点で決める。シグネチャは変えない） |
| `scripts/check-script-structure.py` | モジュール | `MAX_LINES`（変更）・`_check_lines`（変更）・`import_edges`（新設）・`strong_components`（新設。`test_import_cycles.py` の輪の判定を移す）・`lazy_cycles`（新設）・`decide_impure`（新設）・`_no_loops_reached`（新設）・`durable_boundary`（変更）・`DURABLE_BOUNDARY`（変更）・`py_body_key`（変更）・`_scan_file`・`py_functions`・`wrapped_parts`・`hook_deps`・`_dep_groups`・`_DepScan.tree`・`_entries`（構文木を `parsed` から受ける） |
| `scripts/lib/parsed.py` | モジュール | `text`・`tree`・`clear`（新設） |
| `scripts/lib/py_imports.py` | モジュール | `imported`（削除）・`imported_tree`（変えない） |
| `scripts/structure_checks/limits_rules.py` | モジュール | `violations`（変更）・`limit_literals`・`sleep_loops`（構文木を受ける） |
| `scripts/check-ndf-contracts.py`・`contracts_lib/core_checks.py`・`contracts_lib/extract_py.py` | モジュール | `is_entry`・`py_core_entry`・`exit_const_violations`・`result_read_violations`・`PyExtractor.module`（構文木を `parsed` から受ける） |
| `scripts/contracts_lib/limits_decl.py` | モジュール | `references`（変更） |
| `plugins/ndf/scripts/supervise_lib/step_decide.py` | モジュール | `StepState`（新設）・`start_state`・`decide`（新設） |
| `plugins/ndf/scripts/supervise_lib/queue_decide.py` | モジュール | `blocked_reason`（新設、RF4）・`resume_prelude`（新設、RF5a）・`RESUME_ALWAYS`（新設、RF5a）・`overlap_of`・`overlaps`・`groups`・`needs`・`contains`・`touches`・`shared_hits`（`admission.py` から移す、RF5a） |
| `plugins/ndf/scripts/supervise_lib/flow.py` | モジュール | `_plan_steps`（変更、RF4）・`prefix_of`（変更、RF5a）・`cmd_queue`（`queue.py` から移す、RF5a）・`run_engine`（変えない） |
| `plugins/ndf/scripts/supervise_lib/queue.py` | モジュール | `blocked`（変更、RF4）・`cmd_queue`（`flow.py` へ移す、RF5a） |
| `plugins/ndf/scripts/supervise_lib/engine.py` | クラス `Engine` | `run`（削除、RF5a） |
| `plugins/ndf/scripts/supervise_lib/plan_decide.py` | モジュール | `state_dir_for`（新設） |
| `plugins/ndf/scripts/supervise_lib/commands.py` | モジュール | `cmd_run`（変更） |
| `plugins/ndf/scripts/supervise_lib/queue_resume.py` | モジュール | `ResumeTarget`（変更。`prelude` を足す）・`resume_targets`（変更） |
| `plugins/ndf/scripts/supervise_lib/admission.py` | モジュール | `tags`（変えない）。重なりの関数は `queue_decide` へ移す |
| `plugins/ndf/scripts/supervise.py` | モジュール | キューの副命令の呼び先（`flow.cmd_queue`） |
| `plugins/ndf/scripts/supervise_lib/sprint_decide.py` | モジュール | `record_pr_of`・`verify_needed`（新設）・`has_record`・`closing_pace`・`reject_resume`（`sprint_routes.py` から移す）・`prod_version`・`next_text`（`sprint.py` から移す） |
| `plugins/ndf/scripts/supervise_lib/sprint.py`・`sprint_lane_close.py` | モジュール | `close_plan`・閉じの引数の読み取り（番号を `record_pr_of` から受ける） |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | モジュール | `channel_waves`（導入の確認の要否を `verify_needed` から受ける） |
| `plugins/ndf/scripts/lib/parallel_decide.py` | モジュール | `TickState`（新設）・`next_tick`・`answer_payload`・`reserved_lanes`・`stall_start`・`range_answers`・`settled_questions`（新設）・`answer_arg_error`（`parallel_answer.py` から移す）・`came_up`（`parallel_launch.py` から移す）・`split_closed`・`closed_reason`（`parallel_route.py` から移す） |
| `plugins/ndf/scripts/lib/parallel_run.py` | モジュール・クラス `_Run` | `parallel_workflow`（変更）・`_ask_stalls`（変更）・`answer_from_ranges`・`_answer_settled`（判断を `parallel_decide` から受ける） |
| `plugins/ndf/scripts/lib/parallel_answer.py`・`parallel_route.py`・`parallel_reply.py` | モジュール | `lane_repo`・`repo_lane`（`parallel_answer.py` から `parallel_route.py` へ移す）・`owner_lanes`・`_restart_lines`（import 先を替える）・`step_prior_answer`（import 先を替える） |
| `plugins/ndf/scripts/lib/parallel_machine.py` | モジュール | `running_total`（変更） |
| `plugins/ndf/scripts/parallel.py` | モジュール | `cmd_answer`（変更、RF6a）・`cmd_present`（変更、RF6b） |
| `plugins/ndf/scripts/lib/parallel_plan_decide.py` | モジュール | `LaneLoad`（新設）・`peak_load`・`limit_errors`（新設） |
| `plugins/ndf/scripts/lib/parallel_plan.py` | クラス `ParallelPlan` | `_check_limits`（変更） |
| `plugins/ndf/scripts/lib/parallel_lane_cli.py` | モジュール | `cmd_lane`（変更） |
| `plugins/ndf/scripts/lib/parallel_homes.py`・`parallel_files.py`・`parallel_external.py` | モジュール | `PARALLEL_DIR`（`parallel_files.py` から `parallel_homes.py` へ移す）・`_request_errors`（import 先を替える） |
| `plugins/ndf/scripts/lib/sprint_loop_decide.py` | モジュール | `LapFacts`・`ExclusionFacts`（新設）・`lap_outcome`・`exclusion`（新設） |
| `plugins/ndf/scripts/lib/sprint_loop.py`・`sprint_loop_gate2.py`・`sprint_loop_exclusion.py`・`sprint_loop_select.py` | モジュール | `cmd_lap`・`finish_held`・`exclusion_reason`・`boundary_then_select`（変更） |
| `plugins/ndf/scripts/release-steps.py` | モジュール | `main`・`build_parser`・`cmd_*` の 14 個だけを残す。ほかの 48 の関数は段階のモジュールへ移す（「処理の手順」の振り分けの表） |
| `plugins/ndf/scripts/release_lib/stage_present.py` | モジュール | `notes_block`（新設）・`write_notes`（変更。`release-steps.py` から移す） |
| `plugins/ndf/scripts/release_lib/included.py` | モジュール | `distributed_change`（新設）・`no_change`（変更） |
| `plugins/ndf/scripts/release_lib/instructions.py` | モジュール | `files`（変更） |

## 関数の仕様

型の書き方: `Optional[X]` は X か None、`Next` は `decide_loop.Next` を指す。

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `decide_loop.run` | `plugins/ndf/scripts/lib/decide_loop.py` | 新設 | `run(decide: Callable[[S, Optional[D]], Next], perform: Callable[[A], D], state: S) -> S` | 判断の関数と手を実行する関数を交互に呼び、終わりの手の状態を返す | `decide` は同じ状態と結果に同じ次の手を返す | 戻り値は `decide` が終わりの手に載せた状態。`perform` は終わりの手の後に呼ばれない | 無し |
| `decide_loop.Next.step` | `plugins/ndf/scripts/lib/decide_loop.py` | 新設 | `Next.step(act: A, state: S) -> Next` | 実行する手と次の状態を持つ次の手を作る | 無し | `finished` が偽で `act` を持つ | 無し |
| `decide_loop.Next.finish` | `plugins/ndf/scripts/lib/decide_loop.py` | 新設 | `Next.finish(state: S) -> Next` | 終わりの手を作る | 無し | `finished` が真で `act` を持たない | 無し |
| `step_decide.start_state` | `plugins/ndf/scripts/supervise_lib/step_decide.py` | 新設 | `start_state(start: Optional[str], order: Sequence[str], limit: int, prelude: Sequence[str] = ()) -> StepState` | プランの遷移の初めの状態を作る | `order` は空でない | `start` が無ければ先頭のステップ、`prelude` があれば前置きを先に流す状態になる | 無し |
| `step_decide.decide` | `plugins/ndf/scripts/supervise_lib/step_decide.py` | 新設 | `decide(steps: Mapping[str, dict], state: StepState, done: Optional[Mapping]) -> Next` | 前のステップの結果から、次に打つステップか終わりを決める | `done` は前に返した手の実行の結果（`run_step` の戻り値の形）か None | 終わりの手は結果と理由を持つ。数えた回数が上限を超えた・知らないステップ・次が無いのどれかで必ず終わる | 無し |
| `flow._plan_steps` | `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | `_plan_steps(eng: Engine, prefix: str, start: Optional[str], resources: dict, began: float, prelude: Sequence[str] = ()) -> dict` | 遷移の判断と耐久ステップの実行を判断のループでつなぐ | 耐久ワークフローの中で呼ぶ | 耐久ステップの名前と呼ぶ順は着手前と同じ（I13）。戻り値の鍵は着手前と同じ | 耐久ステップ（`prepare`・`run_step`・`slot_workflow`・`report`）の実行とその記録 |
| `queue_decide.blocked_reason` | `plugins/ndf/scripts/supervise_lib/queue_decide.py` | 新設 | `blocked_reason(plan: str, deps: Sequence[str], queued: Sequence[str], results: Mapping[str, Optional[str]]) -> Optional[dict]` | 前提のプランの結果から、流さない理由を決める | `results` は前提の名前から結果（見つからなければ None）への対応 | 前提がすべて完了なら None。それ以外は `blocked_by` と理由の文（後ろにある・見つからない・完了でない）を持つ | 無し |
| `queue.blocked` | `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | `blocked(plan: str, deps: list[str], items: list[dict], queued: list[str]) -> Optional[dict]` | 前提の結果を読み、判定を `blocked_reason` へ渡す | 無し | 戻り値は着手前と同じ形 | 前提のプランの結果ファイルを読む |
| `queue_decide.resume_prelude` | `plugins/ndf/scripts/supervise_lib/queue_decide.py` | 新設 | `resume_prelude(order: Sequence[str], start: str, always: Collection[str]) -> tuple[str, ...]` | 再開の開始点より前にある、毎回通すステップを並びのまま返す | `start` は `order` にある | 戻り値は `order` の順で、`start` より前にあり `always` に入るステップだけを持つ | 無し |
| `queue_resume.resume_targets` | `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 変更 | `resume_targets(done: Path, overrides: Optional[dict] = None) -> list[ResumeTarget]` | 再開するプランごとに開始点と前置きを決める | 無し | `--from` で指したステップより前に `RESUME_ALWAYS` のステップがあれば、`prelude` に載る | キューの一覧と完了の記録・プランのファイルを読む |
| `flow.prefix_of` | `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | `prefix_of(identity: str, body: str, start: Optional[str], head: Optional[str]) -> str` | 実行の鍵に作業場所の head を含める | 無し | head が違えば鍵が違う。head が None なら着手前と同じ鍵 | 無し |
| `plan_decide.state_dir_for` | `plugins/ndf/scripts/supervise_lib/plan_decide.py` | 新設 | `state_dir_for(plan_path: str, state_dir: Optional[str], cwd: str) -> str` | 差し込み `{state_dir}` に使う置き場を絶対のパスで決める | `cwd` は絶対のパス | 戻り値は絶対のパス。`state_dir` があればそれを `cwd` から解き、無ければプランのパスから導く | 無し |
| `commands.cmd_run` | `plugins/ndf/scripts/supervise_lib/commands.py` | 変更 | `cmd_run(plan_path: str, state_dir: Optional[str], slow: bool, start: Optional[str]) -> int` | プランを `flow.run_engine` で流し、置き場を `state_dir_for` から受ける | 無し | 終了コードは着手前と同じ対応 | 状態の置き場を作り、耐久ワークフローを起こす |
| `sprint_decide.record_pr_of` | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` | 新設 | `record_pr_of(given: Optional[int], fallback: str) -> str` | 閉じのプランに渡すリリース記録の番号を決める | 無し | `given` が None のときだけ `fallback`。0 は `"0"`（リリース記録なし） | 無し |
| `sprint_decide.verify_needed` | `plugins/ndf/scripts/supervise_lib/sprint_decide.py` | 新設 | `verify_needed(pace: str, mvv: bool, promote: bool, merge: bool) -> bool` | 閉じのプランに導入の確認の段を置くかを決める | `pace` は進め方の値 | 進め方が `fast`・`auto` で、MVV の判定があり、リリースかマージの経路があるときだけ真 | 無し |
| `parallel_decide.next_tick` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `next_tick(state: TickState, done: Optional[object]) -> Next` | 見回りの手順の次の 1 つか、計画の終わりを決める | `done` は前の手順の戻り値か None | 手順が値を返したら終わる。止める依頼の後は止めた後も打つ手順だけを返す。手順の並びの最後の次は先頭へ戻る | 無し |
| `parallel_run.parallel_workflow` | `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | `parallel_workflow(main: str, name: str) -> dict` | 前段の後、見回りを `next_tick` と判断のループで回す | 耐久ワークフローとして起こす | 耐久ワークフローの名前・耐久ステップの名前と呼ぶ順は着手前と同じ（I13） | 耐久ステップ・耐久のイベント・計画の状態の保存 |
| `parallel_decide.answer_payload` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `answer_payload(choices: Sequence[str], note: Optional[str], revise: bool, approved: bool, by: str) -> dict` | 答えの台帳の 1 行を組む | `answer_arg_error` が None を返した引数 | 選択肢と指摘の両方があれば、両方を持つ | 無し |
| `parallel.cmd_answer` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_answer(a) -> None` | 答えを `answer_payload` で組んで台帳へ書く | 無し | `--choice` と `--note` を一緒に渡すと、指摘の本文が答えに載る | 答えの台帳へ書き、耐久のイベントを送る |
| `parallel_decide.reserved_lanes` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `reserved_lanes(pending: Collection[str], answered: Mapping[str, str], restarted: Collection[str]) -> set[str]` | マシン全体の上限の予約に数えるレーンを決める | `answered` はレーンから答えた時刻への対応 | 答えた後で起こし直しを観測していないレーンを予約に含める | 無し |
| `parallel_machine.running_total` | `plugins/ndf/scripts/lib/parallel_machine.py` | 変更 | `running_total(main: Path, rows: dict, own: Optional[str] = None, status=None) -> dict` | 動いているレーンと予約の数を `reserved_lanes` で数える | 無し | 答えた後で起こし直す前のレーンが数に入る | 答えの台帳を読む |
| `parallel_decide.stall_start` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `stall_start(last_seen: datetime, relaunched: Optional[datetime]) -> datetime` | 動いていない時間を数える起点を決める | 2 つの時刻は同じ時間帯 | 起こし直しの時刻が後なら、そちらを起点にする | 無し |
| `parallel_run._ask_stalls` | `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | `_ask_stalls(run: _Run, rows: dict, now: datetime) -> None` | 止まった時間を `stall_start` の起点から数えて問いを立てる | 無し | 起こし直した後の時間だけで問いの分数が決まる | 問いを立て、耐久のイベントを送る |
| `parallel_decide.range_answers` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `range_answers(ranges: Sequence[tuple[int, int]], questions: Mapping[str, dict]) -> dict[str, dict]` | 範囲で渡された答えを、問いごとの答えへ広げる | 無し | 範囲に入らない問いは戻り値に無い | 無し |
| `parallel_decide.settled_questions` | `plugins/ndf/scripts/lib/parallel_decide.py` | 新設 | `settled_questions(questions: Mapping[str, dict], answers: Mapping[str, dict]) -> list[str]` | 答えが出そろった問いの id を並べる | 無し | 戻り値は `questions` の並びの順 | 無し |
| `parallel_plan_decide.peak_load` | `plugins/ndf/scripts/lib/parallel_plan_decide.py` | 新設 | `peak_load(lanes: Sequence[LaneLoad]) -> tuple[int, int]` | 同時に動きうるレーンの組のうち、レーンの数とプランの本数が最大のものを返す | レーンの `after` は同じ計画のレーンの名前 | 終わったレーンは数えない。`after` でつながる 2 本は同じ組に入らない | 無し |
| `parallel_plan_decide.limit_errors` | `plugins/ndf/scripts/lib/parallel_plan_decide.py` | 新設 | `limit_errors(lanes: Sequence[LaneLoad], max_named: int, max_plans: int) -> list[dict]` | 最大の負荷を上限と比べ、誤りを規則 `I2` の形で返す | 無し | 誤りの形（`level`・`rule`・`at`・`message`）は着手前と同じ | 無し |
| `ParallelPlan._check_limits` | `plugins/ndf/scripts/lib/parallel_plan.py` | 変更 | `_check_limits(self, ctx: dict) -> None` | 上限の誤りを `limit_errors` から受けて積む | `ctx` は終わったレーンの名前（`ended`）を持てる | `after` で直列のレーンと終わったレーンを足し合わせない | 無し |
| `parallel.cmd_present` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_present(a) -> None` | 提示で計画を上限なしの読み方で読む | 無し | 動いている途中で上限を超えた計画でも提示が止まらない | 計画と台帳を読み、提示の記録を書く |
| `parallel_lane_cli.cmd_lane` | `plugins/ndf/scripts/lib/parallel_lane_cli.py` | 変更 | `cmd_lane(a) -> None` | レーンの追加の検査へ終わったレーンの名前を渡す | 無し | 終わって止まったレーンが上限の数に入らない | 計画のファイルを書き換える |
| `sprint_loop_decide.lap_outcome` | `plugins/ndf/scripts/lib/sprint_loop_decide.py` | 新設 | `lap_outcome(facts: LapFacts) -> tuple[str, str]` | 周の結果と理由を、リリースの事実と手順の結果から決める | 無し | その周で版をリリースしていれば、手順の失敗があっても完了（理由に失敗の鍵を添える） | 無し |
| `sprint_loop.cmd_lap` | `plugins/ndf/scripts/lib/sprint_loop.py` | 変更 | `cmd_lap(env, name, failed: bool, done: Optional[str], deferred_issues: list[int], update=None) -> dict` | 周の事実を集めて `lap_outcome` へ渡し、周を締める | 無し | 記録の結果がリリースの事実と食い違わない | 完了の記録・スプリントの状態・版の記録を読み、`loop.json` を書く |
| `sprint_loop_gate2.finish_held` | `plugins/ndf/scripts/lib/sprint_loop_gate2.py` | 変更 | `finish_held(env, rec, before, lap, items, state) -> dict` | 保留した周の結果を `lap_outcome` から受ける | 無し | 結果の決め方が `cmd_lap` と同じ関数になる | `loop.json` を書く |
| `sprint_loop_decide.exclusion` | `plugins/ndf/scripts/lib/sprint_loop_decide.py` | 新設 | `exclusion(row: Mapping, facts: ExclusionFacts) -> Optional[str]` | 選定から外す理由を優先の順で 1 つ返す | 無し | 直して出し直すと答えた課題は、棚卸しの判断待ちより先に選定へ残る | 無し |
| `sprint_loop_exclusion.exclusion_reason` | `plugins/ndf/scripts/lib/sprint_loop_exclusion.py` | 変更 | `exclusion_reason(env: Env, rec: dict, row: dict, lap_n: int, lap_created: set, prior=()) -> Optional[str]` | 除外の事実を集め、理由を `exclusion` から受ける | 無し | 戻り値の文は着手前と同じ | マージ待ちの確かめで GitHub を読む |
| `sprint_loop_select.boundary_then_select` | `plugins/ndf/scripts/lib/sprint_loop_select.py` | 変更 | `boundary_then_select(env, rec, before, n, apply_gate1: Callable) -> dict` | 設計承認ゲート の答えを当てる関数を引数で受けて選定する | 呼ぶ側（`sprint_loop`）が `apply_gate1_answers` を渡す | `sprint_loop` を import しない | 記録を読み、選定を書く |
| `stage_present.notes_block` | `plugins/ndf/scripts/release_lib/stage_present.py` | 新設 | `notes_block(current: Sequence[str], bullets: Sequence[str], migration: Sequence[str]) -> list[str]` | 版の節の新しい本文を、今ある下位の節を残して組む | `current` は版の見出しの次の行から次の版の見出しの前まで | 箇条は差し替え、`###` で始まる下位の節（`### 移行` など）は残す | 無し |
| `stage_present.write_notes` | `plugins/ndf/scripts/release_lib/stage_present.py` | 変更 | `write_notes(root: Path, version: str, plugin: str, bullets: list[str], migration=()) -> dict` | 変更履歴の版の節を `notes_block` で書き直す | 無し | 版の節の `### 移行` が消えない | `CHANGELOG.md` と README の更新の節を書く |
| `included.distributed_change` | `plugins/ndf/scripts/release_lib/included.py` | 新設 | `distributed_change(files: Sequence[str], roots: Sequence[str]) -> bool` | PR がリリース対象のファイルを変えたかを決める | `roots` はリリース対象の置き場 | どれかのファイルが置き場の下にあれば真 | 無し |
| `included.no_change` | `plugins/ndf/scripts/release_lib/included.py` | 変更 | `no_change(d: dict, files: Sequence[str], roots: Sequence[str]) -> bool` | 「利用者向けの変化なし」を本文とリリース対象のファイルの両方から決める | 無し | 本文が「無し」でも、リリース対象のファイルを変えていれば偽 | 無し |
| `instructions.files` | `plugins/ndf/scripts/release_lib/instructions.py` | 変更 | `files(root: Path) -> list[str]` | 指示書の変更として見るパスに `.ndf/` を含める | 無し | 宣言の有無に依らず `.ndf/` を含む | 指示書の宣言を読む |
| `check-script-structure.import_edges` | `scripts/check-script-structure.py` | 新設 | `import_edges(root: Path, rel: str, tree: ast.Module) -> set[str]` | 1 つのファイルが import する同じ木のファイルを返す | `tree` は `rel` の構文木 | 最上位・関数の中の import と、`importlib.import_module` の文字列の定数を含み、`if TYPE_CHECKING:` の中を含まない | 無し |
| `check-script-structure.strong_components` | `scripts/check-script-structure.py` | 新設 | `strong_components(edges: Mapping[str, set[str]]) -> list[list[str]]` | import の辺から 2 つ以上のファイルの輪を返す | 無し | 各輪は辞書順に並ぶ。輪どうしは辞書順の先頭で並ぶ | 無し |
| `check-script-structure.lazy_cycles` | `scripts/check-script-structure.py` | 新設 | `lazy_cycles(root: Path, files: Sequence[str]) -> list[dict]` | 輪ごとに違反を 1 つ返す | `files` は検査の対象の Python のファイル | 違反は `path`（輪の辞書順の先頭）・`function`（`cycle`）・`kind`（`lazy-cycle`）・輪のファイルの一覧を持つ | ファイルを読む（`parsed` 経由） |
| `check-script-structure.decide_impure` | `scripts/check-script-structure.py` | 新設 | `decide_impure(rel: str, tree: ast.Module) -> list[dict]` | 判断のモジュールが読んではならない部品と呼び出しを違反にする | `rel` の名前が `_decide.py` で終わる | 違反は `kind` が `decide-pure` で、読んだ部品の名前を持つ | 無し |
| `check-script-structure._no_loops_reached` | `scripts/check-script-structure.py` | 新設 | `_no_loops_reached(tree: ast.Module, entry: str) -> list[tuple[str, str]]` | 耐久ワークフローの関数と、それが同じモジュールで直に呼ぶ関数の `for`・`while` を集める | `entry` は `tree` にある関数の名前 | 戻り値は（関数の名前・理由）の組。耐久ステップの印の付いた関数は数えない | 無し |
| `check-script-structure.durable_boundary` | `scripts/check-script-structure.py` | 変更 | `durable_boundary(root: Path) -> list[dict]` | 置き場の一覧の規則を当て、2 つの耐久の層には `_no_loops_reached` を当てる | 無し | 違反の `function` はループを持つ関数の名前（`_plan_steps`・`parallel_workflow`） | ファイルを読む（`parsed` 経由） |
| `parsed.text` | `scripts/lib/parsed.py` | 新設 | `text(root: Path, rel: str) -> str` | ファイルの本文を 1 回だけ読んで返す | `rel` は `root` からの相対のパス | 同じ引数の 2 回目以降は読まずに同じ文字列を返す | ファイルを読む |
| `parsed.tree` | `scripts/lib/parsed.py` | 新設 | `tree(root: Path, rel: str) -> Optional[ast.Module]` | ファイルの構文木を 1 回だけ作って返す | 同上 | 構文の誤りは None。同じ引数の 2 回目以降は同じ木を返す。呼び手は木を書き換えない | ファイルを読む |
| `parsed.clear` | `scripts/lib/parsed.py` | 新設 | `clear() -> None` | 覚えた本文と構文木を捨てる | テストと、木を書き替えた後だけが呼ぶ | 次の `text`・`tree` は読み直す | 無し |
| `check-script-structure.py_body_key` | `scripts/check-script-structure.py` | 変更 | `py_body_key(fn: ast.FunctionDef) -> str` | コピーを作らずに、名前・docstring・型注釈を除いた本体の鍵を作る | 無し | 鍵は着手前の `copy.deepcopy` の版と同じ値。`fn` を書き換えない | 無し |
| `limits_rules.violations` | `scripts/structure_checks/limits_rules.py` | 変更 | `violations(rel: str, text: str, tree: Optional[ast.Module]) -> list[dict]` | 上限の数値と待ちのループの規則を、受けた構文木で判定する | 無し | 違反の一覧は着手前と同じ | 無し |
| `limits_decl.references` | `scripts/contracts_lib/limits_decl.py` | 変更 | `references(root: Path, files: list[str]) -> dict[str, list[str]]` | 上限の宣言の参照を、1 回の実行で同じ引数につき 1 回だけ集める | 無し | 違反の側と描画の側が同じ結果を受ける | ファイルを読む（`parsed` 経由） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `decide_loop.run` | 判断の関数が次の手でない値を返す・終わりでないのに手を持たない | 判断の関数の誤り | `run` の中で、`decide` の戻り値を受けた直後 | 止める（`DecideLoopError` を上げる） | 呼び手の誤りとして耐久ワークフローが失敗し、supervise は終了コード 2 | 判断の関数を直して打ち直す。耐久の記録はそこまでの耐久ステップを持ち、直した後は続きから流れる |
| `decide_loop.run` | 手の実行が例外を上げる | 耐久ステップか、テストの偽の関数の失敗 | `perform` の呼び出し | 呼び出し元へ返す（包まずにそのまま上げる） | 着手前と同じ（耐久ステップの失敗の扱いは `durable` が持つ） | 着手前と同じ |
| `flow._plan_steps` | 遷移の判断が知らないステップ・上限を超えたで終わる | プランの誤り・遷移の繰り返し | `step_decide.decide` | 止める（結果 `止まった` と理由を返す） | 「知らないステップ: <id>」「ステップの数が上限 <n> を超えた」。終了コードは着手前と同じ | プランを直して `--from` で打ち直す |
| `flow._plan_steps` | 再開の開始点が `end` | リリースの依頼の答えの後に `--from <プラン>=end` で続けた（#3086） | `step_decide.start_state` | 既定値で続ける（前置きが無ければ、ステップを流さずに結果 `完了` で終える） | 「完了」。キューは次のプランへ進む | 何度打っても同じ結果 |
| `queue.blocked` | 前提のプランの結果ファイルが読めない | ファイルが壊れている・消えた | `queue_fill.results_named` | 既定値で続ける（見つからないとして `blocked_reason` へ渡す） | 「前提のプラン <名前> が見つからない」で、そのプランを流さない | 前提のプランを流し直してからキューを打ち直す |
| `queue_resume.resume_targets` | `--from` が指すステップがプランに無い | 打ち間違い | `queue_decide.resume_prelude` の事前条件の確かめ | 止める | 「<プラン> に <ステップ> は無い」。終了コード 2 | 正しいステップで打ち直す |
| `commands.cmd_run` | 状態の置き場を作れない | 権限・容量 | `RunState.__init__` | 止める | OS の誤りを 1 行で出し、終了コード 2 | 原因を除いて打ち直す |
| `parallel_run.parallel_workflow` | 見回りの手順が例外を上げる | 耐久ステップの失敗 | 判断のループの `perform` | 呼び出し元へ返す（着手前と同じ） | 着手前と同じ | 耐久の記録から続きを流す |
| `parallel.cmd_answer` | 引数の組が誤り | `--revise` に `--note` が無い・`--by mvv` に `--approved` が無い | `parallel_decide.answer_arg_error` | 止める | 誤りの文を 1 行で出し、終了コード 2 | 引数を直して打ち直す |
| `parallel.cmd_answer` | 指摘ファイルが読めない | パスの誤り | `--note` のファイルを読む所 | 止める | 「<パス> を読めない」。終了コード 2 | パスを直して打ち直す。台帳は書かない |
| `parallel_machine.running_total` | 答えの台帳が読めない | 壊れた行 | 台帳の読み取り | 既定値で続ける（その行を飛ばし、警告の 1 行を `items` に載せる）。飛ばしたレーンは予約に数えない側へ倒れるため、件数を `metrics` に出す | 警告 1 行。終了コードは変えない | 台帳を直す。数え直しは次の見回りで行う |
| `parallel_run._ask_stalls` | 一覧の時刻を読めない | 一覧の欄が欠ける | `clock.parse` | 既定値で続ける（そのレーンの問いをこの見回りでは立てない） | 無し | 次の見回りで読み直す |
| `parallel.cmd_present` | 計画が上限以外の規則で誤る | 計画のファイルの誤り | 上限なしの読み方の検査 | 止める | 着手前と同じ誤りの一覧。終了コード 1 | 計画を直して打ち直す |
| `parallel_lane_cli.cmd_lane` | 終わったレーンを除いても上限を超える | 同時に動く数が多すぎる | `limit_errors` | 止める | 上限の誤りと `cap_hint_items` の 1 行。終了コード 1 | 上限を変えるか `after` を付けて打ち直す |
| `sprint_loop.cmd_lap` | 版の記録が読めない | 記録のファイルが無い・壊れている | リリースの事実の読み取り | 既定値で続ける（リリースの事実を「無し」として `lap_outcome` へ渡す） | 周の理由に「版の記録を読めない」を添える | 記録を直した後、`finish_lap` で締め直せる |
| `sprint_loop_gate2.finish_held` | 同上 | 同上 | 同上 | 同上 | 同上 | 同上 |
| `sprint_loop_exclusion.exclusion_reason` | マージ待ちの確かめで GitHub が答えない | ネットワーク・認証 | `merge_wait_reason` | 呼び出し元へ返す（着手前と同じ） | 着手前と同じ | 着手前と同じ |
| `sprint_loop_select.boundary_then_select` | `apply_gate1` が渡されない | 呼び手の誤り | 引数の型の確かめ | 止める（`TypeError`） | 開発ループが終了コード 2 | 呼び手を直す |
| `stage_present.write_notes` | 版の節が無い | 版の見出しを書く前に呼んだ | `changelog_span` | 止める（着手前と同じ） | 着手前と同じ | 着手前と同じ |
| `instructions.files` | 指示書の宣言が壊れている | `.ndf/instructions.json` の誤り | `Declaration.load` | 止める（着手前と同じ） | 着手前と同じ | 宣言を直して打ち直す |
| `check-script-structure.lazy_cycles` | ファイルに構文の誤りがある | 書きかけのファイル | `parsed.tree` が None | 既定値で続ける（そのファイルの辺を空として数える） | 無し（構文の誤りは別の検査が落とす） | 直して打ち直す |
| `check-script-structure.durable_boundary` | 置き場の一覧のファイルか関数が無い | 耐久の層を消した・名前を変えた | `_find_def` | 違反として返す（着手前と同じ「置き場のファイルが無い」の形） | 違反の 1 行。終了コード 1 | `DURABLE_BOUNDARY` を同じ PR で直す |
| `parsed.text` | ファイルが読めない | 消えた・文字コードの誤り | ファイルの読み取り | 呼び出し元へ返す（`OSError`・`UnicodeDecodeError` を上げる） | 呼び手の検査が着手前と同じ扱いをする | 何度打っても同じ |
| `parsed.tree` | 同上 | 同上 | 同上 | 同上 | 同上 | 同上 |
| `limits_decl.references` | 同上 | 同上 | `parsed.tree` | 同上 | 同上 | 同上 |

## 処理の手順

**遷移の判断（`step_decide.decide`）。** 初めの呼び出し（`done` が None）は `start_state` の状態から始める。`done` を受けたら、結果があれば結果と理由を上書きし、遅れの見張りの再試行かを決めてから、次のステップを `done["nxt"]` にする。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `step_decide.decide` | 前置きが残っていて、前の前置きのステップが結果を持たない | 前置きの次のステップを打つ（数えた回数に入れる） |
| `step_decide.decide` | 前置きのステップが結果（止まった・承認ゲート）を持つ | 終わる。結果と理由はそのステップのもの |
| `step_decide.decide` | 前置きが尽きた | 再開の開始点のステップを打つ |
| `step_decide.decide` | 次のステップが無い（None） | 終わる。結果と理由はそこまでのもの（無ければ 完了・無し） |
| `step_decide.decide` | 前の手と同じステップへ戻り、遅れの見張りの答えが `retry` | 再試行の回数だけを足す（I4b） |
| `step_decide.decide` | 数えた回数が上限を超える | 終わる。止まった・「ステップの数が上限 <n> を超えた」（I4a） |
| `step_decide.decide` | 次のステップがプランに無い | 終わる。止まった・「知らないステップ: <id>」（I4c） |
| `step_decide.decide` | 上のどれでもない | 次のステップを打つ |
| `step_decide.start_state` | 開始点が `end` か `stop`、前置きが無い | 次のステップを None にした状態（最初の `decide` で完了で終わる。#3086） |

**前提の判定（`queue_decide.blocked_reason`）。** 理由は前提ごとに 1 つ作り、`。` でつなぐ。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `queue_decide.blocked_reason` | 前提がキューの中でこのプランより後ろにある | 「前提のプラン <名前> の結果が無い（後ろにある）」 |
| `queue_decide.blocked_reason` | 前提の結果が None | 「前提のプラン <名前> が見つからない」 |
| `queue_decide.blocked_reason` | 前提の結果が `完了` でない（承認ゲートを含む） | 「前提のプランが完了でない: <名前>（<結果>）」 |
| `queue_decide.blocked_reason` | すべての前提が `完了` | None（流す） |

**再開の前置き（`queue_decide.resume_prelude`）。** `RESUME_ALWAYS` のステップ（`base-sync`・`deep-due`）は、前の実行の後にベースブランチや振り返りの範囲が変わりうるため、再開の開始点より前にあれば毎回通す（#3114・#3047）。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `queue_decide.resume_prelude` | 開始点より前に `RESUME_ALWAYS` のステップが無い | 空（着手前と同じ流れ） |
| `queue_decide.resume_prelude` | 開始点より前に `base-sync` がある | `("base-sync",)`。取り込みの後に開始点から流す |
| `queue_decide.resume_prelude` | 開始点が `RESUME_ALWAYS` のステップそのもの | 空（開始点として通る） |

**見回りの手順（`parallel_decide.next_tick`）。** 手順の並びは今の `_TICK` の 7 つ（`parallel_run.py:675`）のまま変えない。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `parallel_decide.next_tick` | 前の手順が値を返した | 終わる。その値が計画の結果 |
| `parallel_decide.next_tick` | 止める依頼を受けた後で、次の手順が止めた後に打たないもの | その手順を飛ばして次へ |
| `parallel_decide.next_tick` | 並びの最後の手順が値を返さなかった | 状態の保存（`run.save()`）を手として返し、並びの先頭へ戻る |
| `parallel_decide.next_tick` | 上のどれでもない | 並びの次の手順を打つ |

**最大の負荷（`parallel_plan_decide.peak_load`）。** 終わったレーンを除き、`after` でつながる（どちらかからどちらかへ `after` をたどれる）2 本を同じ組に入れない組のうち、プランの本数の和が最大のものを返す。レーンの数も同じ組で数える。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `parallel_plan_decide.peak_load` | `after` を持つレーンが無い | 終わっていないレーンの数と本数の和（着手前と同じ） |
| `parallel_plan_decide.peak_load` | A の後に B（`after: [A]`） | A と B の大きい方だけを数える（#3145） |
| `parallel_plan_decide.peak_load` | 終わって止まったレーンがある | そのレーンを数えない（#3021） |

**周の結果（`sprint_loop_decide.lap_outcome`）。** 上の行ほど先に当たる。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `sprint_loop_decide.lap_outcome` | その周で版をリリースした（版の記録かリリースの依頼の答え `released` がある） | 完了。手順の失敗があれば理由に失敗の鍵を添える（I11。#2911 の `close_failure` も同じ） |
| `sprint_loop_decide.lap_outcome` | リリースの依頼の答えが `rejected` | 人へ戻した |
| `sprint_loop_decide.lap_outcome` | 失敗の印・完了でない完了の記録の行・スプリントの状態の失敗の鍵のどれかがある | 失敗 |
| `sprint_loop_decide.lap_outcome` | 上のどれでもない | 完了 |

**選定の除外（`sprint_loop_decide.exclusion`）。** 着手前の 8 つの順のうち、「直して出し直す」と答えた課題（先頭に選ぶ課題）だけを、棚卸しの判断待ちより前で選定へ残す（#2722）。ほかの順は変えない。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `sprint_loop_decide.exclusion` | 前のループの判断待ち | 着手前の文 |
| `sprint_loop_decide.exclusion` | 先頭に選ぶ課題 | None（ここで残す） |
| `sprint_loop_decide.exclusion` | 棚卸しの判断待ち・保留した周の課題・未回答と差し戻しとマージ待ち・同じ周に外した・前提・周の中で起票・容量 | この順で最初に当たったものの着手前の文 |

**副命令の振り分け（RF8。決定 13 が詳細設計へ委ねた表）。** `deploy-facts`・`verify-facts` は `release_lib/deploy.py` が持つため動かさない。

| 段階のモジュール | 副命令 | 移す関数 |
| --- | --- | --- |
| `stage_present` | `approval-facts`・`notes`・`changelog`・`changed-plugins` | `kept_sections`・`included_pr_facts`・`pr_notes`・`replace_lines_under`（`notes_block` へ置き換える）・`write_notes`・`unmerged_items`・`unmerged_note`・`notes_prs`・`issue_body`・`_notes_metrics`・`_emit_approval_notes`・`_update_changelog`・`_add_changelog_section`・`_insert_into_changelog_section`・`release_tag_before`・`next_release`・`other_plugins`・`pr_view`・`pr_many`・`section_prs`・`_pr_fields`・`_within_end`・`pr_titles` |
| `stage_approve` | `approve` | 無し（`approval.approve` を呼ぶ） |
| `stage_production` | `bump`・`release`・`release-branch`・`publish` | `run_staleness`・`base_version`・`_bump_manifests`・`_bump_marketplace_and_readme`・`_bump_ndf_docs`・`plan_bump`・`bump_guard`・`run_checks`・`find_pr`・`create_pr`・`pr_check_buckets`・`wait_and_merge`・`merge_commit_of`・`_reusable_release_pr`・`_release_pr_body`・`_merge_release_pr`・`_merged_sync_pr`・`_merge_sync_pr`・`_merge_prod_pr` |
| `stage_cd` | `cd-result`・`cd-guidance`・`record` | `_published`・`_sweep_candidates`・`_emit_record`・`record_promote`・`record_merged`・`_cd_revision` |
| `stage_verify` | `run`・`check` | 無し（宣言の手順 `declared_steps` を確かめて流す。本番の手順もリリース後テストの手順も同じ部品で流し、どちらを流すかは引数 `--stage` が決める） |

2 つ以上の段階が使う関数は、最初に使う段階に置き、ほかの段階はそこを読む。段階のモジュールどうしの向きは `stage_production → stage_present`・`stage_cd → stage_production` の一方向で、輪を作らない（`lazy-cycle` が確かめる）。

**規則 `decide-pure` が見るもの。** 名前が `_decide.py` で終わるモジュールの import のうち `DECIDE_FORBIDDEN_IMPORTS` に入る部品と、関数の呼び出しのうち `DECIDE_FORBIDDEN_CALLS` に入る名前を違反にする。import は直に読むものだけを見て、たどらない。判断のモジュールが読んでよい部品は、標準ライブラリの計算の部品（`dataclasses`・`typing`・`collections`・`itertools`・`datetime`・`pathlib` の純粋なパスの計算）と、ほかの判断のモジュールと `decide_loop` である。

- 繰り返しの終わる条件: 判断のループは判断の関数が終わりの手を返すまで回る。遷移の判断は数えた回数の上限（プランの `上限`）で必ず終わり、見回りの判断は手順が値を返すか止める依頼で終わる（止める依頼の後の手順は `_handle_stop` が値を返す）
- 計算量の注意: `peak_load` は終わっていないレーンの組を全部たどる（レーン n 本で 2 の n 乗）。上限 `named_limit` を超えるレーンの数のときは全部の和を返して上限の誤りにする。`strong_components` はファイル数と辺の数に比例する（Tarjan の方法）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Next` | 型 | `plugins/ndf/scripts/lib/decide_loop.py` | 凍結した dataclass（`act`・`state`・`finished`） |
| `StepState` | 型 | `plugins/ndf/scripts/supervise_lib/step_decide.py` | 凍結した dataclass（`sid`・`n`・`counted`・`retries`・`retried`・`limit`・`result`・`reason`・`prelude`）。今の `_plan_steps` のローカルの変数を 1 つにまとめたもの |
| 遷移の上限 | 設定値 | `plugins/ndf/scripts/supervise_lib/limits.py` の `DEFAULT_LIMIT` | 変えない。`step_decide` は `start_state` の引数で受け、定数を写さない |
| `RESUME_ALWAYS` | 定数 | `plugins/ndf/scripts/supervise_lib/queue_decide.py` | `("base-sync", "deep-due")`。ステップの定義（`procedures.py`・`sprint_close_steps.py`）の id と一致することを `test_queue_decide.py` が定義から引いて確かめる |
| `ResumeTarget.prelude` | 型 | `plugins/ndf/scripts/supervise_lib/queue_resume.py` | 空のタプル |
| `TickState` | 型 | `plugins/ndf/scripts/lib/parallel_decide.py` | 凍結した dataclass（手順の位置・止める依頼の有無） |
| 見回りの手順の並び `_TICK` | 定数 | `plugins/ndf/scripts/lib/parallel_run.py` | 変えない（手順は耐久ステップを呼ぶため耐久の層に置き、`next_tick` は並びの長さと止めた後に打つかの印だけを受ける） |
| `LaneLoad` | 型 | `plugins/ndf/scripts/lib/parallel_plan_decide.py` | 凍結した dataclass（`name`・`plans`・`after`・`ended`） |
| 上限の値 | 設定値 | `plugins/ndf/scripts/lib/parallel_plan.py` の `max_plans`・`named_limit` | 変えない |
| `PARALLEL_DIR` | 定数 | `plugins/ndf/scripts/lib/parallel_homes.py`（`parallel_files.py` から移す） | `Path(".ndf") / "parallel"` |
| `LapFacts` | 型 | `plugins/ndf/scripts/lib/sprint_loop_decide.py` | 凍結した dataclass（`failed`・`bad_rows`・`failure_key`・`released`・`answer`） |
| `ExclusionFacts` | 型 | `plugins/ndf/scripts/lib/sprint_loop_decide.py` | 凍結した dataclass（`prior`・`pending`・`waiting`・`excluded`・`created`・`first`・`size`） |
| 周の結果の値 | 定数 | `plugins/ndf/scripts/lib/sprint_loop_record.py` の `DONE`・`FAILED`・`RETURNED` | 変えない。`sprint_loop_decide` はここから読む |
| リリース対象の置き場 | 設定値 | `plugins/ndf/scripts/release_lib/` の既存の宣言の読み手（`changed.py`） | 変えない。`distributed_change` は引数で受ける |
| `UNDECLARED_FILES` | 定数 | `plugins/ndf/scripts/release_lib/instructions.py` | `("AGENTS.md", "CLAUDE.md", ".ndf/")` |
| `MAX_LINES` | 定数 | `scripts/check-script-structure.py` | 1000（今は 500） |
| `KINDS` | 定数 | `scripts/check-script-structure.py` | 今の 9 つに `lazy-cycle`・`decide-pure` を足す |
| `DURABLE_BOUNDARY` | 定数 | `scripts/check-script-structure.py` | `supervise_lib/engine.py` の `Engine.run` の行を `supervise_lib/flow.py` の `plan_workflow`（`_no_loops_reached`）に替え、`lib/parallel_run.py` の `parallel_workflow`（`_no_loops_reached`）を足す。ほかの 5 行は変えない |
| `DECIDE_FORBIDDEN_IMPORTS` | 定数 | `scripts/check-script-structure.py` | `durable`・`dbos`・`subprocess`・`shutil`・`tempfile`・`os`・`time`・`random`・`socket`・`urllib`・`http`・`run_core` |
| `DECIDE_FORBIDDEN_CALLS` | 定数 | `scripts/check-script-structure.py` | `open`・`read_text`・`write_text`・`read_bytes`・`write_bytes`・`glob`・`rglob`・`iterdir`・`exists`・`is_file`・`is_dir`・`mkdir`・`unlink`・`resolve`・`stat` |
| 遅延の輪の項目 | 設定値 | `scripts/script-structure-allow/` | RF2 が置く 10 件。L1 `plugins__ndf__scripts__lib__project_lib__mvv_llm.py--cycle--lazy-cycle.json`、L2 `plugins__ndf__scripts__lib__deps.py--cycle--lazy-cycle.json`、L4 `plugins__ndf__scripts__relay_lib__handover.py--cycle--lazy-cycle.json`、L6 `plugins__ndf__scripts__lib__context_calibrate.py--cycle--lazy-cycle.json`、L8 `plugins__ndf__scripts__lib__container_reach.py--cycle--lazy-cycle.json` と、設計文書の項目の表の 5 件（L3・L5・L7・L9・L10）。名前は輪の辞書順の先頭のパスで、L1 の先頭は着手の時点の `strong_components` の結果で決まる |
| `lines` の項目 | 設定値 | `scripts/script-structure-allow/` | 今の 27 件のうち、1,000 行を超える 3 件（`check-trigger.py`・`lib/claude_accounts.py`・`release-steps.py`）だけを残す |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ順番 4 の子 Issue の PR（RF3・RF4・RF5b・RF6a・RF7・RF8・RF1b） | 例外リスト `scripts/script-structure-allow/` | 取らない。各 PR は自分が消す項目のファイルを名前で触り、ディレクトリを触るのは順番 3 の RF2 だけ（流す順序の確かめが重なり 0 件を返す） | 項目のファイルを消すだけなので、base-sync の後も衝突しない |
| 順番 4・5 の子 Issue の PR | 兆候の基準 `scripts/ai-smells-baseline/` | 取らない。後からマージする PR が base-sync の後に `check-ai-smells.py --write-baseline` を打ち直す（#1142 の頭の詳細設計の「並行と排他」） | 今の数で上書きするため冪等 |
| RF4・RF5a のマージと、流れているプランとキューの耐久ワークフロー | 耐久の記録（DBOS のデータベース） | 取らない。耐久ワークフローの名前・耐久ステップの名前と呼ぶ順を変えない（I13・決定 16）ため、更新の後も記録から再実行で続く | 代表のテスト（C8）が耐久ステップの名前の並びを着手前と比べて守る。並びが変わると分かった子 Issue は `FORMAT` を上げ、流す順序を確かめ直す |
| RF6a のマージと、動いている並列の計画の耐久ワークフロー | 耐久の記録と `durable.patched` の印 | 同上。見回りの手順の並び `_TICK` と、手順の中の耐久ステップの呼び方を変えない | 同上 |
| RF5a のマージと、`flow` を関数の中で読んでいた呼び手 | `supervise_lib.queue.cmd_queue` の名前 | 取らない。呼び手（`commands.py`・`supervise.py`・`queue_resume.cmd_queue_resume`）を同じ PR で `flow.cmd_queue` へ替える | `rg -n 'queue\.cmd_queue\|Engine\.run\|\.run\(' plugins/ndf/scripts` の結果が 0 件であることを PR 本文に載せる（C34） |
| RF7 のマージと、保留した周を持つ開発ループ | `loop.json` | 取らない。周の結果の値（完了・失敗・人へ戻した・保留）と記録の形を変えない | 保留した周は、マージの後に `finish_held` が新しい判断で締める。締めた周は書き換えない（`write_record` の I1） |
| 構造の検査・契約の検査と CI の別のジョブ | `parsed` が覚えた構文木 | 取らない。覚えるのは 1 回のプロセスの中だけで、プロセスをまたがない（決定 7） | 実行ごとに読み直す |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `flow._plan_steps` | 止まった理由（知らないステップ・上限）と、前置きで流したステップの id | フェーズレポートの理由と進捗の記録（`progress.jsonl` の step の行。着手前と同じ形） | 判断の関数の中間の状態 |
| `queue_resume.resume_targets` | 「<プラン> を <ステップ> から続ける（--from）。前に <前置き> を流す」 | 標準エラー（着手前の 1 行に前置きを足す） | 無し |
| `parallel_run.parallel_workflow` | 着手前と同じ（見回りの手順が出すもの） | 並列の計画の台帳と耐久のイベント | 判断の関数の中間の状態 |
| `parallel_machine.running_total` | 読めなかった台帳の行の数 | 結果 JSON の `items` の警告と `metrics` | 答えの本文 |
| `sprint_loop.cmd_lap` | 周の結果・理由・リリースした版 | `loop.json` と `laps.md` | 費用の内訳の外のもの（着手前と同じ） |
| `check-script-structure.lazy_cycles` | 輪ごとの違反（先頭のパス・輪のファイルの一覧） | 標準エラーの 1 行と結果 JSON の `items` | 無し |
| `check-script-structure.decide_impure` | 違反のモジュールと読んだ部品の名前 | 同上 | 無し |
| 各子 Issue の PR | 着手前と後の所要（設計文書の「測る手順と縮める対象」）・消した古いテストの一覧（C33）・古い形の参照の `rg` の結果（C34）・割り当てた bug の再現の手順を 1 回通した結果（E8） | PR 本文 | 再現できなかった bug の `Closes`（理由を書いて開いたまま残す） |

## 既存の部品の再利用

検索はリポジトリの根で `rg -n '<語>' plugins/ndf/scripts scripts -g '!**/tests/**'` を打った（2026-11-13）。

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `decide_loop.run` | `durable.wait`・`durable.wait_from`（待ちのループ）、`lib/` の `loop`・`drive` の名前の関数 | `rg -n 'def (run\|loop\|drive)\b'` | 使えない理由: 既存のループは耐久の待ちか、手順の並びを固定したもので、判断の関数と実行の関数を受ける形が無い |
| `decide_loop.Next.step` | 同上 | 同上 | 使えない理由: 次の手の値の型が無い |
| `decide_loop.Next.finish` | 同上 | 同上 | 使えない理由: 同上 |
| `step_decide.start_state` | `flow._plan_steps` の 248 行目の初期化、`limits.run_error` | 読んだ | 使う: 初期化の値をそのまま状態へ移し、`run_error` は変えずに `engine.setup` が呼ぶ |
| `step_decide.decide` | `flow._plan_steps` の 250〜266 行目 | 読んだ | 使う: ループの本体の判断をそのまま移し、実行（`run_step`・`_in_slot`・`eng.replay`）は耐久の層に残す |
| `queue_decide.blocked_reason` | `queue.blocked`（300 行目） | 読んだ | 使う: 理由の文と判定をそのまま移し、読み取り `results_named` は `queue.blocked` に残す |
| `queue_decide.resume_prelude` | `queue_resume.parse_from`・`last_step`・`resume_targets` | `rg -n 'base-sync\|deep-due'` と読んだ | 使えない理由: 開始点より前のステップを通す判断がどこにも無い（#3114・#3047 の原因） |
| `plan_decide.state_dir_for` | `paths.state_dir_of`・`commands.cmd_run` の 363 行目 | `rg -n 'def state_dir_of'` | 使う: `state_dir_of` の導き方を受け、`cwd` から絶対のパスへ解く 1 段だけを足す |
| `sprint_decide.record_pr_of` | `sprint_routes.has_record`・`RECORD_NONE` | `rg -n 'record_pr'` | 使う: `RECORD_NONE`（`"0"`）を同じ定数で返す。`has_record` は同じモジュールへ移す |
| `sprint_decide.verify_needed` | `sprint_routes.closing_pace`・`pace_verify` | 読んだ | 使う: `closing_pace` の進め方の値を受ける。`pace_verify`（宣言を読む）は `sprint_routes` に残す |
| `parallel_decide.next_tick` | `parallel_run._TICK` と 646〜650 行目 | 読んだ | 使う: 並びと止めた後に打つかの印をそのまま受ける |
| `parallel_decide.answer_payload` | `parallel_answer.answer_arg_error`・`parallel.cmd_answer` の 385 行目 | 読んだ | 使えない理由: 答えの行を組む関数が無く、`cmd_answer` の中で組んでいる |
| `parallel_decide.reserved_lanes` | `parallel_machine.running_total`・`_asking`・`pending_questions` | 読んだ | 使えない理由: 答えた後で起こし直す前の状態を数える判断が無い（#3096 の原因） |
| `parallel_decide.stall_start` | `parallel_repo.stall_seconds`・`clock.parse` | `rg -n 'stall'` | 使う: 区切りの秒数は `stall_seconds` のまま。起点を選ぶ判断だけが無い |
| `parallel_decide.range_answers` | `_Run.answer_from_ranges`（447 行目） | 読んだ | 使う: 452・457 行目の繰り返しの判断を移す |
| `parallel_decide.settled_questions` | `_Run._answer_settled`（598 行目） | 読んだ | 使う: 604・608 行目の繰り返しの判断を移す |
| `parallel_plan_decide.peak_load` | `parallel_plan.ready_lanes`（498 行目。`after` を見る唯一の関数） | `rg -n 'after' plugins/ndf/scripts/lib/parallel_plan.py` | 使えない理由: `ready_lanes` は今起動できるレーンを返し、同時に動きうる最大を数えない |
| `parallel_plan_decide.limit_errors` | `ParallelPlan._check_limits`（176 行目） | 読んだ | 使う: 誤りの文と規則の名前を移す |
| `sprint_loop_decide.lap_outcome` | `sprint_loop.cmd_lap` の結果の式・`gate2.finish_held`・`settle_release`・`gate.failure_key` | 読んだ | 使う: 3 か所の式を 1 つにまとめ、`failure_key` は呼び手が事実として渡す |
| `sprint_loop_decide.exclusion` | `sprint_loop_exclusion.exclusion_reason`（33 行目） | 読んだ | 使う: 8 つの判定の順と文を移し、先頭に選ぶ課題の行を足す |
| `stage_present.notes_block` | `release-steps.replace_lines_under`（1035 行目）・`changelog_span` | 読んだ | 使えない理由: `replace_lines_under` は節を丸ごと差し替える（#1755 の原因）。`changelog_span` は使う |
| `included.distributed_change` | `release_lib/changed.py`（リリース対象の変化を版の決定に使う部品） | `rg -n 'def ' plugins/ndf/scripts/release_lib/changed.py` | 使う: リリース対象の置き場の決め方を `changed.py` から受ける |
| `check-script-structure.import_edges` | `py_imports.imported_tree`・`tests/test_import_cycles.py` の `_import_module_name`・`lazy_edges` | 読んだ | 使う: `imported_tree` で import を解き、文字列の定数の `import_module` を拾う部分を `test_import_cycles.py` から移す |
| `check-script-structure.strong_components` | `tests/test_import_cycles.py` の輪の判定、標準ライブラリ `graphlib` | 読んだ。`graphlib.TopologicalSorter` は輪の 1 つを例外で知らせるだけ | 使う: `test_import_cycles.py` の判定を移す。`graphlib` は輪をすべて返さないため使えない |
| `check-script-structure.lazy_cycles` | `allowlist.load_allow`・`check()` の突き合わせ | 読んだ | 使う: 違反を今の形で返し、例外の突き合わせと `unused-allow` は `check()` のまま |
| `check-script-structure.decide_impure` | `wrapped_parts`（部品の import を見る規則） | 読んだ | 使う: import を集める走査は `py_imports.imported_tree` を使い、禁止の一覧だけを足す |
| `check-script-structure._no_loops_reached` | `_no_loops`（688 行目）・`_find_def`（656 行目） | 読んだ | 使う: 対象の関数を `_find_def` で引き、各関数に `_no_loops` を当てる。同じモジュールの呼び先をたどる部分だけを足す |
| `parsed.text` | `_DepScan.tree`（498 行目のメモ）、標準ライブラリ `functools.cache`（LIB6） | `rg -n 'functools\|lru_cache\|@cache' scripts/` は 0 件 | 使う: `functools.cache`。`_DepScan.tree` のメモは `parsed.tree` へ置き換える |
| `parsed.tree` | 同上 | 同上 | 使う: 同上 |
| `parsed.clear` | `functools.cache` の `cache_clear` | 読んだ | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `decide_loop.run` | 3 手で終わる判断の関数と偽の実行の関数で、手の順と終わりの状態が返る。DBOS を import しない | 初めの呼び出しで終わる判断の関数では `perform` を 1 回も呼ばない | 次の手でない値を返すと `DecideLoopError`。`perform` の例外がそのまま上がる |
| `decide_loop.run`（耐久を通す代表、`test_decide_loop_durable.py`） | 耐久ステップを渡したワークフローを途中で止めて再開すると、実行済みの手を打ち直さずに続く | 記録した耐久ステップの名前の並びが、判断のループを使う前の並びと同じ（I13） | 耐久ステップの呼ぶ順を入れ替えると落ちる |
| `step_decide.decide` | 上限の内の経路・遅れの再試行を挟む経路が、着手前の `_plan_steps` と同じ結果と理由を返す | 数えた回数がちょうど上限のとき止まらず、1 つ超えると止まる。遅れの再試行は数えない（I4b） | 知らないステップで止まる（I4c）。前置きのステップが止まると再開の開始点を打たない |
| `step_decide.start_state` | 開始点が無ければ先頭から | 開始点が `end` なら完了で終わる（#3086 の再現） | 無し |
| `flow._plan_steps` | 代表のテスト 1 本（`test_supervise_limits.py` に残す）でプランを最後まで流し、耐久ステップの名前の並びを着手前と比べる | 無し | 判断のループが耐久ステップを呼ばないように壊すと落ちる |
| `queue_decide.blocked_reason` | すべて完了なら None | 承認ゲート（`関門`）は完了でない | 後ろにある・見つからない・完了でないの 3 つの文を返す |
| `queue.blocked` | 結果ファイルの木から `blocked_reason` と同じ判定が出る | 無し | 壊れた結果ファイルを「見つからない」として扱う |
| `queue_decide.resume_prelude` | `--from pr` の実装のプランで `("base-sync",)` を返す（#3114 の再現） | 開始点が `base-sync` そのものなら空。`--from retro` のまとめのプランで `("deep-due",)`（#3047 の再現） | 無し |
| `queue_resume.resume_targets` | 前置きが `ResumeTarget.prelude` に載る | 前置きが無いプランは着手前と同じ | 無いステップを指すと止まる |
| `flow.prefix_of` | head が違えば鍵が違う（#3176 の再現） | head が None なら着手前と同じ鍵 | 無し |
| `plan_decide.state_dir_for` | 相対のプランのパスから絶対のパスを返す（#2949 の再現） | 絶対のパスを渡すとそのまま | 無し |
| `commands.cmd_run` | 相対のパスで渡したプランの `{state_dir}` が絶対のパスで埋まる | 無し | 置き場を作れないと終了コード 2 |
| `sprint_decide.record_pr_of` | 番号をそのまま文字列にする | 0 は `"0"`（#2968 の再現）。None は代わりの値 | 無し |
| `sprint_decide.verify_needed` | `fast` の進め方で真 | `normal` の進め方で偽（#2967 の再現） | 無し |
| `parallel_decide.next_tick` | 7 つの手順を順に返し、最後の後に保存と先頭へ戻る | 止める依頼の後は止めた後も打つ手順だけを返す | 手順が値を返すと終わる |
| `parallel_run.parallel_workflow` | 代表のテスト 1 本で、見回りの耐久ステップの名前の並びが着手前と同じ | 無し | 耐久の層にループを書き戻すと `durable-boundary` で落ちる |
| `parallel_decide.answer_payload` | 選択肢と指摘の両方を持つ行を返す（#3174 の再現） | 選択肢だけ・指摘だけ | 無し |
| `parallel.cmd_answer` | `--choice` と `--note` の組で、台帳の行に指摘が載る | 無し | 誤った組は終了コード 2 で台帳を書かない |
| `parallel_decide.reserved_lanes` | 答えた後で起こし直していないレーンを含める（#3096 の再現） | 起こし直しを観測したレーンは含めない | 無し |
| `parallel_machine.running_total` | 予約を数に含める | 無し | 壊れた台帳の行を飛ばし、警告を載せる |
| `parallel_decide.stall_start` | 起こし直しが後なら、その時刻を返す（#2585 の再現） | 起こし直しが無ければ最後に見た時刻 | 無し |
| `parallel_run._ask_stalls` | 起こし直した後の分数で問いを立てる | 区切りの秒数ちょうどでは問わない | 時刻を読めない行では問わない |
| `parallel_decide.range_answers` | 範囲の中の問いへ答えを広げる | 範囲の端の問いを含む | 範囲の外の問いは含めない |
| `parallel_decide.settled_questions` | 答えのそろった問いを並びの順で返す | 答えが 1 つ欠けた問いは含めない | 無し |
| `parallel_plan_decide.peak_load` | `after` の無いレーンは足し合わせる | `after` で直列の 2 本は大きい方だけ（#3145 の再現）。終わったレーンを数えない（#3021 の再現） | 無し |
| `parallel_plan_decide.limit_errors` | 上限の内なら空 | ちょうど上限なら空 | 超えると `I2` の誤りを返す |
| `ParallelPlan._check_limits` | `ctx` の終わったレーンを除いて検査する | 無し | 上限を超えると誤りが積まれる |
| `parallel.cmd_present` | 上限を超えた動いている計画でも提示が通る（#3064 の再現） | 無し | 上限以外の規則の誤りでは止まる |
| `parallel_lane_cli.cmd_lane` | 終わったレーンを除いてレーンを足せる | 無し | 除いても超えると止まる |
| `sprint_loop_decide.lap_outcome` | 「失敗と記録された周が実際はリリースしていた」事実で完了を返す（C23） | リリースの依頼の答え `rejected` は人へ戻した | 版の記録が無く手順が失敗なら失敗 |
| `sprint_loop.cmd_lap` | 周の結果が `lap_outcome` と一致する | 無し | 版の記録を読めないと理由を添えて締める |
| `sprint_loop_gate2.finish_held` | 保留した周の結果が `lap_outcome` と一致する | 無し | 同上 |
| `sprint_loop_decide.exclusion` | 先頭に選ぶ課題は棚卸しの判断待ちでも残る（#2722 の再現） | ほかの 7 つの順は着手前と同じ文 | 無し |
| `sprint_loop_exclusion.exclusion_reason` | 事実を集めて `exclusion` と同じ理由を返す | 無し | GitHub の誤りは着手前と同じ扱い |
| `sprint_loop_select.boundary_then_select` | 渡した関数で設計承認ゲート の答えを当てる | 無し | 関数を渡さないと `TypeError` |
| `stage_present.notes_block` | 箇条を差し替え、`### 移行` を残す（#1755 の再現） | 下位の節が無い版の節 | 無し |
| `stage_present.write_notes` | 版の節が書き直され、`### 移行` が残る | 無し | 版の節が無いと着手前と同じ誤り |
| `included.distributed_change` | リリース対象の置き場の下のファイルで真 | 置き場の外だけなら偽 | 無し |
| `included.no_change` | 本文が「無し」でリリース対象を変えていなければ真 | 本文が「無し」でもリリース対象を変えていれば偽（#2601 の再現） | 無し |
| `instructions.files` | `.ndf/` の下の変更を指示書の変更として返す（#3110 の再現） | 宣言が無いときも `.ndf/` を含む | 壊れた宣言は着手前と同じ誤り |
| `check-script-structure.import_edges` | 最上位・関数の中の import と文字列の `import_module` を辺にする | `if TYPE_CHECKING:` の中は辺にしない | 構文の誤りは辺が空 |
| `check-script-structure.strong_components` | 2 つの輪を別々に返す | 自分への import だけでは輪にしない | 無し |
| `check-script-structure.lazy_cycles` | 例外リストに無い輪が `lazy-cycle` で落ちる（C15） | 例外リストにあり、ほどけた輪は `unused-allow` で落ちる（C18・C22） | 関数の中の import を数えないように壊すと、輪の入力で落ちない |
| `check-script-structure.decide_impure` | `_decide.py` の `import durable` が `decide-pure` で落ちる（C6） | `datetime`・`dataclasses` は落ちない。名前が `_decide.py` で終わらないモジュールは見ない | `open(` の呼び出しが落ちる |
| `check-script-structure._no_loops_reached` | 耐久ワークフローと同じモジュールの呼び先の `while` を返す | 耐久ステップの印の付いた関数のループは返さない | 無し |
| `check-script-structure.durable_boundary` | 2 つの耐久の層のループが違反になり、項目のファイルで許される（I3） | `group_workflow` の `for` は見ない | 置き場の関数が無いと違反 |
| `check-script-structure._check_lines`（行数の上限） | 1,001 行のファイルが例外リストに無ければ `lines` で落ちる（C11） | 1,000 行と、500 行を超える 501〜1,000 行は落ちない（C12） | 例外リストの行数を超えると落ちる（C13） |
| `parsed.text` | 2 回呼ぶと 1 回だけ読む | 別の `root` は別に読む | 読めないファイルは例外 |
| `parsed.tree` | 1 回の検査の実行で、1 つのファイルの解析が 1 回（C29・I9） | 構文の誤りは None | 同上 |
| `parsed.clear` | 呼んだ後は読み直す | 無し | 無し |
| `check-script-structure.py_body_key` | 同じ本体の 2 つの関数が同じ鍵 | 型注釈と docstring だけが違う関数も同じ鍵。呼んだ後の `fn` が変わらない | 無し |
| `limits_rules.violations` | 構文木を渡すと着手前と同じ違反 | 無し | 構文木が None なら空 |
| `limits_decl.references` | 1 回の契約の検査の実行で、参照を集める処理が 1 回（C28） | 違う `files` は別に集める | 読めないファイルは例外 |
| 2 本の検査（結合） | 同じ木を入力に、着手前と後で違反の一覧が同じ（C31・I10） | 無し | 共有した構文木を規則が書き換えるように壊すと、後の規則の結果が変わって落ちる |
| `release-steps.py`（I12。`test_release_stages.py`） | 最上位の関数が `main`・`build_parser`・`cmd_*` だけで、各 `cmd_*` が段階のモジュールの関数を 1 つ呼んで返す | 無し | ハンドラーに分岐を足す・判断の関数を書き戻すと落ちる |

## 設計から受けた確かめの結果

設計文書が「詳細設計の段で」と書いた確かめを、2026-11-13 に打った結果である。

| 確かめ | 打ったもの | 結果 |
| --- | --- | --- |
| D1・D2・D3 | #1142 の頭の詳細設計の「実装計画」へ 11 本の行を足し、SF1b・SF2・SF6・SF7・SF8・SF9・SF11・SF13・DC2・DC8・K1〜K3 の先に要るものへ 8 件を戻し、「流す順序」を 16 の順番に組み直した。確かめのスクリプトは 2 つの設計文書の「設計の結果」を読む形にした | 「子 Issue 58・順番 16: 同じ順番の重なりと先に要るものの逆転は 0 件」、終了コード 0 |
| D4 | 設計文書の「開いている bug の割り当て」の表から番号を抜き出して数えた | 48 件、重複 0、理由の空の範囲外の行 0 |
| D5 | A0（#3196）が未マージで `scripts/check-ai-smells.py` が無い（`ls scripts/check-ai-smells.py` は「No such file or directory」）ため、兆候のあるファイルの一覧は取れない | 8 件の範囲の置き場はすべて、設計文書の「smells の決定 43 が外した範囲の割り当て」の表でどれかの子 Issue に割り当ててある。ファイルの粒度の差は、各子 Issue が着手の時点で数え直す |
| D6 | この文書の「関数の仕様」の 53 の名前と、#1142 の 4 つの主題の詳細設計の「関数の仕様」の名前を、末尾の名前で突き合わせた | 名前が重なるのは `_plan_steps`（scripted-flow）・`run`・`violations`・`references` の 4 つ。`run`・`violations`・`references` は別のモジュール（`proc`・`causes_decl`・`facts_decl` ほか）の関数で、同じ関数ではない。`flow._plan_steps` は同じ関数だが変更が違う: RF4 はループを判断のループへ移し、scripted-flow の行は遅れの打ち直し（`retry_wait` を `durable.sleep` で待つ）を足す。scripted-flow の子 Issue は RF4 の後に流れ（流す順序の順番 9 以降）、打ち直しの判断を `step_decide.decide` に、待ちを耐久の層の手の実行に足す（決定 15）。同じ変更が両方の行に載るものは 0 件 |
| D7 | 設計文書の「測る手順と縮める対象」の表の子 Issue の名前を数えた | 9 行に 11 本の名前（`RF5a`・`RF5b` と `RF6a`・`RF6b` は同じ行）がそろう |
| 触るファイルの数 | 設計文書の「設計の結果」の行ごとにパスを数えた | RF1 3・RF1b 8・RF2 6・RF3 9・RF4 11・RF5a 16・RF5b 8・RF6a 15・RF6b 14・RF7 11・RF8 13。RF5a は 30 本以下で分けない（決定 9） |
| 触るファイルの追加 | 関数の粒度へ下ろして、直す場所が設計の結果の外にあったもの | RF3 に `scripts/structure_checks/limits_rules.py`（`limit_literals`・`sleep_loops` が別に解析する）、RF5b に `supervise_lib/sprint_lane_close.py`（158 行目が `close_plan` と同じ番号の読み方を持つ）、RF6b に `parallel.py`（#3064 の `cmd_present` がここにある）を足した。足した後も D3 は 0 件 |
| U3（開発ループの記録の食い違い） | `lib/sprint_loop.py` の `cmd_lap`・`sprint_loop_gate2.py` の `finish_held`・`settle_release` を読んだ。PR #2905 の `loop_laps.csv` で lproc の 2・5・9 周目が「失敗」 | `cmd_lap` の結果の式は完了の記録の行・失敗の印・スプリントの状態だけを見て、版の記録を見ない。リリースした周でも、後の手順が 1 つ落ちれば失敗になる構造が判定の中にある。3 周の個々の失敗の鍵は `loop.json` がリポジトリに無いため未確認。RF7 は I11 の形を入れ、別の Issue は起票しない |
| #3169 の集計の在りか | `rg -n -i skipping plugins/ndf/scripts -g '!**/tests/**'` と `release_lib/materials.py`・`approval.py` の `skip` の検索 | 前者は `lib/project_mvv_signals.py` の 2 行だけ、後者は 0 件。全体テストの材料 `WholeTest`（`materials.py:82`）にスキップを数える欄が無い。集計は今のコードに無く、足すのは新しい機能なので範囲外のまま |
| 実行の鍵の置き場 | `lib/durable_keys.py` の import を読んだ | `key_hash` の置き場 `durable_keys` は `os`・`time` を読むため、判断のモジュールから読むと `decide-pure` に当たる。実行の鍵（#3176）は耐久の層の `flow.prefix_of` に残し、head を足す。`plan_decide` は置き場の決め方だけを持つ |
