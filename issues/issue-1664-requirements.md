# #1664: CI の所要を飛ばした run から測り、merge の CI 待ちが pytest の完了前に打ち切られる

正は課題の本文（#1664）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> #1664。この要求へ次を取り込む: #1717（重いテストを飛ばした CI の実行を所要として測り、プランの CI を待つ上限が 126 秒に縮む。#1664 と同じ原因の重複）。#1717 は #1664 の重複として閉じる前提で、#1717 の再現の記録（スプリント m1693 の PR 1714・1716）と受け入れ条件案も取り込む。#1717 の受け入れ条件も同じ写し（issues/issue-1664-requirements.md）へ含め、#1717 の本文には #1664 の要求へ取り込んだことを書く。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

課題の起票時の題: 「CI の所要を飛ばした run から測り、merge の CI 待ちが pytest の完了前に打ち切られる」

## 何が起きたか（3 回の再現）

| いつ | どこで | 症状 | 測られた値 |
| --- | --- | --- | --- |
| 2026-10-02 | スプリント m725、#824 の実装プラン（PR https://github.com/devbasex/ai-plugins/pull/1662 ） | `merge` が CI の pytest の完了を待たずに 186 秒で打ち切られた（exit=124）。`merged-steps.py merge-when-green` を手で打ち直すと通った | `pytest.yml` の壁時計 19 秒（ci-scope で pytest のジョブを飛ばした run）。全ワークフローの最大 52 秒 → `ci_wait_timeout` 156 秒 |
| 2026-10-03 | スプリント m1649、#1649 の実装プランの `test-limited` | 「範囲テストが 3 秒で終わらなかった」で止まった | `test_duration` が `ci-steps` の 1.0 秒（run 37079940471。pytest のジョブを飛ばした run）だけになり、前の `ndf-record`（258 秒）が消えた。`test-run.py scope` の上限が 3 × 1.0 = 3 秒 |
| 2026-10-04 | スプリント m1693、実装のプラン 3 本のうち 2 本（PR https://github.com/devbasex/ai-plugins/pull/1714 ・PR https://github.com/devbasex/ai-plugins/pull/1716 ） | `merge` が `merge-when-green --timeout 126` で打ち切られた（exit=124 / exit=1、`#1716 の CI が 126.0 秒で終わらない（待ち: pytest (0/2)）`）。conductor が `merge-when-green` を手で打ち直し、検査のプラン（`6-check.json`）は `--timeout 1800` へ手で延ばした | `pytest.yml` の最新の成功が release の PR の run 37160943783。`scripts/ci-heavy-skip.py` が shard を飛ばし、`pytest (${{ matrix.shard }}/2)` は skipped。壁時計 17 秒・テストの step 合計 1.0 秒（前回の解析は 296 秒・424 秒）。c は runtime-plugin-smoke の 42 秒 → `ci_wait_timeout` = 3·c = 126 秒 |

m1693 の再現の場所: `~/.local/state/ndf/sv/sprint-m1693/5-impl-1654-state`・`5-impl-1655-state` の `merge`。宣言の差分は手順 0 の `project-decl.py write` の出力。

m1649 では、宣言はメインディレクトリの `.ndf/project.json`（未コミット）を worktree からも読むため、worktree 側の値（424 秒）は効かなかった。

#1717（https://github.com/devbasex/ai-plugins/issues/1717 ）は m1693 の中で別に起票した同じ原因の課題で、この要求へ取り込んだ（受け入れ条件 AC7・AC8）。

## 原因

1. `plugins/ndf/scripts/project_lib/measure_ci.py` の `_measure_runs` は `actions/runs?status=success&per_page=50` の直近 50 件から、**ワークフローごとに最新の 1 件**を代表に採る
2. その run が重いジョブを飛ばした run（ジョブの conclusion が `skipped`）でも区別しない。`wall_seconds`（壁時計）と、同じ run のテストの step の合計（`test_duration` の `ci-steps`）・JUnit の合計（`ci-junit`）が、飛ばした run の値になる
3. `plugins/ndf/scripts/lib/test_strategy.py` の `ci_wall_seconds` は、名前の合うワークフローが無ければ全ワークフローの最大を c に採り、`ci_wait_timeout` = 3·c がプランの `merge`（`supervise_lib/verify_steps.py`）と `test-run.py` の CI 待ちに入る。`whole_seconds` は `ndf-record` → `ci-junit` → `ci-steps` の順に w を採るため、`ndf-record` が無いと飛ばした run の `ci-steps` が w になる

## 目的

- 宣言の解析が、重いジョブを飛ばした CI の run を CI の所要に使わず、プランの CI 待ち（`ci_wait_timeout`）とテストの上限が、テストを実際に走らせた run の所要から出るようにする
- 実装・検査のプランの `merge` が、必須のチェック（pytest）が終わる前に打ち切られて conductor が手で打ち直す手当てを無くす

## 前提

- 前提 1: 「飛ばした run」は、その run のジョブのうち conclusion が `skipped` のものを 1 つ以上含む run とする。判定は GitHub Actions の API が返すジョブの conclusion だけで行い、ワークフローのファイル名やこのリポジトリの `ci-heavy-skip.py` の名前に依らない（Value 5）
- 前提 2: ワークフローの代表に採る run は、取得した成功の run のうち飛ばしていない run の中から選ぶ。1 件を採るか、複数件の最大・中央値を採るかは設計が決める（未決 1）
- 前提 3: 取得の範囲に飛ばしていない run が無いワークフローでは、飛ばした run の値を書かない。前の宣言（`.ndf/project.json`）にそのワークフローの値があれば残し、無ければ値を書かずに解析の注記（`notes`）へ理由を残す
- 前提 4: `test_duration` の `ci-steps` と `ci-junit` も、前提 1〜3 と同じ規則で選んだ run から測る
- 前提 5: `merge-when-green` 側に「必須のチェックが動いている間は打ち切らない下限」を足すことはこの課題で行わない。現象の場所（待ち）ではなく原因の場所（測定）で直す（Value 6）。測定を直した後も同じ打ち切りが起きたら、別の課題で扱う
- 前提 6: m1649 で `ndf-record` が消えた理由はこの課題の設計で調べて記録する。2026-10-04 時点で `~/.local/state/ndf/metrics/devbasex--ai-plugins/cross-refactoring-allocation.jsonl` は 3 行（最も古い run の記録は 2026-10-03 の rf1673）で、m1649 の解析の時点で記録が無かった可能性がある。記録があるのに消えたと分かった場合は、その原因もこの課題で直す
- 前提 7: #1717 は #1664 の重複として扱い、閉じるのは棚卸し（`backlog-refinement`）が行う。この工程では #1717 の本文へ取り込んだことを書くだけにする

## 対象範囲

含む:
- 宣言の解析（`project-decl.py measure` / `write`）の CI の測定で、代表の run の選び方を変える（`wall_seconds`・`ci-steps`・`ci-junit`）
- 飛ばしていない run が見つからないときの扱い（前の値を残す・注記を残す）
- m1649 で `ndf-record` が消えた理由の調査と、記録があるのに消える不具合があればその修正
- 宣言の解析の確定仕様（`docs/specifications/` の該当する文書）の、代表の run の選び方の記述

含まない:
- `merge-when-green` と `test-run.py` の CI 待ちの打ち切りの規則（前提 5）
- `ci_wall_seconds` の「名前の合うワークフローが無ければ最大」の規則と、`3·c` / `3·w` の係数
- ワークフローの `ci-heavy-skip.py` の判定（飛ばすこと自体は正しい振る舞い）
- 宣言をメインディレクトリから読む仕組み（m1649 の worktree 側の値が効かない件）
- クラス図（設計の成果物）: 型（クラス）を足さず変えない。変えるのは `measure_ci.py`・`merge.py` の関数だけである

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 宣言の解析を始めた | `project-decl.py measure` / `write`（手順 0 の解析し直し） | — | — |
| E2 | 成功の run の一覧を取った | E1 | `gh` が使えない・時間切れ → `ci` を `unknown` にし注記を残す（今の振る舞い） | E1 |
| E3 | ワークフローごとに候補の run のジョブを取った | E2 | 時間切れ → 取れた分だけで決め、取れなかったワークフローは前提 3 の扱い | E2 |
| E4 | 飛ばした run を候補から外した | E3 | — | E3 |
| E5 | ワークフローごとの代表の run を決めた | E4 | 候補が 0 件 → 前提 3（前の値を残す / 注記） | E4 |
| E6 | 壁時計とテストの所要（`ci-steps`・`ci-junit`）を記録した | E5 | — | E5 |
| E7 | 宣言（`.ndf/project.json`）を書いた | E6 と `ndf-record` の読み取り | — | E6 |
| E8 | プランが `ci_wait_timeout` とテストの上限を出した | `supervise.py` の起動 | — | E7 |
| E9 | `merge` が CI の必須のチェックの完了を待った | プランの `merge` のステップ | 上限で打ち切り → exit=124（今回の症状） | E8 |

E3 は今は代表の 1 件だけのジョブを取る。候補を増やすと `gh` の呼び出しが増えるため、締め切り（I10）の扱いを非機能の条件に置いた。

## 受け入れ条件

#1664 の条件:

- [ ] AC1: 同じワークフローに「飛ばした run（新しい）」と「飛ばしていない run（古い）」が成功の run の一覧にあるとき、宣言のそのワークフローの `wall_seconds` は飛ばしていない run の値になる（API の応答を差し替えた単体テストで確かめる）
- [ ] AC2: AC1 と同じ入力で、`test_duration` の `ci-steps` と `ci-junit` の値と `detail` の run の番号は、飛ばしていない run のものになる
- [ ] AC3: 取得の範囲に飛ばしていない run が無いワークフローでは、飛ばした run の値を `wall_seconds`・`ci-steps`・`ci-junit` に書かない。前の宣言に同じワークフローの `wall_seconds` があればその値が残り、無ければ `wall_seconds` を書かず、解析の注記に「飛ばしていない run が無い」旨とワークフローのパスが出る
- [ ] AC4: どの run にも `skipped` のジョブが無いワークフローでは、`wall_seconds` と `ci-steps` は今と同じ値になる（退行しない）
- [ ] AC5: 飛ばした run の判定は、ジョブの conclusion だけで行う。ワークフローのファイル名・ジョブ名・`ci-heavy-skip` の語をコードの判定に書かない（別の名前のワークフローで AC1 と同じ結果になる単体テストで確かめる）
- [ ] AC6: NDF の実行の記録（`cross-refactoring-allocation.jsonl`）に `whole_test.init` が 1 件以上ある機械で解析し直すと、`test_duration` に `ndf-record` が残る。m1649 で `ndf-record` が消えた理由が設計文書に記録されている（前提 6）

#1717 から取り込んだ条件（#1717 の受け入れ条件案）:

- [ ] AC7: 重いジョブを飛ばした成功の run しか直近に無いワークフローで、その run を CI の所要に使わない（前の値を残すか、飛ばしていない run を探す）
- [ ] AC8: このリポジトリで再解析しても `pytest.yml` の壁時計が shard を走らせた run の値になる（`project-decl.py measure` の出力の `pytest.yml` の `wall_seconds` が、`pytest (0/2)` と `pytest (1/2)` のジョブが success の run の壁時計と一致する）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 解析の `gh` の呼び出しは今の締め切り（I10。1 回 30 秒と締め切りまでの残りの小さい方）の中で打ち切られ、締め切りに届いたら起動しない。候補の run を増やして呼び出しが増えても、解析全体の締め切りは変えない |
| 運用・保守性 | 代表の run を決められなかったワークフローは、解析の注記に理由とパスが出て、人が宣言を見て気づける |
| セキュリティ | `gh` へ渡すのは読む要求（`--method GET`）だけ（I5）のまま |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。宣言（`.ndf/project.json`）の `ci.workflows[].wall_seconds` と `test_duration.measured[]` の形は今のまま。値の出所の run が変わる |
| データ | スキーマの変更は無い。次の解析し直しで値が入れ替わる |
| 既存の振る舞い | プランの `ci_wait_timeout`・`test_timeout`・`whole_timeout` が、飛ばしていない run の所要から出る（このリポジトリでは c が数十秒から数百秒へ戻る） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（`gh` の応答を差し替えた `measure_ci` の単体テストで AC1〜AC5・AC7） |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`・`claude plugin validate .` |
| 手動確認 | このリポジトリで `python3 plugins/ndf/scripts/project-decl.py measure` を打ち、`pytest.yml` の `wall_seconds` と `ci-steps` の `detail` の run 番号を `gh run view <run> --json jobs` と突き合わせる（AC8）。リリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`。変更は worktree の中で行い、測定は `plugins/ndf/scripts/project_lib/` に置く |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。外部コマンド（`gh api`）の応答の形は書く前に実際に打って確かめる |
| テスト戦略 | 単体テストで `gh` の応答を差し替える。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`gh` の応答の形を実際の run（37160943783 など）で確かめる |
| 確認してから行う | 宣言の形（スキーマ）の変更 |
| 行わない | `merge-when-green` の待ちの規則の変更、`ci-heavy-skip.py` の変更、`.ndf/project.json` の手での書き換え（C7） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 未決 1: 飛ばしていない run が複数あるとき、最新の 1 件を採るか、複数件の最大・中央値を採るか | 設計（`design`） | 設計 PR まで |
| 未決 2: 直近 50 件に飛ばしていない run が無いとき、さらに前の頁を取りに行くか（呼び出しの増え方と締め切りとの兼ね合い） | 設計（`design`） | 設計 PR まで |
| 未決 3: 前の宣言の値を残す処理を、測定の側（`measure_ci`）と宣言の合成の側（`project_lib/merge.py`）のどちらに置くか | 設計（`design`） | 設計 PR まで |

## 当座の回避

プランの `merge` のステップの `timeout` と `--timeout` を手で延ばす（m725 の `6-check.json` は 1500 / 1400、m1693 の検査のプランは `--timeout 1800`）。`test_duration` が縮んだときは、メインディレクトリの `.ndf/project.json` の値を前の値へ戻して `--from test-limited` で流し直す（m1649）。
