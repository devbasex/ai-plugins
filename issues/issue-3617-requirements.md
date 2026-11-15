# #3617: ndf-next への ai-plugins 固有の前提の混入: 既定値・文書・同梱の 12 件を直し、決め打ちと宣言の受け口の減少を CI で止める

正は課題の本文（#3617）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く、起票者（並列の計画 p6 のオーガナイザー）か要求の書き手が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | ai-plugins の値が ndf-next の既定に入ったのは、ndf-next を ai-plugins の中で作り ai-plugins でしか試していないためである | 起票時の本文の「起票者の推定」 | 目的の背景として読む。受け入れ条件は混入の結果（既定値・文書・同梱）と、再発を止める検査に置く |
| S2 | 点検の 20 件のうち 1〜6・8・9・12 は #3615・#3616・#3572 が受け持ち、この Issue は 7・10・11・13〜20 を受け持つ | 起票時の本文の「点検の一覧」の受け持ちの列 | 対象範囲の「含む」「含まない」 |
| S3 | ハードコードを数える CI の検査は、例外リストの各項目に件数と理由を持ち、例外リストの外が増えたら落ちる | 起票時の本文の「対象範囲」 | 受け入れ条件 1〜4 |
| S4 | 設定で選べる値（取り込み方の表の行・`delivery` の種類・リリースの形）を数え、減ったら落ちる CI の検査を置く。減らすときは例外の項目に Issue の番号を書く | 起票時の本文の「対象範囲」 | 受け入れ条件 5〜7 |
| S5 | リモート名は設定で受け、既定は `origin` にする | 点検の一覧 10 の受け持ちの列 | 受け入れ条件 8・9 |
| S6 | 文書だけの差分とみなすパスは設定で受ける | 点検の一覧 11 の受け持ちの列 | 受け入れ条件 10・11。既定は前提 3 |
| S7 | クロスリファクタリングの配分の値は、初期値であることを書き、設定で上書きできるようにする | 点検の一覧 13 の受け持ちの列 | 受け入れ条件 12 |
| S8 | 起動のときに外す旧い NDF は、ハードコードの登録名ではなく、導入されている登録から読む | 点検の一覧 14 の受け持ちの列 | 受け入れ条件 13 |
| S9 | 機能の対応表（parity）とファイル冒頭の注記は、NDF 自身の開発の置き場へ移す | 点検の一覧 18 の受け持ちの列 | 受け入れ条件 18・19。置き場は未決 1 |
| S10 | NDF 自身の CI の検査のサブコマンド（`check`・`manifests`・`hooks`）は、NDF 自身の開発へ移すか、利用者向けの一覧から隠す | 点検の一覧 19 の受け持ちの列 | 受け入れ条件 20・21。どちらにするかは未決 2 |
| S11 | 設計の「適用範囲」に、代表のリポジトリ 3 種（Web サービス・モバイルアプリ・業務文書だけ）と ai-plugins だけの事情の欄を持つ振る舞いの表を書く | 起票時の本文の「設計で書くこと」（オーガナイザーの指示。#3618 の設計の型の先取り） | 受け入れ条件 24 |
| S12 | ほかのレーン（#3613・#3615・#3616）が触るファイルは、その直しを待たずに検査の例外リストで扱う | 起票時の本文の「設計で書くこと」 | 前提 5 |
| S13 | この Issue を #1142 の子の表と `plugins/ndf-next/src/ndf/checks/children.json` へ足す。子の記号・順番・レーンは設計で決めて設計 PR に載せる | 起票時の本文の「設計で書くこと」 | 受け入れ条件 25。記号・順番・レーンは未決 3 |

## 目的

ndf-next（`plugins/ndf-next/`）を、ai-plugins 以外のリポジトリ（言語・テストの形・ブランチの運用・リリースの形が違うもの）でも、ai-plugins の値を持ち込まずに使える状態にする。

- ai-plugins の値は、ai-plugins の `.ndf/` の設定ファイルにだけ置く。ndf-next の既定値・Skill の本文・同梱物には置かない
- 同じ混入が再び入ったら CI が落ちる。設定が受けられる値が黙って減っても CI が落ちる

背景: 2026-11-16 の点検で、ndf-next に ai-plugins の事情が汎用の振る舞いとして 20 件入っていた（壊れる 6・検査の穴 1・既定が合わない 8・文書と同梱だけ 5）。ハードコードを見つける今の検査（`scripts/check-skill-repo-assumptions.py`）は、走査する Skill を `manifests/*-skills.txt` から読む。ndf-next の Skill の一覧は `manifests/skills.json` にあるため、ndf-next の Skill を 1 本も見ていない。`src/` の Python と `contracts/` も走査の外である。

```text
$ python3 scripts/check-skill-repo-assumptions.py --report
plugins/ndf-next/skills: 公開する Skill 0 個 / Markdown 0 本 / スクリプト 0 本（family 0・Skill 0） / hook 2 本 / エージェント 11 本 / ヒット 0 件
```

（2026-11-15、`design/issue-3617` の 43fbe05fb で実行。`plugins/ndf-next/skills/` には Skill のディレクトリが 51 個ある）

## 点検の一覧（20 件）

| # | 場所 | ai-plugins の前提 | 重さ | 受け持ち |
| --- | --- | --- | --- | --- |
| 1 | `domains/release/plugins.py`・`assemble.py`・`tagging.py`・`gather.py` | リリースがプラグインの形だけ | 壊れる | #3615 |
| 2 | `domains/release/__init__.py`・`.github/workflows/ndf-next-release.yml` | リリースの実行の場が ai-plugins の workflow だけ | 壊れる | #3615 |
| 3 | `adapters/ci.py:58`・`core/route/__init__.py:35-39`・`parts/steps.json:73` | 落ちたテストを pytest の形だけで読む | 壊れる | #3616 |
| 4 | `issues/issue-3572-design-decisions.md` 決定 5・14 | 取り込み方の表から develop→main の行を外す | 壊れる（設計） | #3572（やり直し中） |
| 5 | 同 決定 9 | 検証の場が 2 つ目のマーケットプレイスだけ | 壊れる（設計） | #3572 |
| 6 | 同 決定 7 | バージョンを上げる PR を迂回でマージ、`plugin.json` を書き換える | 壊れる（設計） | #3572・#3615 |
| 7 | `scripts/check-skill-repo-assumptions.py:172-178` | 走査が `manifests/*-skills.txt` 前提で ndf-next を見ない | 検査の穴 | この Issue |
| 8 | `domains/release/changes_decide.py:22` | 配らないファイルの既定が Python のテストだけ | 既定 | #3615 |
| 9 | `version_decide.py:31-33`・`changelog_decide.py:29` | タグ・リリースブランチ・CHANGELOG の形 | 既定 | #3615 |
| 10 | `adapters/git.py:29`・`parts/steps.json:19`・`check.json:12`・`core/converge/worktree.py:248`・`core/route/__init__.py:406` | リモート名 `origin` のハードコード | 既定 | この Issue（設定 remote、既定 origin） |
| 11 | `contracts/templates/check.json:12`（`assess`） | `.md` だけの差分は全体テストとリファクタリングを飛ばす | 既定 | この Issue（文書だけとみなすパスを設定で） |
| 12 | `domains/crossrefactor/collect.py:27` | 終了コード 5 を pytest の収集 0 件として受ける | 既定 | #3616 |
| 13 | `skills/cross-refactoring/data/allocation-defaults.json` | 配分の初期値が ai-plugins の実測 | 既定（軽い） | この Issue（初期値と明記・設定で上書き） |
| 14 | `adapters/claude.py:32` | `{"ndf@ai-plugins": False}` のハードコード | 既定 | この Issue（導入元を読む） |
| 15 | `.github/workflows/ndf-next-release.yml:96-97` | `base_branch // "develop"`・`// "main"` でスキーマ（未指定なら origin の HEAD）と食い違う | 既定（ai-plugins 側） | この Issue |
| 16 | `skills/merged/SKILL.md:158` | `--admin` のマージを既定として書く | 文書 | この Issue |
| 17 | `skills/release/SKILL.md:39`・`skills/development-workflow/SKILL.md:337` | 汎用の表の行に「（ai-plugins）」 | 文書 | この Issue |
| 18 | `skills/**`・`agents/**` の冒頭の注記（`parity` を含むファイル 104 本）、`src/ndf/parity/`（188K） | 機能の対応表（parity）をリリース対象に同梱 | 同梱 | この Issue（NDF 自身の開発の置き場へ移す） |
| 19 | `src/ndf/cli.py:31-55` の `check`・`manifests`・`hooks`、`src/ndf/checks/` | NDF 自身の CI の検査（I2・移しの印・子の表）を利用者向けのサブコマンドとして出す | 同梱 | この Issue（NDF 自身の開発へ移すか一覧から隠す） |
| 20 | `development-workflow/references/projects-tracking.md`・`progress-tracking/references/board.md` | 例に `--owner devbasex`・`devbasex/ai-plugins` | 文書 | この Issue（プレースホルダへ） |

パスは、`.github/` と `scripts/` と `issues/` で始まるものはリポジトリの根から、それ以外は `plugins/ndf-next/` から（`domains/`・`adapters/`・`core/`・`parts/` は `src/ndf/` の下）。件数と大きさは 2026-11-15 に `grep -rl parity skills agents | wc -l`・`du -sh src/ndf/parity` で数えた。

手本になる設定の受け方: `domains/worktree/repo.py:78-80`（ブランチ名を設定から読み、無ければ origin の既定ブランチ、決まらなければ止まる）・`domains/release/land.py`（迂回を設定と ruleset の両方で確かめる）・`domains/hooks/guards/full_test.py`（設定の suite）・`contracts/domains/test-files.json`（言語ごとの表）・`contracts/domains/branch-flows.json`（行で受け、合わない設定は止める）。

## 前提

- 前提 1: ndf-next は未リリースであり、既定値・サブコマンドの一覧・同梱物を変えても、利用者の側の互換の経路は要らない
- 前提 2: 今の NDF（`plugins/ndf/`）の振る舞いは変えない。検査の走査の対象を広げても、`plugins/ndf/skills` と `plugins/playwright-kit/skills` の件数と合否は今と同じである（2026-11-15 の `--report` で、`plugins/ndf/skills` は公開する Skill 49 個・ヒット 238 件、宣言の外 0 件、終了コード 0）
- 前提 3: 文書だけとみなすパスの設定が無いときの既定は、今と同じ「拡張子が `.md` のファイルだけ」である。ai-plugins は Skill の本文（`.md`）が振る舞いであるため、ai-plugins の `.ndf/` に自分の値を書く
- 前提 4: リモート名の設定が無いときの既定は `origin` である
- 前提 5: #3613・#3615・#3616 が触るファイル（点検の一覧の 1〜3・8・9・12 の場所と、#3613 が置き換える語）は、この Issue では直さず、検査の例外リストに件数・理由・受け持ちの Issue の番号つきで載せる
- 前提 6: ハードコードとして数える名前は、少なくとも `ai-plugins`・`ndf@`・`ndf--v`・`marketplace.json`・`plugin.json`（リリースの領域の外）・`develop`・`devbasex` である。足す名前は設計で決める
- 前提 7: 11.0 のバージョンで直す（利用者の指示「先ほど発見した ndf-next の点検 20 件も 11.0 で修正されるようにしてください」）

## 対象範囲

含む:

- 点検の一覧の 7・10・11・13〜20 の直し
- ndf-next の Skill・`src/`・`contracts/` を走査し、ai-plugins 固有の名前のハードコードを数え、例外リスト（件数と理由つき）の外が増えたら落とす CI の検査
- 設定で選べる値（取り込み方の表の行・`delivery` の種類・リリースの形）をコントラクトから数え、減ったら落とす CI の検査
- ai-plugins の値を ai-plugins の `.ndf/` の設定ファイルへ移すこと
- #1142 の本文の子の表と `children.json` へこの Issue を足すこと

含まない:

- 点検の一覧の 1〜6・8・9・12（#3615・#3616・#3572 が受け持つ）
- #3613 が受け持つ ndf-next 全体の語の置き換え
- 今の NDF（`plugins/ndf/`）の既定値・文書の直し

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 開発者が ndf-next のファイルを変えた PR を出した | 開発者の push | — | — |
| E2 | CI が ndf-next の Skill・`src/`・`contracts/` のハードコードを数えた | E1 | 例外リストが読めない・形が違うときは落ちる（数えずに通さない） | E1 |
| E3 | 例外リストの外のハードコードが見つかり、CI が落ちた | E2 で例外リストの件数を超えた | — | E2 |
| E4 | CI が設定で選べる値を数え、基準の集合と比べた | E1 | 基準の集合が読めないときは落ちる | E1 |
| E5 | 選べる値が減り、Issue の番号のある例外が無いため、CI が落ちた | E4 で減った値がある | — | E4 |
| E6 | 利用者のリポジトリで ndf が `.ndf/` の設定を読んだ | ndf のサブコマンドの起動 | 設定の形が違うときは止まり、直す箇所を示す | — |
| E7 | ndf がリモート名・文書だけとみなすパス・配分の値を、設定か既定から決めた | E6 | — | E6 |
| E8 | ndf が Claude Code を起動するとき、導入されている旧い NDF の登録を読み、外す登録を決めた | Skill を回す起動（full） | 登録の一覧が読めないときは、外す登録を空にして起動し、読めなかったことを結果に残す（前提ではなく未決 4） | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| ハードコード | ai-plugins 固有の名前（リポジトリ名・組織名・ブランチ名・タグの形・マーケットプレイスの登録名など）を、設定から読まずにコード・コントラクト・Skill の本文へ直接書くこと |
| 例外リスト | 検査が違反として数えない箇所の一覧。この Issue の検査では、項目ごとにファイル・名前・件数・理由を持つ |
| 設定で選べる値 | `.ndf/` の設定ファイルに書ける値の選択肢。取り込み方の表（`branch-flows.json`）の行・`delivery` の種類・リリースの形 |
| 機能の対応表 | 今の NDF の機能と ndf-next の行き先の対応表（`src/ndf/parity/`）。各ファイルの冒頭の対応の注記もこれを指す |

文書だけとみなすパス: 変えたファイルがすべてこのパスに当たる差分では、全体テストとリファクタリングを飛ばす。

## 受け入れ条件

ハードコードの検査（点検の一覧 7）:

- [ ] 1. `check-skill-repo-assumptions.py --report`（か後継の検査）が、ndf-next の Skill・`src/`・`contracts/` のそれぞれについて 0 でない走査の件数を出す（Skill は `plugins/ndf-next/skills/` の Skill のディレクトリの数と一致する）
- [ ] 2. 例外リストの外のハードコードを 1 つ足した入力（例: `src/` の Python に `devbasex` を 1 行足す）で、検査が 0 でない終了コードで終わり、足したファイルと行を出す
- [ ] 3. 例外リストの項目の件数よりハードコードが 1 件増えた入力で、検査が落ちる。理由の無い項目を持つ例外リストでも落ちる
- [ ] 4. 前提 2 のとおり、`plugins/ndf/skills` と `plugins/playwright-kit/skills` の件数・宣言の外の件数・終了コードが変わらない（退行しない）

設定で選べる値の検査:

- [ ] 5. `contracts/domains/branch-flows.json` から行を 1 つ外した入力で、CI の検査が落ち、外れた値を出す
- [ ] 6. 同じ入力に、外れた値と Issue の番号を持つ例外の項目を足すと、検査が通る。Issue の番号の無い例外の項目では落ちる
- [ ] 7. `delivery` の種類とリリースの形の集合についても、5・6 と同じ入力で同じ結果になる

既定値（点検の一覧 10・11・13・14・15）:

- [ ] 8. `.ndf/` の設定にリモート名 `upstream` を書いたリポジトリで、ndf の取得・照合・差分の基準が `upstream` を使う。点検の一覧 10 の場所に `origin` のハードコードが残らない（ハードコードの検査の例外リストに載らない）
- [ ] 9. リモート名の設定が無いリポジトリでは、今と同じく `origin` を使う（退行しない）
- [ ] 10. `.ndf/` の設定で文書だけとみなすパスを書いたリポジトリで、変えたファイルがすべてそのパスに当たる差分は全体テストとリファクタリングを飛ばし、1 つでも外れる差分は飛ばさない
- [ ] 11. 文書だけとみなすパスの設定が無いリポジトリでは、前提 3 の既定のとおり、`.md` だけの差分を飛ばす（退行しない）
- [ ] 12. 配分の初期値のファイルが、値が初期値であることを持ち、`.ndf/` の設定に配分の値を書いたリポジトリでは設定の値で配分する（後半の「設定の値で配分する」の端から端の確認は、呼び手を作る #3643 で行う。呼び手を任せた M4 #3298 が配分の表を作らずに閉じたため。この Issue は配分の値を決める部品と宣言を置く）
- [ ] 13. Skill を回す起動（full）で外す旧い NDF の登録が、`ndf@ai-plugins` のハードコードではなく、導入されている登録から決まる。旧い NDF が `ndf@<別の名前>` で登録された環境でその登録が外れ、旧い NDF が無い環境では何も外さない
- [ ] 14. `ndf-next-release.yml` が、`.ndf/worktree.json` に `base_branch`・`production_branch` が無いとき、スキーマのとおり origin の既定ブランチ（HEAD）を使う

文書（点検の一覧 16・17・20）:

- [ ] 15. `skills/merged/SKILL.md` が `--admin` のマージを既定として書かない。迂回を使う条件（設定と ruleset）を書く
- [ ] 16. ndf-next の Skill の汎用の表の行に「（ai-plugins）」が無い
- [ ] 17. ndf-next の Skill の例が `devbasex`・`devbasex/ai-plugins` ではなくプレースホルダ（`<owner>`・`<owner>/<repo>` など）で書かれている

同梱（点検の一覧 18・19）:

- [ ] 18. 利用者へリリースする ndf-next の Skill とエージェントの本文に、機能の対応表（parity）の注記と、機能の対応表へのリンクが無い
- [ ] 19. `plugins/ndf-next/` のリリース対象に `src/ndf/parity/` が無く、機能の対応表は NDF 自身の開発の置き場（未決 1）にある。機能の対応表を読む今の検査とテストは、移した先で通る
- [ ] 20. `ndf --help` の利用者向けのサブコマンドの一覧に、NDF 自身の CI の検査（`check`・`manifests`・`hooks` のうち、未決 2 で NDF 自身の開発のものと決めたもの）が出ない
- [ ] 21. 利用者のリポジトリで hook の登録と起動が今と同じく動く（`hooks/entry.py` と、hook の登録を作る手順が、20 の後も動く。退行しない）

ai-plugins の値の置き場:

- [ ] 22. 点検の一覧 10・11・13・14・15 で ai-plugins に要る値は、ai-plugins の `.ndf/` の設定ファイルにだけあり、ndf-next の既定値・コントラクト・Skill の本文に無い（ハードコードの検査の例外リストの外で 0 件）
- [ ] 23. ai-plugins の今の CI の検査（行数・frontmatter・秘密・instructions の検査など）が、この変更の後も通る（退行しない）

設計と記録:

- [ ] 24. 設計文書の「適用範囲」に、Web サービス（TypeScript・vitest か jest・develop→main・OpenAPI）・モバイルアプリ（Flutter か Swift）・業務文書だけのリポジトリの 3 種と「ai-plugins だけの事情」の欄を持ち、点検の一覧 7・10・11・13〜20 の直しの後に何が起きるかを書いた表がある
- [ ] 25. #1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` に、この Issue（子の記号・順番・レーンつき）がある。`children.json` の変更は、正本を直す設計 PR の後に入る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 検査が落ちたとき、ファイル・行・当たった名前（ハードコードの検査）か、減った値（選べる値の検査）を出力に出し、例外リストのどの項目を足せば通るかが読める |
| 性能・拡張性 | ハードコードの検査の走査を広げた後も、CI のジョブの所要が今の同じジョブより 30 秒を超えて延びない（測る手段は設計で決める） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。ndf-next のサブコマンドの一覧と `.ndf/` の設定の項目（リモート名・文書だけとみなすパス・配分の値）。ndf-next は未リリースのため `互換なし` |
| データ | 無し（`.ndf/` の設定ファイルに項目が足される） |
| 既存の振る舞い | ndf-next の既定値が設定で変えられるようになる。設定の無いリポジトリの振る舞いは前提 3・4 のとおり変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `plugins/ndf-next/bin/ndf --help` |
| テスト | 変えた範囲のテスト（`uv run --frozen --project . --all-extras pytest <対象のテスト> -q`）。全体テストはプランの test-all か CI が持つ |
| 静的解析・型検査 | ndf-next の今の静的解析（`plugins/ndf-next/pyproject.toml` の設定）と、`python3 scripts/check-skill-repo-assumptions.py --report` |
| 手動確認 | 受け入れ条件 13: 旧い NDF を別の登録名で入れた環境で、Skill を回す起動（full）の引数から、その登録が外れていることを見る（リリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。ai-plugins の値は `.ndf/` に置く。ndf-next のリリース対象（`plugins/ndf-next/`）に NDF 自身の開発だけが使うものを置かない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。設定の受け方は、目的の節の後の「手本になる設定の受け方」に揃える |
| テスト戦略 | 検査は、ハードコードを足した入力・値を外した入力で落ちることを単体テストで確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変えた範囲のテスト、ハードコードの検査と選べる値の検査の実行 |
| 確認してから行う | `.ndf/` の設定ファイルの書き換え（C7。リリース承認ゲートで人が見る）、サブコマンドの一覧の変更（設計承認ゲートで人が見る） |
| 行わない | 点検の一覧 1〜6・8・9・12 の直し、今の NDF（`plugins/ndf/`）の変更、#3613 の語の置き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 機能の対応表（parity）を移す先（`docs/`・`issues/`・リポジトリの根の開発用ディレクトリのどれか） | 設計（`design`） | 設計承認ゲート |
| 2. `check`・`manifests`・`hooks` のそれぞれを、NDF 自身の開発へ移すか、利用者向けの一覧から隠すか（`hooks render` と `manifests render` は利用者のリポジトリでも使うかを確かめて決める） | 設計（`design`） | 設計承認ゲート |
| 3. #1142 の子の表でのこの Issue の記号・順番・レーン | 設計（`design`） | 設計承認ゲート |
| 4. 旧い NDF の登録の一覧が読めないときの起動の扱い（E8） | 設計（`design`） | 設計承認ゲート |
| 5. 選べる値の検査の基準の集合を置く場所と形 | 設計（`design`） | 設計承認ゲート |

## 依頼（原文）

> ai-plugins(ndf) を開発することと、ndf を使って様々な開発を汎用的に進行することの区別がついていない設計が入り込んでしまう

（書き手: 利用者、日付: 2026-11-16 JST。起票時の本文の「由来」に引用されたもの）

> 先ほど発見した ndf-next の点検 20 件も 11.0 で修正されるようにしてください

（書き手: 利用者、日付: 2026-11-16 JST。起票時の本文の「設計で書くこと」に引用されたもの）

> - **設計の「適用範囲」に、代表のリポジトリでの振る舞いの表を必ず書く**（#3618 の設計の型を先取り）。少なくとも次の 3 種で、直した後に何が起きるかを書く
>   - Web サービス（TypeScript・vitest/jest・develop→main・OpenAPI）
>   - モバイルアプリ（Flutter か Swift）
>   - 業務文書だけのリポジトリ
>   - 参照してよい実物: `/work/nd-factory`（`promotion: selected`・`base_branch: qa/dev`）・`/work/carmo-system-console`（Laravel・PHPUnit）
>   - 「ai-plugins だけの事情」の欄も書き、ai-plugins の値は ai-plugins の `.ndf/` の宣言へ移す
> - 文書と承認の資料は業界の語で書く（基準は `.ndf/handoff/lepic-note-terms.md`。「完了条件」はそのまま）。#3613（レーン lterms）が ndf-next 全体の語を置き換えるので、新しく書く語はその対応表に合わせる
> - ほかのレーン（lterms #3613・lci-junit #3616・lrel-form #3615）が触るファイルは、その直しを待たずに検査の許可リストで扱う
> - **#1142 の受け入れ条件に入る**（ラベル `rebuild-1142`）。#1142 の本文の子の表と `plugins/ndf-next/src/ndf/checks/children.json` へこの Issue を足す。`children.json` の決まり（正本を直す設計 PR を先に通してから直す）に従い、子の記号・順番・レーンは設計で決めて設計 PR に載せる
> - 設計承認はオーガナイザーが利用者へ問い、承認の後に設計 PR を手でマージする（#3603 が直るまで）

（書き手: AI の起動指示（並列の計画 p6 のオーガナイザー）、日付: 2026-11-16 02:4x JST。起票時の本文の「設計で書くこと」。利用者の指示の原文として扱わない）

起票時の本文の「目的」「点検の一覧」「対象範囲」「受け入れ条件」「影響」「起票者の推定」は起票者（同じオーガナイザーが worker で点検した結果）が書いた。点検の一覧は上へそのまま写し、条件は「起票者の推定」の節へ写した。
