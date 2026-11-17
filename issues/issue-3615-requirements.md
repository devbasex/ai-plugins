# #3615: ndf-next のリリース: プラグインのリリースの形でしか動かない（plugin.json・marketplace.json 決め打ち）。設定したリリースの形で選び、今の ndf の 11 種と同等にする

正は課題の本文（#3615）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（並列の計画 p6・p8 のオーガナイザー）が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。利用者の原文は 2026-11-16 の 1 文だけで（末尾の原文のセクション）、範囲と受け入れ条件はどれも起票者が足した。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は、リリースの部分を ai-plugins の運用（プラグインのリリース）で試しながら作り、リリースの形の分岐が移されなかったこと | 本文の旧「起票者の推定」 | 前提 7（原因の記述として残し、受け入れ条件にしない） |
| S2 | 今の NDF の release Skill のリリースの形は 11 種 | 本文の「目的」と受け入れ条件 1 | 前提 1（数え直すと 9 種。受け入れ条件 1 は 9 種で判定する） |
| S3 | バージョンの保存先として `package.json`・`pubspec.yaml`・タグだけ を受ける | 本文の「対象範囲」 | 受け入れ条件 3・4 |
| S4 | リリースを利用者のリポジトリから起動する経路は workflow のテンプレートか会話のセッション | 本文の「対象範囲」・#3572 の決定 22 の境の表 | 受け入れ条件 8・9 |
| S5 | リモート名の部品への置き換えと `ORIGIN` の削除（#3617 の決定 5 からの引き継ぎ） | 本文の受け入れ条件 6 | 受け入れ条件 11 |
| S6 | trunk 系の設定で差分の範囲を前のバージョンのタグから集める（#3734 の cross-review で見送った指摘） | 本文の受け入れ条件 7 | 受け入れ条件 12 |
| S7 | 代表のリポジトリ 3 種での振る舞いの表と「ai-plugins だけの事情」の欄を設計の「適用範囲」に書く | 本文の「進め方の決まり」 | 受け入れ条件 2 |
| S8 | #1142 の子の表と `children.json` へこの課題を足す | 本文の「進め方の決まり」 | 受け入れ条件 14 |

## 目的

ndf-next のリリースの部分（`plugins/ndf-next/src/ndf/domains/release/`）を、Claude Code のプラグインのリリースの形でしか動かない状態から、利用者のリポジトリが設定したリリースの形で動く状態にする。対象は Web サービス・モバイルアプリ・業務文書だけのリポジトリを含む。今の NDF の release Skill が持つリリースの形の扱いを ndf-next に移し、#1142 の受け入れ条件「現行と同等」（同等は既知の不具合を引き継がない）を満たす。

## 点検で見つかった箇所

2026-11-16 の develop の時点で点検した。2026-11-17 の main（207803ced）で読み直した結果を右の列に書く。

| # | 場所 | ai-plugins の前提 | 汎用のリポジトリで起きること | 2026-11-17 の main |
| --- | --- | --- | --- | --- |
| 1 | `domains/release/gather.py:88`・`assemble.py:32`・`tagging.py:21`・`cli.py:341` | リリースの単位をマーケットプレイスの登録から求め、バージョンのSSOTを `plugin.json` にする | `.claude-plugin` が無いリポジトリでは `PluginsError` が原因 `precondition` になり、承認資料・組み立て・タグのどれも動かない | 3 ファイルが今も `plugins` を読む。バージョンアップ（`bump`）は #3572 でバージョンの保存先のアダプター（`version_store` の `plugin`・`json`・`none`）へ移った |
| 2 | `domains/release/__init__.py`・`.github/workflows/ndf-next-release.yml` | リリースを実行する場が ai-plugins の workflow だけ（秘密 `NDF_RELEASE_TOKEN`・変数 `NDF_RELEASE_RUNNER`） | 利用者のリポジトリに ndf-next のリリースを起動する経路が無い | 変わらない |
| 8 | `domains/release/changes_decide.py:22` | リリース対象外のファイルの既定が `tests/`・`test_*.py`・`conftest.py` だけ | `*.test.ts`・`__tests__/`・`*Test.php` をリリース対象のファイルと数え、テストだけの変更でバージョンが上がる | 変わらない。`contracts/domains/test-files.json` は 24 行の形を持つが、リリースの部分は読んでいない |
| 9 | `version_decide.py:31-33`・`changelog_decide.py` | タグ `<プラグイン>--v<バージョン>`・リリースブランチ `release/v<バージョン>`・CHANGELOG の見出しの形が固定 | `v1.2.3` や Keep a Changelog の `## [1.2.3]` と合わない | 設定の `release.tag`（例 `{plugin}--v{version}`）はスキーマにあるが、リリースの部分は読まず、`TAG_SEPARATOR = "--v"` を使う |

## 前提

- 前提 1: 今の NDF の release Skill のリリースの形は 9 種である（`plugins/ndf/skills/release/SKILL.md` の「リリースの形」の表の 9 行と、`references/form-*.md` の 9 ファイル: パッケージ・プラグイン / サービス / デスクトップアプリ / モバイルアプリ / 手順・設定 / スライド / 文書 / 表計算 / ページ）。本文の旧記述の「11 種」は、この 9 種を指すものとして読む
- 前提 2: リリースの形とリリースの形の設定は、#3681 が入れた `.ndf/project.json` のセクション `branch_strategy` の `release` の 1 か所に置く。今の値は `form`（`service`・`mobile`・`document`・`package-plugin` の 4 値）・`version_store`・`tag`・`github_release`・`cd`・`deliveries` である。新しいキーもこのセクションに足し、別のファイルを作らない
- 前提 3: バージョンの保存先のアダプターのインタフェース・設定のキー `version_store`・アダプター `plugin`・`json`・`none` は #3572 の決定 22 が持つ。この課題はそのインタフェースへアダプターを足すだけで、インタフェースを変えない。#3572 の決定 7・9・21・22 と食い違う決め方をしない
- 前提 4: ndf-next は未リリースなので、設定のキー・`release-steps` の出力を変えても互換の経路を持たない（MVV Value 4）
- 前提 5: ai-plugins 自身の設定は今の値（`form: package-plugin`・`version_store.kind: plugin`・`tag: {plugin}--v{version}`）のまま変えない。ai-plugins の固有の値はコードの既定ではなく ai-plugins の `.ndf/project.json` に置く
- 前提 6: 「nd-factory の設定のコピー」は、`/work/nd-factory/.ndf/` の今の値（起点 `qa/dev`・本番 `main`・`promotion: selected`・CD の無い手動の反映・バージョンを持たない）を `branch_strategy` の形へ移したテスト用の設定を指す。nd-factory のリポジトリそのものは書き換えない
- 前提 7: 後退の原因は、リリースの部分を ai-plugins の運用で試しながら作り、試走 T2（#3411）も ai-plugins でしか行わなかったことである。再発を止める検査は「ndf-next への ai-plugins 固有の前提の混入を検査で止める」の Issue（#3617）が持ち、この課題では作らない
- 前提 8: 文書の 4 形（スライド・文書・表計算・ページ）の生成と提出の手順は置き先のシステム（`document-systems`）が持ち、ndf-next のリリースの部分に移すかは受け入れ条件 1 の表で決める。移さない形は、11.x へ回す理由を表に書けば受け入れ条件 1 を満たす

## 対象範囲

含む:
- リリースの部分が設定の `release.form`・`deliveries` でリリースの形を選び、承認資料・リリースブランチの組み立て・タグをプラグインの登録が無いリポジトリでも組むこと。プラグインの形はアダプターの 1 つにする
- バージョンの保存先のアダプターのうち、#3572 が作らないもの（`pubspec.yaml`・タグだけ）
- タグ・CHANGELOG の見出し・リリースブランチの名前の書式を設定で変えること
- リリース対象外のファイルの既定を `contracts/domains/test-files.json` から作ること
- リリースを利用者のリポジトリから起動する経路（workflow のテンプレートと手順の文書）
- リリースの部品のリモート名をリモート名の部品（`adapters/git.py` の `remote_of`）へ替えること
- trunk 系の設定で、差分の範囲を前のバージョンのタグから集めること

含まない:
- ai-plugins 自身の設定の値（前提 5）
- バージョンを上げる時点・主体とキューとの関係（#3572 の決定 7・20）
- ai-plugins 固有の前提の混入を止める検査（#3617）
- 語の置き換え（#3613 が済ませた語に合わせるだけ）
- Swift・Gradle などほかのバージョンの保存先のアダプター（受け入れ条件 1 の表で 11.x へ回す理由を書く）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 設定を読んだ | `release-steps` の各サブコマンドの起動 | セクションが無い・スキーマに落ちるときは `precondition` で止まり、直す場所を出す | — |
| E2 | リリースの形とバージョンの保存先を選んだ | E1 | 形に対応するアダプターが無いときは止まり、形の名前と 11.x へ回した理由の在りかを出す | E1 |
| E3 | 前のバージョンの起点を決めた | E2 | 前のバージョンのタグが無いとき（初めてのリリース）は本番ブランチの最初のコミットからとする（前提ではなく受け入れ条件 12 で判定） | E2 |
| E4 | 差分の範囲のファイルを、リリース対象と配らないものに分けた | E3 | テストのファイルの表を読めないときは `unreadable` で止まる | E3 |
| E5 | 次のバージョンを決めた | E4 | リリース対象のファイルの変化が無ければバージョンを上げない | E4 |
| E6 | 承認資料を組んだ | E5 | — | E5 |
| E7 | リリースブランチを作り、バージョンと CHANGELOG のセクションを書いた | 人の承認の後の `assemble` | 同じ名前のブランチが先にあれば作らずに止まる | E6 |
| E8 | タグを打った | `publish`（CD か手元） | 同じ名前のタグが別のコミットを指していれば止まる | E7 とマージ |
| E9 | 利用者のリポジトリの workflow がリリースを起動した | 利用者が置いたテンプレートの workflow | 秘密が無いときに既定のトークンへ倒すか止まるかは設計で決める（未決 2） | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| リリースの形 | 利用者へ届く仕組みの種類。設定の `release.form` の値。今の NDF の release Skill の「リリースの形」に当たる |
| バージョンの保存先 | バージョンを書く場所（`plugin.json`・`package.json`・`pubspec.yaml`・タグだけ など）。設定の `release.version_store` |
| アダプター | リリースの形かバージョンの保存先ごとに違う処理を、同じインタフェースの裏に置いた部品 |
| 名前の書式 | タグ・CHANGELOG の見出し・リリースブランチの名前を、バージョン（と必要ならプラグインの名前）から作る書式 |
| リリース対象外のファイル | バージョンを上げるかの判定で数えないファイル（テストなど） |

## 受け入れ条件

- [ ] 1. 今の NDF の release Skill のリリースの形 9 種（前提 1）のそれぞれについて、ndf-next での扱い（アダプターがある / 設定で受ける / 11.x へ回す理由）を 1 行に 1 種で書いた表が設計文書にある
- [ ] 2. 設計文書の「適用範囲」に、Web サービス（TypeScript・vitest か jest・develop→main・OpenAPI）・モバイルアプリ（Flutter か Swift）・業務文書だけのリポジトリの 3 種で、直した後に `release-steps materials`・`assemble`・`publish` が何をするかの表と、「ai-plugins だけの事情」の欄がある
- [ ] 3. `.claude-plugin` の無いリポジトリ（前提 6 の nd-factory の設定のコピー）で、`release-steps materials` が `precondition` で止まらず、設定のリリースの形で承認資料を組む（テスト）
- [ ] 4. `.claude-plugin` の無いリポジトリで、バージョンの保存先が `json`（`package.json`）・`pubspec.yaml`・タグだけ のそれぞれについて、`materials` が前のバージョンと次のバージョンを出し、`assemble` が保存先のバージョンを書き換える（タグだけ は書き換えるファイルが無い）（テスト）
- [ ] 5. タグの名前の書式を設定の `release.tag` から作る。`release.tag` が `v{version}` の設定で、バージョン 1.2.3 のタグの名前は `v1.2.3` になり、前のバージョンのタグを `v1.2.2` から読む（テスト）
- [ ] 6. CHANGELOG の見出しとリリースブランチの名前の書式を設定で変えられる。Keep a Changelog の形（`## [1.2.3] - <日付>`）の設定で、その形のセクションを足し、前のセクションを読める（テスト）
- [ ] 7. 名前の書式の設定が無いときは、今の形（タグ `<プラグイン>--v<バージョン>`・リリースブランチ `release/v<バージョン>`・今の CHANGELOG の見出し）になり、ai-plugins の設定での `materials` の出力が直す前と同じになる（テスト）
- [ ] 8. 差分の範囲が `src/foo.test.ts`・`src/__tests__/bar.ts`・`tests/Unit/FooTest.php` の変更だけのとき、バージョンが上がらない。リリース対象外のファイルの既定は `contracts/domains/test-files.json` の行から作り、設定の `undistributed` で足せる（テスト）
- [ ] 9. 利用者のリポジトリへ置く workflow のテンプレートがあり、テンプレートと手順の文書に ai-plugins の秘密・変数の名前（`NDF_RELEASE_TOKEN`・`NDF_RELEASE_RUNNER`）が 0 件である（`rg` の結果を設計か PR に添える）
- [ ] 10. 利用者のリポジトリから ndf-next のリリースを起動する手順が文書にあり、テンプレートの置き方・要る権限・秘密の名前の決め方を書いている
- [ ] 11. `domains/release/gather.py`・`assemble.py` の `git.ORIGIN` をリモート名の部品（`adapters/git.py` の `remote_of`）の呼び出しへ替え、`adapters/git.py` の `ORIGIN`（`__getattr__` の分岐）を消す。`rg -n "ORIGIN" plugins/ndf-next/src` が 0 件である
- [ ] 12. base と production が同じ設定（trunk 系の `plugin`・`web-trunk`・`docs`）で、`materials` と `assemble` の差分の範囲が `origin/main..origin/main` の空にならず、前のバージョンのタグから集める。プラグインごとにバージョンのタグが違うときの起点の選び方と、`release_branch` の選んだスプリントの集め方を 10 系の `release_prev`・`strategy_rows.release_range` から移す（テスト）
- [ ] 13. 設定のリリースの形にアダプターが無いとき、`release-steps` は止まり、形の名前と扱いの表（受け入れ条件 1）の在りかを理由に出す（テスト）
- [ ] 14. #1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` にこの課題がある
- [ ] 15. 起きてはいけないこと: ai-plugins の設定（`form: package-plugin`・`version_store.kind: plugin`）での `bump`・`pr-check`・`land` の既存テストがすべて通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | リリースの形とバージョンの保存先の追加は、アダプターを 1 つ足して設定のスキーマに値を足すだけで済む（工程の部品に形ごとの分岐を書かない） |
| セキュリティ | workflow のテンプレートは秘密の値を持たず、秘密の名前は利用者が決める。トークンの権限はテンプレートの `permissions` に最小で書く |
| システム環境 | GitHub Actions を前提にする。ほかの CI のテンプレートは作らない（未決 1） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。設定の `branch_strategy.release` のキーと `release-steps` の出力。ndf-next は未リリースなので `互換なし` |
| データ | 無し（設定の値の追加だけで、移すデータは無い） |
| 既存の振る舞い | ai-plugins の設定では変わらない（受け入れ条件 7・15） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf-next/tests/domains/release -q -n 4` |
| 静的解析・型検査 | `bash scripts/check-lint.sh -- plugins/ndf-next`・`python3 scripts/check-script-structure.py plugins/ndf-next` |
| 検索 | 受け入れ条件 9・11 の `rg` |
| 手動確認 | リリース後テストで、テンプレートを置いた Web サービスのリポジトリ 1 つで `materials` を流し、承認資料が組まれることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 触る場所は `plugins/ndf-next/src/ndf/domains/release/`・`plugins/ndf-next/tests/domains/release/`・`plugins/ndf-next/contracts/`・`issues/issue-3615-*`。後続のレーン lr-rel-rest が同じ場所を触る |
| コーディング規約 | `AGENTS.md`・ndf-next のコントラクト（`contracts/`）とスキーマ。ファイルは行数の上限（500 行）に収める |
| テスト戦略 | アダプターごとの単体テストと、設定のコピーを置いた一時リポジトリでの `release-steps` の結合テスト。`.md` の文言を検証するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 範囲のテストの実行、リンタの適用、語を #3613 の対応表に合わせる |
| 確認してから行う | 設定のスキーマのキーの追加・改名（設計承認ゲートで見る） |
| 行わない | ai-plugins の設定の値の変更、#3572 の決定 7・9・21・22 と食い違う変更、nd-factory などほかのリポジトリへの書き込み |

## 方針の分け方

| 方針 | このリポジトリの運用の決まり（設定の値） | 作るものの振る舞い |
| --- | --- | --- |
| ai-plugins はプラグインの形で出す | `.ndf/project.json` の `branch_strategy.release`（`form: package-plugin`・`version_store.kind: plugin`・`tag: {plugin}--v{version}`） | プラグインの形はアダプターの 1 つで、既定にしない |
| ai-plugins の CD は `NDF_RELEASE_TOKEN` でバージョンアップの PR を作る | `.github/workflows/ndf-next-release.yml` の秘密の名前 | テンプレートは秘密の名前を決め打たない |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 起動の経路を workflow のテンプレートだけにするか、会話のセッションからの起動も文書にするか | 設計（設計承認ゲートで利用者が確かめる） | 設計 PR |
| 2. テンプレートで秘密が無いとき、`github.token` へ倒すか止まるか | 設計 | 設計 PR |
| 3. CHANGELOG の見出しとリリースブランチの名前の書式の設定のキーの名前と書式（`release.tag` と同じ `{version}` の置き換えにそろえるか） | 設計 | 設計 PR |
| 4. リリースの形 9 種と `release.form` の 4 値の対応（デスクトップアプリ・手順・設定・文書の 4 形をどの値で受けるか、値を足すか） | 設計（受け入れ条件 1 の表） | 設計 PR |

## 依頼（原文）

> ai-plugins(ndf) を開発することと、ndf を使って様々な開発を汎用的に進行することの区別がついていない設計が入り込んでしまう

（書き手: 利用者、日付: 2026-11-16。起票者が本文の「目的」に引用した文）

> ndf-next のリリースの部分（`plugins/ndf-next/src/ndf/domains/release/`）は、Claude Code のプラグインの配布の形でしか動かない。版・タグ・承認資料を `.claude-plugin/marketplace.json` と `plugin.json` から作り、宣言の `release.form`・`delivery`・`publishes_release` を読む箇所が 0 件。今の NDF の release Skill は配布の形ごとの参照を 11 種（`form-service`・`form-page`・`form-mobile` など）持っており、作り直しで後退している。#1142 の受け入れ条件「現行と同等」（同等は既知の不具合を引き継がない）に当たる。
>
> 対象範囲 — 含む: リリースの部分が宣言の `release.form`・`delivery` で配布の形を選び、版の置き場（`plugin.json`・`package.json`・`pubspec.yaml`・タグだけ など）とタグ・CHANGELOG の形をアダプタか宣言で受けること。プラグインの形はアダプタの 1 つにする。リリースを利用者のリポジトリから起動する経路（workflow の雛形か会話のセッション）。配らないファイルの既定を `test-files.json` から作る / 含まない: ai-plugins 自身の宣言の値（ai-plugins はプラグインの形を宣言で選ぶ）
>
> 受け入れ条件（起票時）: 配布の形 11 種の扱いの表 / `.claude-plugin` の無いリポジトリで `materials` が `precondition` で止まらない / タグ・CHANGELOG の見出し・リリースブランチの名前の形を宣言で変えられ、宣言が無ければ今の形 / テストだけの変更で版が上がらない / 起動の手順が文書にあり ai-plugins の秘密・変数の名前に依らない / `gather.py`・`assemble.py` の `git.ORIGIN` を `remote_of` へ替え `ORIGIN` を消す / trunk 系で差分の範囲を前の版のタグから集める

（書き手: 起票者（並列の計画 p6 のオーガナイザー）、日付: 2026-11-16〜17。利用者の指示の原文としては扱わない。受け入れ条件の全文は課題の履歴にある）

> 進め方の決まり: #3572 の決定 22 の境の表で #3615 の側とされたものを受け、決定 7・9・21・22 と食い違う決め方をしない / ブランチ戦略の設定は 1 つの設定ファイルの 1 つの節にまとめる / 設計の「適用範囲」に代表のリポジトリでの振る舞いの表を必ず書く / 文書と承認の資料は業界の語で書く / 点検の #1・#2・#8・#9 は 11.0 で直す / #1142 の子の表と `children.json` へこの課題を足す / 設計承認のリクエストはオーガナイザーへ送る

（書き手: AI の起動指示（並列の計画 p6 のオーガナイザー）、日付: 2026-11-16）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示（並列の計画 p8 の supervisor）、日付: 2026-11-17）
