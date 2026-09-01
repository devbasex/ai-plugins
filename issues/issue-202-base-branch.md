# issue #202: 開発の起点ブランチを宣言で決める

## 関連リンク

- [issue #202](https://github.com/devbasex/ai-plugins/issues/202)
- 由来: [issue #192](https://github.com/devbasex/ai-plugins/issues/192) — 配布のチャネルを
  `main` / `develop` へ分ける
- 判定基準: `plugins/ndf/skills/development-workflow/SKILL.md`
- 起点の解決を持つ実体: `plugins/ndf/scripts/lib/worktree-common.sh`

## 依頼（原文）

> NDF の複数の Skill が「**開発の起点ブランチ = 既定ブランチ**」を前提にしている。#192 で
> このリポジトリの構成を「`main` = 正式版（既定ブランチ）/ `develop` = 開発版」へ変えるため、
> `develop` へ移行した時点でこの前提が崩れる。

> **`develop` ブランチを作る前。** 作った後では、作業ツリーの起点が正式版になったまま開発が
> 進む。

あわせて、`main` 宛の Pull Request を `develop` 発のものだけに限る検査を入れることが依頼に
含まれる。

## モード

`architecture`。宣言ファイル `.ndf/worktree.json` に、利用者が書くキーを 1 つ足す（公開
インタフェースの追加）。変更は共通ライブラリ `worktree-common.sh`、`worktree` /
`ndf-policies` / `cherry-pick-pr` / `deploy` の 4 Skill、継続的統合の設定にまたがる。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 既定ブランチ | リポジトリの HEAD が指すブランチ。clone 直後に checkout される |
| 起点ブランチ | 開発の変更が分岐し、Pull Request の宛先になるブランチ |
| 宣言 | 主ディレクトリの `.ndf/worktree.json` |
| 取り込み | 短命ブランチへ起点ブランチを merge すること |
| 検査 | 継続的統合（GitHub Actions）で走る自動判定 |
| 解決 | 宣言と git の参照から、起点ブランチの名前を 1 つ決めること |

## 目的と非目的

達成したい状態:

- 既定ブランチと開発の起点が違うリポジトリで、作業ツリーが開発の起点から分岐する
- 短命ブランチが取り込む対象が、正式版ではなく開発の起点になる
- 宣言に起点を書かないリポジトリの挙動が、現在と変わらない
- `main` 宛の Pull Request が `develop` 以外から出たとき、検査で落ちる

開発の起点前提は `merged` / `pr` / `pr-review` / `problem-solving` にもある。いずれも
`develop` を作った後に誤るため、範囲へ含める。

やらないこと:

- **`develop` ブランチを実際に作ること。** この変更は前提を直すもので、チャネルの移行そのものは
  後続の作業になる
- **Pull Request の宛先を `develop` へ切り替えること。** `AGENTS.md` の規則は `develop` が
  存在してから効く
- **`$NDF_SCRIPTS` の定義**（[issue #193](https://github.com/devbasex/ai-plugins/issues/193)）。
  別の課題として開いている

## 前提

- 前提 1: 宣言に起点を書かないリポジトリでは、現在の解決（origin の HEAD → `main` → `master`）が
  そのまま使われる
- 前提 2: 検査は `develop` が origin に存在するときだけ判定する。存在しない間は成功で通す
- 前提 3: 起点として宣言された名前が origin にもローカルにも無い場合、解決は失敗として扱い、
  既定ブランチへ落とさない

前提 3 の理由: 落とすと、開発版のつもりの変更が正式版から分岐したまま進む。これは直そうと
している事象そのものである。

前提 2 の理由: 検査を先に有効化すると、`develop` が無い間に出す Pull Request がすべて落ちる。
時期を人の手で合わせる代わりに、`develop` の有無で判定する。

## 受け入れ条件

起点の解決:

- [ ] 宣言に起点の指定が無いリポジトリで、解決の結果が既定ブランチの解決と同じになる
- [ ] 宣言が起点に `develop` を指定し、`origin/develop` が存在するとき、解決の結果が `develop` になる
- [ ] 宣言が起点を指定し、同名のブランチがローカルにだけあるとき、解決の結果がその名前になる
- [ ] 宣言が起点を指定し、同名のブランチが origin にもローカルにも無いとき、解決が終了コード 1 で
      失敗し、標準エラーへ指定された名前を含む案内が出る
- [ ] 起点の値が文字列でない（数値・配列・null）とき、宣言に指定が無い場合と同じ結果になる
- [ ] 宣言の版が対応外のとき、宣言に指定が無い場合と同じ結果になる

作業ツリーの起点:

- [ ] 手順が示すコマンドで作業ツリーを作ると、宣言した起点ブランチから分岐する
- [ ] 主ディレクトリのブランチ追従で、稼働中の開発用作業ツリーが 0 個または複数のとき、
      宣言した起点ブランチへ合わせる

取り込みの対象:

- [ ] 開発の起点を扱う 8 Skill の手順に、`origin/main` の字面が残っていない
- [ ] 同じ 8 Skill の git のコマンドの引数に、`main` の字面が残っていない
- [ ] 起点を解決する 4 Skill の手順が示す結果が、共通ライブラリの解決の結果と一致する

Pull Request の宛先の検査:

- [ ] `develop` が origin に無いとき、`main` 宛の Pull Request で検査が成功する
- [ ] `develop` が origin にあり、`main` 宛の Pull Request の分岐元が `develop` のとき、検査が成功する
- [ ] `develop` が origin にあり、分岐元が `develop` 以外のとき、検査が失敗し、宛先の指定方法を
      含む案内が出る

起きてはいけないこと:

- [ ] 宣言を持たないリポジトリで、hook もコマンドも何も出力せず終了コード 0 で終わる
- [ ] 宣言の版は 1 のまま変わらない（既存の宣言ファイルがそのまま読める）
- [ ] 既存のテスト 1301 件が通り続ける

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 宣言へ任意のキーが 1 つ増える。書かなければ挙動は変わらない |
| データ | スキーマ変更・移行はない |
| 既存の振る舞い | 宣言に起点を書いたリポジトリでのみ、作業ツリーの起点と追従先が変わる |
| 配布物 | Skill 4 個と共通ライブラリが変わる。配布の対象になる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 静的解析 | `bash scripts/validate-runtime-plugins.sh` |
| 記載の陳腐化 | `python3 scripts/check-doc-staleness.py` |
| Skill の frontmatter | `python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 検査の 3 条件のうち、いま実行できるのは `develop` が無い場合の 1 つだけである。残る 2 つは判定部分を切り出したテストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 判定は `plugins/ndf/scripts/lib/worktree-common.sh` に集める（`CLAUDE.md` の v9.2.0 の段落） |
| コーディング規約 | `AGENTS.md`「最小限のコード実装」。手順に書くコマンドは書く前に実行して確かめる |
| テスト戦略 | 共通ライブラリの関数は `plugins/ndf/skills/worktree/tests/` の pytest から shell を呼んで検査する。手順の bash はコードブロックを抽出して実行する（`test_projects_scripts_lookup.py` と同じ形） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、手順に書いた bash の実行確認、宣言が無い場合の挙動の確認 |
| 確認してから行う | 宣言の版を上げること、既定の解決順序を変えること |
| 行わない | `develop` の作成、Pull Request の宛先の切り替え、`$NDF_SCRIPTS` の定義 |

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 宣言へ起点のキーを足す | 宣言の最上位へ `base_branch` を置き、共通ライブラリが解決する | 採用 | 宣言の仕組みが既にあり、書かなければ現在の挙動のまま動く |
| 環境変数で渡す | `NDF_BASE_BRANCH` を読む | 不採用 | セッションごとに設定が要る。リポジトリの設定として共有できない |
| 既定ブランチの解決順序を変える | `develop` を `main` より先に探す | 不採用 | `develop` を別の用途に使うリポジトリの挙動まで変わる |
| 検査を後続の作業へ回す | 宛先の検査を `develop` の作成と同じ Pull Request で入れる | 不採用 | 追加する時期を人が合わせることになる。`develop` の有無で判定すれば同じ結果を機械で得られる |

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 宣言ファイル | 最上位へ任意のキーを 1 つ追加 | 追加のみ。版は 1 のまま |
| 既定ブランチを解決する関数 | 変えない | 既定ブランチの解決は起点の解決とは別の関数として残す |
| 4 Skill の手順 | 起点の解決を挟む | 宣言に指定が無ければ、解決の結果は現在と同じになる |
| 継続的統合 | 検査を 1 つ追加 | `develop` が無い間は成功で通る |

## 不変条件

- 宣言を持たないリポジトリでは、この仕組みは何も出力せず終了コード 0 で終わる
- 起点の解決に成功したとき、返る名前は origin かローカルのどちらかに実在する
- 起点を解決できないとき、既定ブランチへ落とさない

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `plugins/ndf/scripts/lib/worktree-common.sh` | 起点を解決する関数を追加する |
| `plugins/ndf/scripts/worktree-session.sh` | 追従先を起点ブランチにする |
| `plugins/ndf/skills/worktree/SKILL.md` | 作業ツリーの起点の取り方を書き換える |
| `plugins/ndf/skills/worktree/references/declaration.md` | 宣言のキーを追加する |
| `plugins/ndf/skills/ndf-policies/SKILL.md` | ブランチ運用の原則 3 を書き換える |
| `plugins/ndf/skills/cherry-pick-pr/SKILL.md` | 取り込みの手順と対応表を書き換える |
| `plugins/ndf/skills/deploy/SKILL.md` | 取り込みの手順と概要を書き換える |
| `plugins/ndf/skills/merged/SKILL.md` | 更新・整理・取り込みの先を起点ブランチにする |
| `plugins/ndf/skills/pr-review/SKILL.md` | ブランチ差分の比較元を起点ブランチにする |
| `plugins/ndf/skills/pr/SKILL.md` | 関連の記載を起点ブランチにする |
| `plugins/ndf/skills/problem-solving/SKILL.md` | 汚染を避ける対象を起点ブランチにする |
| `scripts/check-pr-base.sh` | 新規。宛先の判定 |
| `.github/workflows/pr-base-guard.yml` | 新規。判定を呼ぶ |
| `plugins/ndf/skills/worktree/tests/test_base_branch.py` | 新規。起点の解決と手順の bash |
| `scripts/tests/test_pr_base_guard.py` | 新規。宛先の判定 |

## タスク分解

### Task 1: 起点ブランチの解決を共通ライブラリへ追加する

- **対象ファイル:** `plugins/ndf/scripts/lib/worktree-common.sh`、
  `plugins/ndf/skills/worktree/tests/test_base_branch.py`
- **変更内容:** 宣言の `base_branch` を読み、実在を確かめて名前を返す関数を足す。指定が無ければ
  既定ブランチの解決へ落とす。指定があって実在しなければ、標準エラーへ案内を出して終了コード 1
- **満たす受け入れ条件:** 起点の解決の 6 件
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: 作業ツリーの起点と追従先を向け直す

- **対象ファイル:** `plugins/ndf/scripts/worktree-session.sh`、
  `plugins/ndf/skills/worktree/SKILL.md`、
  `plugins/ndf/skills/worktree/references/declaration.md`、
  `plugins/ndf/skills/worktree/tests/test_base_branch.py`
- **変更内容:** 追従先を起点ブランチにする。手順が示すコマンドを、起点を解決してから
  `git worktree add` へ渡す形にする。宣言の書き方へキーを 1 つ足す
- **満たす受け入れ条件:** 作業ツリーの起点の 2 件
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。手順の bash はコードブロックを抽出して実行する

### Task 3: 取り込みの対象を起点ブランチへ向ける

- **対象ファイル:** 起点を扱う 8 Skill の `SKILL.md`、
  `scripts/tests/test_base_branch_consistency.py`
- **変更内容:** 手順から既定ブランチの字面を外し、起点を解決してから使う形にする。
  これらの Skill は作業ツリーの仕組みを前提にしないため、手順には共通ライブラリを読み込まずに
  動く数行を書き、その結果が共通ライブラリの解決と一致することをテストで確かめる。
  起点を入れる変数は `dev_base` とする。`cherry-pick-pr` が環境ブランチを「ベースブランチ」と
  呼んでおり、`base` では指す対象が 2 つになる
- **満たす受け入れ条件:** 取り込みの対象の 2 件
- **進め方:** 失敗するテスト → 手順の書き換え → 整理

### Task 4: `main` 宛の Pull Request の分岐元を検査する

- **対象ファイル:** `scripts/check-pr-base.sh`、`.github/workflows/pr-base-guard.yml`、
  `scripts/tests/test_pr_base_guard.py`
- **変更内容:** 宛先が `main` の Pull Request について、`develop` が origin にある場合だけ
  分岐元を判定する。判定はスクリプトへ切り出し、継続的統合の設定はそれを呼ぶだけにする
- **満たす受け入れ条件:** Pull Request の宛先の検査の 3 件
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: 配布物と説明文書を合わせる

- **対象ファイル:** 生成物の同期で変わるファイル
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行し、差分があれば取り込む。
  Skill の数とカテゴリは変わらないため、配布定義の変更はない
- **満たす受け入れ条件:** 起きてはいけないことの 3 件
- **進め方:** 検証コマンドの実行

## 影響範囲

- 作業ツリーを使う開発の流れ（起点の決まり方）
- 環境ブランチへ修正を届ける 2 つの手順（取り込みの対象）
- `main` 宛の Pull Request（`develop` が作られた後）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 起点を解決できないときに作業が止まる | 案内へ、宣言のキーと解決できなかった名前を含める |
| 手順の数行と共通ライブラリの解決が食い違う | 両方の結果を突き合わせるテストを置く |
| 検査が `develop` 作成前に働いて Pull Request が落ちる | `develop` の有無で判定を切り替える。作成前の状態をテストで固定する |
| 3 Skill の手順が `jq` の無い環境で動かない | 宣言が読めないときは既定ブランチへ落ちる形にする |

## 切り戻し手順

- 宣言から `base_branch` を消せば、解決は既定ブランチへ戻る
- `.github/workflows/pr-base-guard.yml` を消せば、宛先の検査は無くなる
- データ移行を含まないため、巻き戻しはファイルの取り消しだけで済む

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が通る
- [ ] `python3 scripts/check-doc-staleness.py` が通る
- [ ] 2 つの外部レビューが承認している
