# #1916: 実装の worker の上限と構造改善の予算を、プロジェクトの宣言か引数で決められるようにする

正は課題の本文（#1916）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> スプリントの実装のプランの `impl` の上限（`timeout`）と、検査のプランの構造改善の予算（cross-refactoring の `--budget-minutes`）を、`.ndf/` の宣言か `new sprint` の引数で受ける。構造改善のステップの `timeout` は予算に余裕を足した値（`with_margin`）にし、予算と同じ値で打ち切られないようにする。
>
> 今は conductor が実装のプランへ `timeout` 3600 → 5400 を毎回 jq で足し（m1693・m1385・m744・m815・m653・m1290・m1743・m1847・m1683。#1334）、検査のプランへ `--budget-minutes 60`・`timeout` 3600 → 4200 を足した（#1370・m1693・m1385）。#1336 では構造改善が 3600 秒ちょうどで exit=124 になった。

（書き手: 起票者（#1426 の「同じ形の箇所」の表から切り出した AI）、日付: 2026-10-10 以前。利用者の指示の原文ではない。利用者の承認済みの方針として当たるのは、マイルストーン「17 トークン消費の削減」の MVV の Value 1「決定論が先」・Value 6「汎用を保つ」と、プロジェクト MVV の Value 4・5）。原文の引用（`>` の行）には用語集で廃止した語が残るが、引用の外では「リファクタリング」と書く

## 起票者の推定

依頼は起票者の文であり、条件はすべて起票者が推したもの。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 実装の上限とリファクタリングの予算を、宣言か `new` の引数で受ける | conductor の jq の手当ての記録（m1693 ほか 9 回、#1334・#1370） | 受け入れ条件 1・2 |
| S2 | リファクタリングの `timeout` は `with_margin(予算)` にする | #1336 の exit=124 と、merge・release のステップの前例 | 受け入れ条件 2・3 |
| S3 | 宣言の置き場は `.ndf/supervise.json` か `.ndf/project.json` | 既存の宣言の読み込み（`decl.py`） | 受け入れ条件 4（どちらにするかは設計が決める） |
| S4 | ai-plugins の値（5400・60）を既定へ埋め込まない | マイルストーンの MVV の Value 6 | 受け入れ条件 8・含まない（承認ゲート 1 の利用者の指示で、ai-plugins の宣言にも書かない） |
| S5 | 渡さなければ `impl` の `timeout` は今の 3600 のまま | 起票者が既定を変えない方針として書いた | 受け入れ条件 1・8（`impl` だけ。`refactor` の `timeout` は承認ゲート 1 の利用者の指示で受け入れ条件 3 の式に変える） |
| S6 | リファクタリングの予算の中の配分は扱わない（#1385 で直してある） | #1385（閉じた） | 含まない |

## 目的

- conductor がスプリントのたびにプランの外で `timeout` と `--budget-minutes` を jq で書き足す手当て（Value 4 で数える手当て）を無くし、プロジェクトの宣言か引数の 1 か所で決まるようにする
- リファクタリングのステップが、cross-refactoring の予算と同じ秒数で打ち切られて（exit=124）結果を失うことを無くす

## 今の作り

- `plugins/ndf/scripts/supervise_lib/templates.py:65-76`: `plan_impl` の `impl` のステップは `"timeout": 3600` の固定値
- `plugins/ndf/scripts/supervise_lib/templates.py:216-226`: `_inspect_steps` の `refactor` のステップは `"timeout": 3600` の固定値で、cmd に `--budget-minutes` を渡さない（cross-refactoring の既定 30 分。`plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/vocabulary.py:107` の `DEFAULT_BUDGET_MINUTES = 30`）
- `_inspect_steps` は `plan_check`（`new check`。スプリントの検査の `plan_sprint_check` も経由する）と `plan_check_since`（`new check --since-last`。`new close` の最終の検査）の 2 か所から呼ばれる
- `plugins/ndf/scripts/supervise_lib/verify_steps.py:46-48`: `with_margin(seconds)` = 秒 + max(30, ⌈秒 × 0.1⌉)。merge・release のステップは既に使う
- `plugins/ndf/scripts/supervise_lib/decl.py:280-297`: `decl_fields` が各プランへ引き継ぐ宣言の値に、実装の上限もリファクタリングの予算も無い。`grep -rn "budget-minutes\|budget_minutes\|impl_timeout" plugins/ndf/scripts/supervise_lib plugins/ndf/scripts/supervise.py` は 0 件
- 引数は `plugins/ndf/scripts/supervise_lib/new_args.py` の `NEW_ARGS` の表が種別（impl / check / sprint / close …）ごとに持つ。宣言との優先は「引数 → 宣言」（`apply_decls` の `--test-cmd` → `project.json` の `test` と同じ形）
- リファクタリングの予算の中の時間の配分（着手前のテストの所要の差し引き）は #1385（閉じた）で直してある。この課題は予算と上限をプランへ渡す側だけを扱う

## 前提

- 前提 1: 優先は「`new` の引数 → `.ndf/` の宣言 → NDF の既定」の順とする。既存の `--test-cmd` / `--base` と同じ順で、誤っても局所的に直せる
- 前提 2: 予算を渡さないときの `refactor` の `timeout` は、cross-refactoring の既定の予算（cross-refactoring の定数 `DEFAULT_BUDGET_MINUTES`）に前提 3 と同じ式を掛けた値にし、固定の秒を書かない。既定の予算の値は cross-refactoring の定数から読み、supervise の側に数で写さない（承認ゲート 1 の利用者の指示、2026-10-11:「margin は 10% とし、紛らわしいので固定値は書かない」）
- 前提 3: 予算を渡したときの `refactor` の `timeout` は `with_margin(<分> × 60)`（余裕は 10%、最低 30 秒。`with_margin` の式と最低 30 秒は変えない）とし、下限の固定値は掛けない。予算を短く渡したときに `timeout` も短くなるのは、渡した側の意図として扱う
- 前提 4: リファクタリングの予算は `refactor` のステップを持つすべてのプラン（`new check`・`new check --since-last`・`new sprint` の検査・`new close` の最終の検査）に同じ値で効く。`new close` の引数も受ける
- 前提 5: 値は正の整数だけを受ける（実装の上限は秒、リファクタリングの予算は分）。0・負・整数でない値は、プランを書かずに止まる（宣言なら既存の `DeclError` と同じ止まり方、引数なら argparse の誤り）

## 対象範囲

含む:
- `impl` のステップの `timeout` を、引数（`new impl` / `new sprint`）か宣言から決める
- `refactor` のステップの cmd の `--budget-minutes` と `timeout` を、引数（`new check` / `new sprint` / `new close`）か宣言から決める
- 宣言の新しい項目の読み込みと形の検査、`decl_fields` による `new sprint` の各プランへの引き継ぎ
- 宣言の新しい項目の説明（宣言の書き方を持つ文書）

含まない:
- リファクタリングの予算の中の時間の配分（#1385 で済み）
- 設計文書を実装の指示へ渡す・lint を実装のステップに入れる（#1651 に寄せる）
- 他のステップ（`review`・`assess`・merge・release など）の `timeout` の変更
- ai-plugins の `.ndf/` に実装の上限・リファクタリングの予算の値を書き込むこと（承認ゲート 1 の利用者の指示で外した。conductor の手当ての値は根拠の無い上乗せか、1 回の実測を写しただけの固定値のため。値が要るときは `new` の引数で渡す）
- `with_margin` の式と最低 30 秒（`MARGIN_FLOOR`）の変更
- cross-refactoring の既定の予算（`DEFAULT_BUDGET_MINUTES`）の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | conductor が `supervise.py new <種別>` を打った | スプリント・課題の着手 | — | — |
| E2 | 宣言から実装の上限とリファクタリングの予算を読んだ | E1 で引数に値が無い | 形が違えば DeclError でプランを書かずに止まる（前提 5） | E1 |
| E3 | プランの `impl` の `timeout` を決めた | E1（impl / sprint） | — | E2 |
| E4 | プランの `refactor` の cmd と `timeout` を決めた | E1（check / sprint / close） | — | E2 |
| E5 | `new sprint` が課題ごとの実装のプランと検査のプランへ値を引き継いだ | E1（sprint） | 引き継ぎ漏れはテストで落とす（受け入れ条件 5） | E2 |
| E6 | supervise.py が `refactor` を `timeout` で打ち切った | cross-refactoring が `timeout` を超えた | exit=124。`on_fail: abort` で止まる（今と同じ） | E4 |

E6 は今の振る舞いのまま。受け入れ条件 3 は、E6 が予算どおりに終わる run で起きないことを求める。

## 受け入れ条件

- [ ] 1. 宣言（または `new sprint` / `new impl` の引数）で実装の上限（秒）を渡すと、`impl` のステップの `timeout` がその値になる。どちらも渡さなければ変更の前と同じ値のまま
- [ ] 2. 宣言（または `new sprint` / `new check` / `new close` の引数）でリファクタリングの予算（分）を渡すと、`refactor` のステップの cmd に `--budget-minutes <分>` が入り、`timeout` が `with_margin(<分> × 60)` になる
- [ ] 3. 予算を渡さないとき、`refactor` の cmd に `--budget-minutes` は入らず、`timeout` は `with_margin(<cross-refactoring の既定の予算（分）> × 60)` になる。既定の予算は cross-refactoring の定数から読み、cross-refactoring の既定を変えると `timeout` も追う
- [ ] 4. 宣言の置き場は既存の宣言（`.ndf/supervise.json` か `.ndf/project.json`）の項目として足し、新しいファイルを作らない
- [ ] 5. `new sprint` に宣言か引数で値を渡すと、書き出した課題ごとの実装のプランの `impl` と、検査のプランの `refactor` に同じ値が入る
- [ ] 6. 引数と宣言の両方があるときは引数の値が入る
- [ ] 7. 0・負・整数でない値を渡すと、プランのファイルを書かずに 0 以外で終わり、どの項目が違うかを出す
- [ ] 8. NDF の既定（コード）に実装の上限・リファクタリングの予算・`refactor` の `timeout` の固定値を新しく書かない。宣言も引数も無いときのプランは、`impl` の `timeout` と `refactor` の cmd が変更の前と同じで、`refactor` の `timeout` だけが受け入れ条件 3 の値になる
- [ ] 9. 既存のテストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 宣言の新しい項目は、宣言の書き方を持つ文書に 1 か所だけ説明を置き、写さずに指す（Value 7） |
| 移行性 | 既存の `.ndf/` の宣言はそのまま読める（新しい項目は任意）。宣言を足さない利用者のプランは、`refactor` の `timeout` だけが受け入れ条件 3 の値に変わる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（互換あり）。`supervise.py new` に任意の引数が増え、`.ndf/` の宣言に任意の項目が増える。既存の引数と宣言の意味は変えない |
| データ | 無し |
| 既存の振る舞い | 値を渡したときだけ、プランの `impl` の `timeout` と `refactor` の cmd が変わる。`refactor` の `timeout` は値を渡さないときも、cross-refactoring の既定の予算から出した値に変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（プランを書き出して `impl` / `refactor` の `timeout` と cmd を見るテストを足す） |
| 静的解析・型検査 | `bash scripts/check-lint.sh` |
| 同期 | `bash scripts/build-runtime-plugins.sh` と `bash scripts/validate-runtime-plugins.sh` |
| 手動確認 | 次のスプリントで conductor が jq の手当てをせずに `new sprint` を打ち、プランの `impl` / `refactor` の値が宣言どおりかを見る（リリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `plugins/ndf/scripts/supervise_lib/`（プランの雛形・宣言・引数）に置く。`plugins/ndf/scripts/experimental/` は使わない（既定で働く安定版の変更のため。`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | `AGENTS.md`（最小限のコード実装）。`bash scripts/check-lint.sh` で確かめる |
| テスト戦略 | 単体テストでプランの JSON の値を見る。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、リンタの適用、`impl` の既定の値を変えないことと、`refactor` の `timeout` が既定の予算から出ることのテスト |
| 確認してから行う | — |
| 行わない | cross-refactoring の予算の配分の変更、他のステップの `timeout` の変更、#1651 の範囲の変更、ai-plugins の `.ndf/` へ値を書くこと、`with_margin` の式と最低 30 秒の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 宣言の置き場を `.ndf/supervise.json` と `.ndf/project.json` のどちらにするか、項目の名前 | 設計（`design`） | 設計 PR |
| 引数の名前（例: `--impl-timeout` / `--refactor-budget-minutes`） | 設計（`design`） | 設計 PR |
| 前提 2・3 の採否 | 決定済み（承認ゲート 1 の利用者の指示、2026-10-11。前提 2・3 をその形へ書き直した） | — |

## 由来

#1426 の「同じ形の箇所」の表の 6 行目のうち「上限と予算」（受け入れ条件 8）。表の 6 行目の「設計文書を実装の指示へ渡す」と「lint を実装のステップに入れる」は #1651 に寄せる（下記）。

## 依存

- 先に無し。#1651（設計の結果のステージが実装のプランを作り直す）と同じ実装のプランを触るが、`timeout` は #1651 の作り直しでも残る（#1651 の本文）ため衝突しない

## 見積

Size 3。宣言の読み込み（`decl.py`）・`new_args.py` の引数・`templates.py` の 2 か所・`with_margin` の適用・cross-refactoring の既定の予算の読み込みと、`impl` の既定の値を変えないことのテスト。
