# #1658: cross-refactoring: 閉じた・マージ済み・Draft でない PR にも提案から push まで進む

正は課題の本文（#1658）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> #1658。この要求へ次を取り込む: #1660（見送りの件数が駆動の要約 metrics.deferred と完了報告で食い違う）。どちらも cross-refactoring の入口の検査と結果の報告の整合の問題として 1 本の設計で扱う。#1660 の受け入れ条件も同じ写し（issues/issue-1658-requirements.md）へ含め、#1660 の本文には「## 受け入れ条件」の節を書き、#1658 の要求へ取り込んだことを書く。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（2026-10-06、conductor の起動指示。起票時の記録は末尾の「起票時の記録」の節に残す）

## 目的

- cross-refactoring が、閉じた・マージ済みの Pull Request に対して、提案・実装・push へ進まない。閉じた・マージ済みの Pull Request の head ブランチへ改善のコミットが積まれることを防ぐ。Draft かどうかは問わない（2026-10-07、本番配布の承認ゲート 2 での利用者の差し戻し）（#1658）
- 1 つの実行の「見送り」の件数が、結果 JSON の `metrics.deferred`・リファクタリング計画のコメントの件数の行・完了報告（`refactor.py report`）の件数の行で同じ値になる。計画に入らなかった提案まで含む数は、別の語で出す（#1660）

## 前提

- 前提 1: Draft であることは求めない。新しい実行も、終わっていない状態ファイルから再開する実行も、Pull Request が開いていれば続け、閉じた・マージ済みなら止まる。判定に新しい実行と再開の区別は要らない（Draft の検査は 2026-10-07 の利用者の差し戻しで外した。SKILL.md の前提「Draft で開いている」は conductor の起票で足した条件で、利用者の決定ではなかった）
- 前提 2: 止めるときの終了コードは、`refactor.py init` の既存の中断（4）を使う。駆動（`drive.py`）は既存の表どおり終了コード 1・`metrics.exit` 4 で終わる。共通ライブラリの終了コードの表（`scripts/lib/drive_pause.py`。cross-review と共有）には新しい値を足さない
- 前提 3: Pull Request の状態は、`init` が既に読んでいる `repos/{repo}/pulls/{pr}` の応答（`state` / `merged_at`）から判定する。GitHub API の呼び出しを増やさない
- 前提 4: 結果 JSON の `metrics.deferred` の意味（表示の状態が「見送り」の改善項目の数。`ledger.tally` が数える）は変えない。食い違いは完了報告の側を `ledger.tally` へ揃えて解く。計画に入らなかった提案を含む数（`deferred_items` の数）は「見送った提案」として別に出す
- 前提 5: 状態が見送りの改善項目は、すべて `deferred_items` にも理由 `not_done` か `test_failed` で載っている（`commands/implement.py` が `status` を `deferred` にするときに `defer` を呼ぶ。状態を見送りにする経路は今はこの 1 つだけ。#1743 の決定 10 の後は採り直し（`readopt`）が持ち越しの項目を見送る経路が加わり、同じく `defer` を呼ぶ）。設計で、状態を見送りにする経路がどれも `defer` を呼ぶことを確かめ、呼ばない経路があれば受け入れ条件 12 を直す

## 対象範囲

含む:
- `refactor.py init` の入口で、Pull Request が開いているか・マージ済みでないかを確かめ、満たさなければ作業ディレクトリと状態ファイルを用意する前に止める
- 止まったときの理由の出力（Pull Request の番号と、どの条件を満たさなかったか、直し方）
- 完了報告の件数の行の「見送り」を `ledger.tally` の値にすること
- 完了報告で「見送った提案」の総数と理由別の件数を、改善項目の「見送り」と別の語で出すこと
- `SKILL.md` の前提・終了コード 1 の行・`metrics` の説明・完了報告の節を、上の振る舞いに合わせて書き直すこと
- 用語集に「見送った改善項目」と「見送った提案」を足すこと

含まない:
- `init` より後（提案・実装・push の直前）で Pull Request の状態を確かめ直すこと。`init` から push までの間に閉じられた・マージされた Pull Request は扱わない
- Pull Request が Draft かどうかを確かめること
- `assess` で Pull Request の状態を見ること（`assess` は Pull Request の番号を受けない）
- cross-review の入口で同じ検査をすること
- 結果 JSON の `metrics` のキーを足す・意味を変えること
- リファクタリング計画のコメントの件数の行と「見送った提案」の節の形を変えること（既に `ledger.tally` と `deferred_items` から出ている）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 駆動が `refactor.py init` を呼んだ | 利用者か工程が `drive.py <PR>` を打った | `init` が 0 以外で終わると駆動は中断（終了コード 1・`metrics.exit` に元の終了コード）で終わる | — |
| E2 | `init` が Pull Request の応答を読んだ | E1 | 応答を読めなければ既存どおり終了コード 4 で止まる | E1 |
| E3 | `init` が Pull Request の状態を判定した | E2 | 閉じている・マージ済み・判定できないなら、終了コード 4 で止まる（受け入れ条件 1・2・5・6） | E2。新しい実行と再開で同じ判定（前提 1） |
| E4 | `init` が作業ディレクトリと状態ファイルを用意した | E3 で続けると判定した | 既存どおり | E3（止まるなら E4 は起きない） |
| E5 | リファクタリング計画が提案を見送った | 予算・順位・重複などの理由で計画に入れなかった | — | E4 |
| E6 | 実装の取り込みか採り直しが改善項目を見送った | 時間内に実装を終えなかった・足したテストが落ちた | — | E4。計画が採った改善項目にだけ起きる |
| E7 | 駆動が結果 JSON の `metrics` を数えた | done・中断・最終ゲートの止まりの各出口 | — | E5・E6 の後 |
| E8 | 完了報告を出した | `refactor.py report` | — | E7 と同じ状態ファイルを読む |

## 用語

| 用語 | 意味 |
| --- | --- |
| 見送った改善項目 | リファクタリング計画が採った改善項目のうち、表示の状態が「見送り」のもの（時間内に実装を終えなかった `not_done`・足したテストが落ちた `test_failed`）。結果 JSON の `metrics.deferred` の数。`not_done` が指す項目は #1743 の決定 9・10 で「実装の終わりまでにコミットが無く、採り直しでも残った時間に入らなかった」へ変わる |
| 見送った提案 | 計画に入らなかった提案と、見送った改善項目を合わせたもの。理由（`budget` / `rank` / `duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done`）を 1 つ持つ。状態ファイルの `deferred_items` |

## 受け入れ条件

入口の検査（#1658）:

- [ ] 1. 前提: 状態ファイルの無い新しい実行で、Pull Request の応答が `state: closed`・`merged_at: null`
      操作: `refactor.py init <PR> --scope ...`
      結果: 終了コード 4 で止まり、標準エラーに Pull Request の番号と「閉じている」が出る。作業ディレクトリ（`work`）も状態ファイルも作られない
- [ ] 2. 前提: Pull Request の応答が `state: closed`・`merged_at` に時刻がある
      操作: 1 と同じ
      結果: 終了コード 4 で止まり、標準エラーに Pull Request の番号と「マージ済み」が出る。作業ディレクトリも状態ファイルも作られない
- [ ] 3. 前提: 状態ファイルの無い新しい実行で、Pull Request の応答が `state: open`・`draft: false`（または `draft` が無い・真偽値でない）
      操作: 1 と同じ
      結果: 止まらずに作業ディレクトリと状態ファイルを用意して続ける（`draft` の値は判定に使わない）
- [ ] 4. 前提: Pull Request の応答が `state: open`・`draft: true`
      操作: 1 と同じ
      結果: 今までどおり作業ディレクトリと状態ファイルを用意して続ける（既存のテストがすべて通る）
- [ ] 5. 前提: 終わっていない状態ファイルがある（再開）
      操作: 1 と同じ
      結果: 応答が `state: open` なら `draft` の値にかかわらず続ける。`state: closed` なら 1・2 と同じく終了コード 4 で止まり、状態ファイルは書き換わらない
- [ ] 6. 前提: 応答の `state` が `open` / `closed` のどちらでもない（項目が無い場合を含む）
      操作: 1 と同じ
      結果: 終了コード 4 で止まり、標準エラーに「Pull Request の状態を判定できない」と読めなかった項目名が出る（続けない）
- [ ] 7. 1・2・6 のどれかで止まったとき、`drive.py <PR>` は終了コード 1・結果 JSON の `metrics.exit` 4 で終わり、担当の CLI を 1 つも起動せず、push しない
- [ ] 8. Pull Request の状態の判定は、既存の `repos/{repo}/pulls/{pr}` の応答 1 回から行う。`init` が打つ `gh` の呼び出しの回数は変更の前と同じである
- [ ] 9. `scripts/lib/drive_pause.py` に差分が無い（`git diff --stat` に載らない）。cross-review の駆動のテストが変更の前と同じく通る

件数の整合（#1660）:

- [ ] 10. 前提: 状態ファイルに、見送った改善項目 1 件（理由 `not_done`）と、計画に入らなかった提案 2 件（理由 `budget` と `duplicate`）がある
      操作: 駆動の結果 JSON を組み、`refactor.py report <ID>` を打ち、リファクタリング計画のコメントを組む
      結果: 結果 JSON の `metrics.deferred`・完了報告の件数の行の「見送り」・計画のコメントの件数の行の「見送り」がすべて 1 になる
- [ ] 11. 10 と同じ状態で、完了報告は「見送った提案」の総数 3 と理由別の件数（`budget` 1・`duplicate` 1・`not_done` 1、他は 0）を、件数の行の「見送り」と別の行に、別の語で出す。理由別の件数の和は総数と等しい
- [ ] 12. 10 と同じ状態で、完了報告の理由別の `not_done` と `test_failed` の和は `metrics.deferred` と等しい
- [ ] 13. 見送りの件数を数える処理は `ledger.tally` の 1 か所だけである。完了報告の件数の行が `deferred_items` の長さを使わない
- [ ] 14. 結果 JSON の `metrics` のキーの集合と、それぞれの値の意味が変更の前と同じである（既存の駆動のテストが変更なしで通る）
- [ ] 15. `SKILL.md` の `metrics` の説明と完了報告の節が「見送った改善項目」と「見送った提案」を書き分け、用語集にこの 2 語があり、`glossary.py check` がこの写しで当たり 0 件になる

退行しないこと:

- [ ] 16. `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/cross-refactoring plugins/ndf/skills/cross-review plugins/ndf/scripts/tests/test_drive_refactor.py plugins/ndf/scripts/tests/test_drive_review.py -q -n 4` が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `init` の GitHub API の呼び出しを増やさない（受け入れ条件 8） |
| 運用・保守性 | 止まった理由（Pull Request の番号・満たさなかった条件・直し方）が標準エラーの 1 行で読める（受け入れ条件 1・2・6） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。終了コードと結果 JSON の形・`metrics` のキーは同じ。受け付ける Pull Request が開いているものに狭まる。Draft かどうかは問わないため、工程（`development-workflow`）からの起動も Draft を外した Pull Request への起動も止まらない |
| データ | 状態ファイルの形は変わらない |
| 既存の振る舞い | 閉じた・マージ済みの Pull Request で `init` が止まる。完了報告の件数の行の「見送り」の数が、計画に入らなかった提案を含まない数になる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/cross-refactoring plugins/ndf/skills/cross-review plugins/ndf/scripts/tests/test_drive_refactor.py plugins/ndf/scripts/tests/test_drive_review.py -q -n 4` |
| 静的解析・型検査 | リポジトリの CI と同じ（`ruff` ほか。`.ndf/project.json` の宣言） |
| 用語 | `python3 plugins/ndf/scripts/glossary.py check --file issues/issue-1658-requirements.md` |
| 手動確認 | リリース後テストで、マージ済みの Pull Request の番号で `drive.py` を打ち、終了コード 1・`metrics.exit` 4 と「マージ済み」の理由が出て、作業ディレクトリが作られないことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 入口の検査は cross-refactoring の `refactor_lib` の中に置き、共通ライブラリ（`plugins/ndf/scripts/lib/`）の終了コードの表に触れない。件数は `ledger.tally` が持つ（`AGENTS.md`・Value 6 の同じ役割を分けない） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストを書かない |
| テスト戦略 | `gh` の応答を差し替えた単体テストで受け入れ条件 1〜8 を、状態ファイルの組み立てで 10〜14 を確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | cross-refactoring と cross-review のテストの実行、`check-skill-frontmatter.py`・用語集の検査 |
| 確認してから行う | 共通ライブラリの終了コードの表と `metrics` のキーの変更（この要求では行わない） |
| 行わない | `init` 以外の地点での Pull Request の状態の検査、Draft の検査、cross-review の入口の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `init` から push までの間に Pull Request が閉じられた・マージされたときに push を止めるか（この要求では扱わない） | 利用者（必要なら別の課題として起票する） | この要求の設計の承認まで |

## 起票時の記録

### 何を見つけたか

SKILL.md は前提として「対象の Pull Request が Draft で開いている」と書くが、実装はどこでもこれを確かめない。init が読む `pulls/{pr}` の応答の `state` / `draft` を見ておらず、`refactor_lib` 全体に draft の語が 1 件も無い。閉じた PR・マージ済みの PR・Draft でない PR を渡しても、提案・実装・push まで進む。

### どこで見つけたか

- `plugins/ndf/skills/cross-refactoring/SKILL.md` 129（前提の「Draft で開いている」）
- `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/setup.py:217-266`（`_pr_payload` と PR の文脈の取得。`state` / `draft` を読まない）
- `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/`（`git grep -i draft` で 0 件）

### なぜこの変更の範囲外なのか

スプリント m725（#725・#1289・#842・#824）の受け入れ条件に含まれない。 確かめる場所と、満たさないときの止め方（終了コードの表のどの値で止めるか）を決める必要があり、終了コードの表（共通ライブラリの `drive_pause.py`、cross-review と共有）に触れうるため、即時修正の 4 条件のうち「既存の約束事を壊さない」を満たさない。根拠: Value 4（判断の要らない検査はスクリプトが進める。前提の確認を LLM の読み手に任せない）。

### 直さないと何が起きるか

マージ済みの PR の head ブランチへ改善のコミットが push される、レビュー中の（Draft でない）PR へ予告なく大量のコミットが積まれる、など、利用者が想定しない場所へ変更が出る。時間と費用もそのまま消費する。

### 取り込んだ課題

- #1660（見送りの件数が駆動の要約 `metrics.deferred` と完了報告で食い違う）。受け入れ条件 10〜15 が #1660 の分である

### 由来

issue #725（スプリント m725。cross-refactoring の SKILL.md の点検、2026-10-03 利用者の依頼）
