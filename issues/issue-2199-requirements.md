# #2199: factory/ の構造改善の提案 20 件が時間で見送られ、子課題が同じ重複を写して広げる

正は課題の本文（#2199）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> #2199。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-10-31。利用者の指示の原文としては扱わない）

課題の本文（下の「起票時の記録」）は、#2170 の cross-refactoring（PR #2192）の後に起票者（AI）が書いた記録である。利用者が書いた文は含まれていない（書き手: 起票者、日付: 2026-10-30）。

## 起票時の記録

起票者の本文をそのまま引用する（書き換えない）。

> ### 何を見つけたか
>
> #2170 の検査（cross-refactoring、PR #2192）で、`factory/` の構造改善の提案 20 件が時間（`budget`）で見送られ、どの課題にも残っていない。リファクタリング計画（PR #2192 のコメントの「見送った提案」）にだけある。
>
> 主なもの（見積りの大きい順）:
>
> | 対象 | 兆候 | 見積り |
> | --- | --- | --- |
> | `factory/api/src/accounts/store.ts#DynamoAccountStore` | duplication | 10.3 分 |
> | `factory/api/src/routes/teams.ts#registerTeams` | duplication | 9.2 分 |
> | `factory/api/src/accounts/store.ts#seatItem` | duplication | 9.2 分 |
> | `factory/api/src/handlers/github-oidc.ts#requireEnv` | duplication | 9.2 分 |
> | `factory/api/src/accounts/service.ts#AccountService.hist` | long_parameter_list | 4.7 分 |
> | `factory/api/src/accounts/store.ts#AccountStore.updateAccount` | data_clump | 4.7 分 |
> | `factory/api/src/index.ts#depsFromEnv` | circular_dependency | 2.0 分 |
> | `factory/api/src/accounts/store.ts#AccountStore` | divergent_change | 2.0 分 |
>
> ほか 12 件（`github-oidc/app.ts`・`auth/verify.ts`・`routes/accounts.ts`・`web/src/pages/*.tsx`・`scripts/tests/test_check_markdown_links.py`）。全件の表は PR #2192 の cross-refactoring のコメントにある。
>
> ### どこで見つけたか
>
> PR #2192（https://github.com/devbasex/ai-plugins/pull/2192 ）の cross-refactoring のコメント「見送った提案」。件数の行は「採用 3・未確認 0・取り消し 0・見送り 20（うち時間 20）」。
>
> ### なぜこの変更の範囲外なのか
>
> 構造改善の工程の予算（`--budget-minutes`）を超える分で、#2170 の受け入れ条件はすべて満たしている。Value 10 は、上限を超えてまで行いたい品質の向上を別の Issue へ起こして後へ回すと定める。根拠: Value 10・Value 6（MVV 版 3）
>
> ### 直さないと何が起きるか
>
> #2171・#2186・#2173・#2172・#2174 が同じ `factory/api/` の `store.ts`・`service.ts`・`routes/*.ts` に足していくため、重複（席の行の組み立て・トランザクション・ルートの登録）が子課題ごとに写されて広がる。後の子課題の cross-refactoring が同じ提案を毎回出し直し、その都度予算で見送る。
>
> ### 由来
>
> PR #2192

## 見送った提案の全件（PR #2192 のコメントから写した）

| # | 対象 | 兆候 | 見積り |
| --- | --- | --- | --- |
| R01 | `factory/api/src/routes/teams.ts#registerTeams` | duplication | 9.2 分 |
| R02 | `factory/api/src/index.ts#depsFromEnv` | circular_dependency | 2.0 分 |
| R03 | `factory/api/src/github-oidc/app.ts#githubJson` | duplication | 1.4 分 |
| R04 | `factory/api/src/accounts/store.ts#DynamoAccountStore.transact` | duplication | 2.3 分 |
| R05 | `factory/web/src/pages/TeamPage.tsx#TeamPage` | duplication | 2.5 分 |
| R06 | `factory/api/src/accounts/service.ts#AccountService.setExecutionMode` | duplication | 1.4 分 |
| R07 | `factory/api/src/github-oidc/app.ts#createGithubOidcApp (POST /token handler)` | long_method | 1.4 分 |
| R08 | `factory/api/src/accounts/store.ts#AccountStore` | divergent_change | 2.0 分 |
| R09 | `factory/api/src/accounts/store.ts#DynamoAccountStore` | duplication | 10.3 分 |
| R10 | `factory/api/src/accounts/store.ts#DynamoAccountStore.updateAccount` | conditional_chain | 1.3 分 |
| R11 | `factory/api/src/accounts/service.ts#AccountService.addSeat` | duplication | 1.4 分 |
| R12 | `factory/api/src/auth/verify.ts#verifyAuthorization` | long_method | 1.4 分 |
| R13 | `factory/api/src/routes/accounts.ts#registerAccounts` | duplication | 1.4 分 |
| R14 | `factory/web/src/pages/AccountPage.tsx#AccountPage` | duplication | 1.4 分 |
| R15 | `factory/web/src/pages/TeamPage.tsx#TeamPage.act / AccountPage / ExecutionModeView / NewTeamPage` | duplication | 1.4 分 |
| R16 | `scripts/tests/test_check_markdown_links.py#test_link_targets_extracts_html_and_excludes_images` | duplication | 1.9 分 |
| R17 | `factory/api/src/accounts/service.ts#AccountService.hist` | long_parameter_list | 4.7 分 |
| R18 | `factory/api/src/accounts/store.ts#AccountStore.updateAccount` | data_clump | 4.7 分 |
| R19 | `factory/api/src/accounts/store.ts#seatItem` | duplication | 9.2 分 |
| R20 | `factory/api/src/handlers/github-oidc.ts#requireEnv` | duplication | 9.2 分 |

起票の後に重複が広がっている。2026-10-31 の `develop`（ee32e92f）では、`requireEnv` が 4 か所（`factory/api/src/handlers/github-oidc.ts`・`handlers/api.ts`・`handlers/ingest.ts`・`runs/deps.ts`）にある。PR #2192 の時点では `runs/` と `handlers/ingest.ts` はまだ無かった。

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 子課題が `factory/api/` の同じファイルへ足していき、重複が広がる | `docs/specifications/ndf-factory-service-boundary.md` の子課題の受け持ちと、#2170 の「提供する契約」 | 前提 1（2026-10-31 の `develop` で `requireEnv` が 4 か所に増えたことで確かめた） |
| S2 | 着手は #2171 の実装の前が効く | 起票者の推定。棚卸しで順位を決める | 含まない（着手の順位は棚卸し `backlog-refinement` が決める。未決 1） |
| S3 | 20 件の対象と同じ役割の写しが、PR #2192 の後に足されたファイルにもあれば、同じ変更で 1 つへ寄せる | 要求を書いた AI の推定（2026-10-31）。Value 6「同じ役割の定数・関数を分けない」と、本文の「子課題ごとに写されて広がる」 | 受け入れ条件 3 |
| S4 | 20 件のどれも、見送りの理由を「時間（`budget`）」にしない（適用するか、理由を添えて採らないかのどちらかに決める） | 要求を書いた AI の推定（2026-10-31）。本文の「その都度予算で見送る」を止めるため | 受け入れ条件 1・2 |
| S5 | 振る舞いを変えない（HTTP の応答・OpenAPI の契約・DynamoDB の行の形を変えない） | 要求を書いた AI の推定（2026-10-31）。リファクタリング（リファクタリング）の定義と、C2・C3 に当たる箇所を含むこと | 受け入れ条件 4〜7 |

## 目的

- `factory/` で見送ったリファクタリングの提案 20 件を片付け、後の子課題（#2171・#2172・#2173・#2174）が重複を写して広げないようにする
- 後の子課題の cross-refactoring が、同じ提案を出し直して時間で見送ることを止める

## 前提

- 前提 1: 20 件の対象の一部は、PR #2192 の後の変更（#2186 などのマージ）で形が変わっているか、すでに直っている。実装の最初に、20 件のそれぞれが今の `develop` に残っているかを確かめる
- 前提 2: R07（`createGithubOidcApp` の POST /token）は PR #2192 の I-001 で同じ箇所を抽出済みで、残りがあるかは前提 1 の確かめで決める
- 前提 3: 振る舞いの基準は、実装を始める時点の `develop` の既存テスト（`factory/` の vitest と `scripts/tests/test_check_markdown_links.py`）である。テストの期待値は変えない（テストの重複を直す R16 は、テストの関数の数と確かめる入力・期待値の組を減らさない）
- 前提 4: 外部 AI の提案ごとの手法（extract_method など）は手がかりであり、同じ兆候をより小さい変更で消せるならその手法を採ってよい

## 対象範囲

含む:
- 上の表の R01〜R20 の 20 件
- R01〜R20 と同じ役割の写しのうち、PR #2192 の後に足されたファイル（`factory/api/src/runs/`・`handlers/ingest.ts` など）にあるもの（S3）

含まない:
- 振る舞いの変更（API の応答・エラーの文言・OpenAPI の契約・DynamoDB のキーと属性の形・画面の表示）
- R01〜R20 と同じ役割の写しでない、新しく見つかったリファクタリングの提案（見つけたら `out-of-scope` で別の課題へ起こす）
- `factory/runner/`・`factory/contracts/` のリファクタリング
- 着手の順位を決めること（S2。棚卸しが決める）
- 依存パッケージの追加

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 20 件のそれぞれが今の `develop` に残っているかを確かめた | 実装の開始 | 対象の関数が無い・形が変わって判断できない → その件を「採らない（対象が無い）」か、今の形で同じ兆候があるかを書く | — |
| E2 | 同じ役割の写しを PR #2192 の後のファイルから拾った | E1 の後 | 写しかどうか決められない → 含めずに、理由を記録の表に書く | E1 |
| E3 | 着手前のテストが通った | E1 の後 | 既存の失敗がある → 直さずに件数を記録し、その失敗を基準から外す | — |
| E4 | 各件を適用したか、採らないと決めた | E1〜E3 | 適用してテストが落ちた → その件を戻し、採らない理由（テストが落ちた理由）を書く | E3 |
| E5 | 全体の検査（`npm --prefix factory run check` と範囲の pytest）が通った | E4 の後 | 落ちた → 原因の件を戻すか直す | E4 |
| E6 | 20 件と写しの扱いを 1 つの表として Pull Request の本文に残した | E5 の後 | 表の件数が 20 と合わない → 欠けた件を足すまで Pull Request を出さない | E4・E5 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 見送った提案 | cross-refactoring のリファクタリング計画のコメントの「見送った提案」の表の 1 行。ここでは PR #2192 の 20 件（R01〜R20） |
| リファクタリング | 外から見た振る舞いを変えずに構造を変えること |

## 受け入れ条件

- [ ] 1. 実装の Pull Request の本文に、R01〜R20 の 20 行がそろった表があり、各行の扱いが「適用した」か「採らない」のどちらかである
- [ ] 2. 表の「採らない」の行には理由があり、理由は次のどれかである: 対象が今の `develop` に無い / 兆候がすでに無い / 適用するとテストが落ちる / 適用すると振る舞いが変わる。「時間（`budget`）」を理由にした行は 0 行である
- [ ] 3. `factory/api/src` で `function requireEnv` の定義は 1 か所だけである（`grep -rn "function requireEnv" factory/api/src` が 1 行を返す）。R01〜R20 のほかの「適用した」重複についても、同じ役割の関数の定義が `factory/` の中で 1 か所にまとまり、表の行にその場所が書かれている
- [ ] 4. `npm --prefix factory ci --no-audit --no-fund --silent && npm --prefix factory run check` が終了コード 0 で終わる（契約の lint・型検査・vitest を含む）
- [ ] 5. `uv run --frozen --project . --all-extras pytest scripts/tests/test_check_markdown_links.py -q` が終了コード 0 で終わり、通ったテストの件数が変更の前と同じかそれ以上である
- [ ] 6. 実装の差分に、`factory/` の既存のテストファイル（`*.test.ts` / `*.test.tsx`）の期待値（`expect(...)` の右辺）を変える行が無い。テストを動かした・まとめた行があれば、表の行に理由が書かれている
- [ ] 7. 実装の差分に、`factory/contracts/` の変更が無い
- [ ] 8. 起きてはいけないこと: `factory/` の外のコード（`scripts/tests/test_check_markdown_links.py` を除く）と、`plugins/` に差分が無い

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 受け入れ条件 3 のとおり、同じ役割の関数の定義を 1 か所にする |
| セキュリティ | 認証（`auth/verify.ts`・`github-oidc/`）と認可（`auth/resolve.ts`）の振る舞いを変えない。受け入れ条件 4 の既存テストが通ることで確かめ、差分は承認ゲート 2 で人が見る（C2） |
| 移行性 | DynamoDB のキーと属性の形を変えない。`store.test.ts` の既存テストが通ることで確かめる（C3 に当たる変更を含まない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（HTTP API・OpenAPI の契約・画面） |
| データ | 変わらない（スキーマの変更も移行も無い） |
| 既存の振る舞い | 変わらない。変わるのは `factory/api/src`・`factory/web/src` の内部の関数の置き場と引数の形、`scripts/tests/test_check_markdown_links.py` のテストの書き方 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト（factory） | `npm --prefix factory ci --no-audit --no-fund --silent && npm --prefix factory run check` |
| テスト（R16） | `uv run --frozen --project . --all-extras pytest scripts/tests/test_check_markdown_links.py -q` |
| 静的解析・型検査 | `npm --prefix factory run check` の `tsc --noEmit`（api・web）と `spectral lint` |
| 重複の確かめ | `grep -rn "function requireEnv" factory/api/src`（1 行）。ほかの重複は表の行に書いた場所で確かめる |
| 範囲の確かめ | `git diff --stat origin/develop...HEAD`（受け入れ条件 7・8） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `factory/` はプラグインと分ける。`plugins/` と `.claude-plugin/marketplace.json` から `factory/` を参照しない（`AGENTS.md` の「マーケットプレイスの構造」、`python3 scripts/check-service-boundary.py`） |
| コーディング規約 | `factory/` は TypeScript の `tsc --noEmit` と既存の書き方に合わせる。`AGENTS.md` の「最小限のコード実装」 |
| テスト戦略 | 既存の単体テスト（vitest・pytest）を振る舞いの基準にし、新しいテストは足さない（足すのは、抽出した共通の関数に既存のテストが届かないときだけ） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 着手前と各件の適用の後に既存テストを流す。テストが落ちた件は戻す |
| 確認してから行う | 認証・認可（`auth/`・`github-oidc/`）とデータの書き込み（`accounts/store.ts`）の差分は、承認ゲート 2 で人が見る |
| 行わない | 振る舞いの変更、テストの期待値の書き換え、`factory/contracts/` と `plugins/` の変更、依存パッケージの追加、R01〜R20 と写しの外のリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 着手の順位（#2171 の実装より前に行うか） | 棚卸し（`backlog-refinement`）か利用者 | 次の棚卸し |
| 2. 実装を cross-refactoring で行うか、計画どおりに直接行うか。cross-refactoring で行う場合の予算（`--budget-minutes`） | `implementation-plan` | 実装計画の作成時 |
