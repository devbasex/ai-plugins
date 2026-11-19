## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 文言の直しに加え、止まったときに記録を持つ PR を示す | 2026-11-19 の conductor の実行で、止まった後に正しい PR を探す手間が出た | 受け入れ条件 2・3 |
| S2 | `release` の SKILL.md の手順 3（版を上げる形では「リリースの Pull Request の本文へ `## リリース記録` を置く」）が、読み手にリリースの PR を選ばせる | 本文の「事象」の 2 段落目 | 受け入れ条件 1（手順 3 に workflow の形を除くと書く） |

## 目的

- CD の形（workflow でリリースする形）のリポジトリで、リリース後テストの記録を書く手順が、記録を置く PR を取り違えずに 1 回で通る
- 取り違えて止まったときも、止まった結果から記録を持つ PR が分かる

## 事象

`release-verification` の Skill（`plugins/ndf/skills/release-verification/SKILL.md` の「出力物」）は、記録を置く場所を「`release` が『リリース記録』を置いた Pull Request」と単数で書き、`ndf release-steps verification-record --pr <リリース記録の PR>` を打たせる。

workflow でリリースする形（ai-plugins の CD の形）では、リリース記録はリリースの PR（`release/v<版>`）には無く、workflow の `record` が**含めた PR の各々**へコメントで書く（`release` の `references/release-steps.md` の「デプロイの後にリリース記録を置く」）。一方で `release` の SKILL.md の手順 3 は、版を上げる形では「リリースの Pull Request の本文へ `## リリース記録` を置く」と書く。読み手はリリースの PR を選びやすい。

11.0.8 のリリース後テストで、conductor がリリースの PR #3849 を渡し、`ndf release-steps verification-record --pr 3849` が `precondition`（3）で止まった。実物は #3849 に記録が無く、#3845・#3848 のコメント（`<!-- ndf:release-record -->`）にあった。

## 期待する結果

- `release-verification` の「出力物」が、形ごとに記録を置く PR を書き分ける（workflow の形では含めた PR。リリースの PR ではない）
- `precondition` で止まるときの出力が、同じバージョンのリリース記録を持つ PR の番号を示すか、持たないことを理由に書く

## 再現手順

1. `gh pr view 3849 --repo devbasex/ai-plugins --json body,comments` に `## リリース記録` が無い
2. `gh pr view 3845 --repo devbasex/ai-plugins --json comments` に `<!-- ndf:release-record -->` のコメントがある
3. `ndf release-steps verification-record --pr 3849 --record <記録の JSON>` が終了コード 3 で止まる（2026-11-19 の conductor の実行）

## 環境

NDF 11.0.8・Claude Code・Linux

## 前提

- 前提 1: リリースの PR は、本番ブランチ（ブランチ戦略の設定の `branches.production`）へマージ済みで、head がリリースブランチの書式（`release/v<版>`）に合う PR である。`record` が含めた PR を決めるのと同じ判定（`record.released`）で見分けられる
- 前提 2: `strict` の squash（PR を閉じて本番へ入れる形）のリリースの PR は、本番へ入れたコミットを渡さないと `released` が読めない。`verification-record` はそのコミットを受けないため、その形ではリリースの PR と見分けられず、受け入れ条件 3 の理由で止まる

## 対象範囲

含む:
- `release-verification` の「出力物」の記録を置く PR を、リリースの形ごとに書き分ける
- `release` の SKILL.md の手順 3 と「出力物」の「リリースの Pull Request の本文へ置く」に、workflow でリリースする形を除くと書く
- `ndf release-steps verification-record` が記録の無い PR で止まるときの `summary` と `items[]`

含まない:
- 記録を書く先の自動の選び直し（止まったときに別の PR へ書くこと）。書く先は打つ側が決める
- `strict` の squash のリリースの PR を `verification-record` で見分けること（前提 2）
- `record` がリリース記録を書く先（含めた PR の各々）を変えること

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | リリース記録を含めた PR の各々へ書いた | 本番ブランチへのマージの後の workflow の `record` | 書けなければ workflow が止まる（この変更の外） | リリースの PR をマージした |
| E2 | リリース後テストの記録を書く PR を選んだ | `release-verification` の「出力物」 | 記録を持たない PR を選ぶと E3 で止まる | E1 |
| E3 | 記録を持たない PR への書き込みを断った | `verification-record` が選んだ PR に同じバージョンのリリース記録を見つけない | — | E2 |
| E4 | 記録を持つ PR を示した | E3 で、選んだ PR が同じバージョンのリリースの PR だった | 含めた PR のどれも記録を持たなければ、持たないことを理由に書く | E3 |

## 用語

| 用語 | 意味 |
| --- | --- |
| リリース記録 | 用語集のとおり（`ndf-release`） |
| リリースの PR | 用語集のとおり（`ndf-release`） |
| リリース後テストの記録 | 用語集のとおり（`ndf-release`） |
| リリースの形 | 用語集のとおり（`ndf-release`） |

## 受け入れ条件

- [ ] `release-verification` の「出力物」が、記録を置く PR をリリースの形ごとに書き分ける（workflow でリリースする形では含めた PR のどれか、それ以外の形ではリリース記録を置いた PR）。`release` の SKILL.md の手順 3 と「出力物」の「リリースの Pull Request の本文へ置く」が、workflow でリリースする形を除くと書く
- [ ] `ndf release-steps verification-record --pr <リリースの PR>` で、そのリリースの PR のバージョンが記録の `version` と同じで、含めた PR のどれかが同じバージョンのリリース記録を持つとき、何も書かずに `precondition`（3）で止まり、`summary` に記録を持つ PR の番号を挙げ、`items[]` に `{"kind": "pull", "number": <番号>}` を 1 本ずつ置く
- [ ] 渡した PR がリリースの PR でない・バージョンが違う・含めた PR のどれも同じバージョンのリリース記録を持たないとき、何も書かずに `precondition`（3）で止まり、`summary` が「workflow でリリースする形ではリリース記録は含めた PR の各々にある」ことと、記録を持つ PR を示せない理由を書く
- [ ] 退行しない: 同じバージョンのリリース記録を持つ PR を渡したときは、今と同じく記録を書いて `ok` を返す。形の違う記録と信用されない書き手は、今と同じく `precondition` で止まる

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: `verification-record` の止まったときの `summary` と `items[]`。終了コードと `cause.kind`（`precondition`）は変わらない |
| データ | 変わらない |
| 既存の振る舞い | 記録の無い PR で止まるときの出力だけ |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/release -q -n 4` |
| 静的解析・型検査 | `uv run --frozen --project . --all-extras ruff check plugins/ndf/src` とリポジトリの CI |
| 手動確認 | 次のリリースのリリース後テストで、リリースの PR を渡して止まった結果に含めた PR の番号が出るかを見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更した文書の参照の確かめ |
| 確認してから行う | 結果 JSON の項目の追加（設計承認ゲートで見る） |
| 行わない | 記録を書く先の自動の選び直し |

## 由来

issue #3812 の通しの振り返り（仕上げのフェーズ）。リリース後テスト（PR #3845 のコメント https://github.com/devbasex/ai-plugins/pull/3845#issuecomment-7006962253 ）を書く途中で起きた。#3812 は中継のプラグインの更新で、`release-verification` の文言は範囲外。根拠: Value 14「判断の理由が残り、語がそろっている」（MVV バージョン 9）。

## リクエスト（原文）

> #3852 release-verification: CD の形ではリリース記録が含めた PR の各々にあり、リリースの PR を渡すと `ndf release-steps verification-record` が precondition(3) で止まる。文言の書き分けと、止まるときに記録を持つ PR を示す

（書き手: AI の起動指示（conductor、計画 p8-5 のレーン lclose）、日付: 2026-11-19。利用者の指示の原文として扱わない。事象・期待する結果・再現手順・環境・由来は起票者の本文のまま）
