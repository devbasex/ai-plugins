# #1668: cross-refactoring: 構造検査が項目の範囲テストに入らず、全体テストで初めて落ちて無関係な項目まで取り消される

正は課題の本文（#1668）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文を、見出しの深さだけを 1 段下げ、引用としてそのまま残す。

> ### 何が起きたか
>
> スプリント m725 の検査（スプリント PR https://github.com/devbasex/ai-plugins/pull/1663 、2026-10-03 06:36〜07:46 JST）で、cross-refactoring の改善項目 16 件のうち 3 ファイルを触った 8 件が、スクリプトの構造検査（`scripts/check-script-structure.py`）に違反した。
>
> | ファイル | 違反 |
> | --- | --- |
> | `plugins/ndf/scripts/lib/transcript_agents.py` | 861 行。例外リストの 853 行を超えた（抽出系の項目 3 件で増えた） |
> | `plugins/ndf/skills/backlog-refinement/scripts/upkeep.py` | 636 行。例外リストの 614 行を超えた（3 件） |
> | `plugins/ndf/scripts/merged_lib/trash.py` | `_remove` を足し、`relay_lib/accounts.py` の同名関数と本体が違う（2 件） |
>
> 違反は項目の範囲テストでは見えず、危険フラグの全体テスト（`scripts/tests/test_check_script_structure.py::test_repository_matches_its_allow_list`）で初めて出た。そこからは #1649 の経路に入り、危険フラグの無関係な項目 7 件が取り消され、原因の 8 件は未確認のまま push された。最終ゲートは打ち切りで止まり、conductor が worker に 8 件を手で取り消させた。
>
> PR https://github.com/devbasex/ai-plugins/pull/1634 （スプリント m1340）でも同じ検査で I-005・I-006 が落ちている（#1649 の本文）。**構造改善のたびに、このテストが全体テストでしか落ちない。**
>
> ### 原因
>
> - cross-refactoring は、**静的解析の suite**（`.ndf/project.json` の `test.suites[]` で `kind: lint`）を項目のコミットが変えたファイルにかけて、項目の単位で検証する（`plugins/ndf/scripts/lib/test_strategy.py` の `scope_runs`）。落ちた項目は修正か取り消しに回り、ほかの項目を巻き込まない
> - このリポジトリの宣言には pytest の suite しかない。構造検査は pytest のテストとして全体テストの中にだけある
> - `scripts/check-script-structure.py` はファイルの指定を受け取らない（`--root` と `--allow` だけ）。根全体を 2.6 秒で検査する
>
> ### 受け入れ条件（案）
>
> 1. `scripts/check-script-structure.py` が位置引数でファイルを受け取り、検査は木全体で行ったうえで、**指定したファイルに関わる違反だけ**で合否を決める（同名の違反は、どちらかのファイルが指定に入っていれば数える）。引数が無いときの振る舞いは今と同じ
> 2. `.ndf/project.json` に静的解析の suite（構造検査）を宣言し、`scope_command` が `{paths}` を受け取る。受け持つ `paths` は検査の対象（`plugins/ndf/scripts`・`plugins/ndf/skills/*/scripts`・`scripts` など、検査が実際に読む範囲）に合わせる
> 3. 上の 8 件と同じ変更（行数の上限を超える抽出・同名関数の追加）を項目として通すと、その項目の範囲テストが落ち、ほかの項目は取り消されない（再現テスト）
> 4. 宣言を解析し直しても（`project-decl.py`）、足した suite が残る
>
> ### 関連
>
> - #1649: 全体テストの失敗を危険フラグの項目だけに帰す。この課題は、構造検査の失敗をそもそも全体テストへ持ち込まない側の手当て。#1649 は構造検査以外の失敗でも起きるので、両方要る
> - #464: 整形の違反で push が落ちる。`ruff format --check` も同じ形で静的解析の suite にできる（この課題の範囲には入れない）

## 目的

- スクリプトの構造検査（`scripts/check-script-structure.py`）の違反を、全体テストではなく、違反を持ち込んだ改善項目の範囲テストで捕まえる
- 違反した項目だけを修正か取り消しへ回し、全体テストの失敗から無関係な項目を取り消す経路（#1649）へ入らないようにする
- リファクタリングのたびに同じテスト（`test_repository_matches_its_allow_list`）が全体テストでだけ落ちる状態をなくす

設計文書は #1649 と同じ 1 本（`issues/issue-1649-design.md`）で扱う。

## 前提

- 前提 1: cross-refactoring の側は変えない。静的解析の suite（宣言の `test.suites[]` で `kind: lint`）を項目のコミットが変えたファイルにかける仕組み（`test_strategy.scope_runs`）は既にある。足りないのは、このリポジトリの宣言と、構造検査がファイルを受け取る入口である
- 前提 2: 構造検査は同名・同じ本体の関数のように木全体を見ないと決まらない規則を持つので、ファイルを指定しても検査は木全体で行い、合否だけを指定したファイルに関わる違反で決める。根全体の検査は 2.6 秒で、範囲テストに入れても所要は問題にならない
- 前提 3: 静的解析の suite の受け持つ範囲（`paths`）は、構造検査が実際に読む `plugins/ndf/` と、例外リストの置き場 `scripts/script-structure-allow/` と、検査のスクリプト自身にする。例外リストを変えた項目も検査の対象になる
- 前提 4: 例外リストの項目が違反に当たらない（`unused-allow`）ときは、その項目の `path` が指定に入っていれば数える
- 前提 5: `.ndf/project.json` への suite の追加は、共通原則 C7（利用者の設定を書き換える）に当たる。設計 PR の承認で人がこの書き換えを明示的に認めたときだけ書く。承認の対象であることを設計 PR の本文に示す

## 対象範囲

含む:
- `scripts/check-script-structure.py` に、位置引数でファイルを受け取る入口を足す
- `.ndf/project.json` に、構造検査を静的解析の suite として宣言する（C7。前提 5）
- 構造検査の違反を持ち込んだ項目が範囲テストで落ち、ほかの項目が取り消されないことを確かめる再現テスト
- 宣言を解析し直しても足した suite が残ることを確かめる

含まない:
- 全体テストの失敗から原因の項目を決める処理（#1649）
- 最終ゲートの打ち切りの扱い（#1669）
- `ruff format --check` などほかの静的解析の suite 化（#464）
- 構造検査の規則（行数の上限・同名関数・例外リスト）の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 改善項目のコミットを取り込んだ | 実装担当の結果 | — | — |
| E2 | 項目の範囲テストで構造検査を走らせた | 検証（`verify`）で、項目のコミットが静的解析の suite の `paths` に当たるファイルを変えた | 検査が例外リストを読めない（終了コード 2）→ 落ちたものとして扱う（今の静的解析の suite と同じ） | E1 |
| E3 | 項目の構造検査が落ちた | E2 で、指定したファイルに関わる違反がある | — | E2 |
| E4 | その項目だけを修正へ回した | E3 | 締め切りを過ぎた → その項目だけを取り消す（今の範囲テストの経路） | E3 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 範囲テスト | 変更が触った範囲に限って走らせるテスト。ここでは静的解析の suite（構造検査）を、項目が変えたファイルにかけるものを含む |
| 全体テスト | テストの宣言のすべての suite を走らせるテスト |

## 受け入れ条件

- [ ] 1. `python3 scripts/check-script-structure.py <ファイル>...` は木全体を検査し、指定したファイルに関わる違反があれば終了コード 1、無ければ 0 で終わる。結果 JSON の `items` には指定したファイルに関わる違反だけが出る
- [ ] 2. 同名の関数の違反（`same-name` / `same-body`）は、関わるファイルのどちらか 1 つが指定に入っていれば数える（#1634 の `participants.py` を指定すれば、`setup.py` との衝突が出る）
- [ ] 3. 行数の違反（`lines`）は、そのファイルが指定に入っているときだけ数える
- [ ] 4. 位置引数を渡さないときの出力と終了コードは今と同じ（既存のテストが変わらずに通る）
- [ ] 5. `.ndf/project.json` に `kind: lint` の suite があり、`scope_command` が `{paths}` を受け取って `check-script-structure.py` を走らせ、`paths` が前提 3 の範囲になっている
- [ ] 6. 再現テスト: 行数の上限を超える抽出の項目と、同名関数を足す項目を、ほかの項目と並べて検証に通すと、その 2 項目の範囲テストが落ちて修正か取り消しに回り、ほかの項目は `verified` のまま残る
- [ ] 7. `python3 plugins/ndf/scripts/project-decl.py` で宣言を解析し直しても、足した suite が残る
- [ ] 8. 退行しない: 構造検査に関わらない項目（`plugins/ndf/` の外だけを変えた項目）では、構造検査の suite が組まれない（`scope_runs` が入れるファイルの無い suite を組まない）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 構造検査の範囲テストは 1 回あたり今の根全体の検査と同程度（約 3 秒）に収まる |
| 運用・保守性 | 例外リストの読み方・規則は 1 か所（`check-script-structure.py`）のまま。ファイルの指定は合否の絞り込みだけを足す |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `check-script-structure.py` に位置引数が増える（省略時は今と同じ）。CI の呼び方は変えない |
| データ | `.ndf/project.json` に suite が 1 つ増える（C7） |
| 既存の振る舞い | cross-refactoring・supervise の範囲テストで、`plugins/ndf/` を変えた項目に構造検査が走る。最終ゲートの静的解析の全体テストにも構造検査が入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest scripts/tests/test_check_script_structure.py plugins/ndf/skills/cross-refactoring -q -n 4` |
| 静的解析 | `python3 scripts/check-script-structure.py`・`python3 scripts/check-script-structure.py plugins/ndf/scripts/lib/test_strategy.py` |
| 宣言 | `python3 plugins/ndf/scripts/project-decl.py`（解析し直して suite が残るか） |
| 手動確認 | 次に cross-refactoring を通すスプリントで、構造検査を落とした項目があれば、それが範囲テストの段で落ちたことを状態ファイルで見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 構造検査は `scripts/`（このリポジトリの開発の検査）に置いたまま。NDF の配布物（`plugins/ndf/`）へ移さない |
| コーディング規約 | `AGENTS.md`。宣言の形は `plugins/ndf/skills/development-workflow/schemas/project.schema.json` |
| テスト戦略 | ファイルの指定の絞り込みは `scripts/tests/test_check_script_structure.py` の単体テスト。項目の範囲テストで落ちることは cross-refactoring の再現テスト |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の構造検査のテストの実行 |
| 確認してから行う | `.ndf/project.json` の書き換え（C7。設計 PR の承認で人が認めたときだけ） |
| 行わない | 構造検査の規則と例外リストの中身の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `project-decl.py` が解析し直しで手で足した suite を消す場合、残す仕組み（宣言の印か、解析の側の保持か） | 設計（実際に打って確かめてから） | 設計 PR |
