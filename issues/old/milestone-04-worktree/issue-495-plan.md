# #495: 宣言を共有と個人に分け、個人の宣言を重ね合わせる

## 関連リンク

- 課題: [#495](https://github.com/devbasex/ai-plugins/issues/495)
- 要求: [issue-495-610-requirements.md](issue-495-610-requirements.md)（AC11〜AC29、AC30〜AC31）
- 設計: [issue-495-610-design.md](issue-495-610-design.md)（決定 5〜16。この課題は「Pull Request 2」）
- 先行: #610 の Pull Request 1（[#643](https://github.com/devbasex/ai-plugins/pull/643)。`follow_branch` と `wt_follow_enabled` は既に `develop` にある）

## モード

`standard`（親が判定済み。判定はやり直さない）。

## 目的と非目的

達成したい状態:

- ポートの帯・持ち込み物・追従の有無を、追跡しないファイル `.ndf/worktree.local.json` から各自の値にできる
- リポジトリの運用を決める項目（起点・本番のチャネル・案内を出さないパス・外部への公開）は、個人の宣言で変わらない
- 個人の宣言が壊れていても共有の運用は止まらず、`status` / `check` の行で気づける

やらないこと:

- 追従の判定規則（`wt_follow_target`）の変更（#610 で済み、規則は変えない）
- `.ndf/worktree.json` を `jq` で直接読む 6 か所（`check-pr-base.sh` と 5 つの Skill）の変更（設計の決定 13）
- `wt_declaration_stamp` の変更（決定 14）
- `.ndf/projects.json` の個人化（要求の前提 3）
- `CHANGELOG.md` と版数（配布の工程が書く）

## 前提

- 前提 1: 起点は `origin/develop` の 1d060a6。#573 / #312+#315 / #313 / #610 はすべてマージ済みで、`do_init` の書き込みの経路（`write_declaration` と `refuse_symlink`）はその形の上に足す
- 前提 2: このリポジトリの `.ndf/worktree.json` には `follow_branch` を書かない（要求の前提 4）。`.ndf/.gitignore` を足しても追従は有効にならない
- 前提 3: `jq` の `*` はオブジェクトを深く併合し、配列を置換する（設計の実測。jq 1.8.1 で確認済み。実装前に手元でも 1 度確かめる）

## 受け入れ条件

要求文書の AC11〜AC31 をそのまま使う。検証手段は設計の「テスト設計」の表が持つ。

- [ ] AC11〜AC14: 個人の値が反映される（`port_band` / `port_roles` の深い併合 / 配列の置換 / `follow_branch`）
- [ ] AC15〜AC17: 運用の項目は個人の宣言で変わらない（`base_branch` / `production_branch` / `allow_paths` / `expose` / 宛先の検査）
- [ ] AC18〜AC21: 壊れた個人の宣言で共有の宣言だけで動き、`status` / `check` が状態と反映しない項目を報告する
- [ ] AC22〜AC23: 共有の宣言が無い・読めないときは個人の宣言を使わず、`check` の終了コードは共有の宣言だけで決まる
- [ ] AC24〜AC27: `init` が `.ndf/.gitignore` を作り、既にあれば触らない。`status` が登録の案内を出す。このリポジトリ自身にも置く
- [ ] AC28: 個人の宣言が無いときの `status` / `check` の出力と終了コードが変更前と同じ
- [ ] AC29: `declaration.md` に個人の宣言の節があり、SKILL.md の手順 0 が個人の値の置き場所を書く
- [ ] AC30〜AC31: 全体のテスト、配布物の同期の検査、定義の検査

## 代替案と採否

設計文書の決定 5〜16 が持つ。このプランで新しい設計判断はしない。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| B | 個人の項目だけ別ファイルへ分ける | 採用 | 決定 5。宛先の検査と 5 つの Skill が共有の宣言だけを読み続けられる |
| A | 宣言を全部 `.gitignore` へ入れる | 不採用 | 決定 5。宛先の検査が働かなくなる |
| C | 現状のまま混在を許す | 不採用 | 決定 5。ポートの帯を変えた差分が毎回 Pull Request に載る |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 共有の宣言 | `.ndf/worktree.json`。追跡する |
| 個人の宣言 | `.ndf/worktree.local.json`。追跡しない |
| 重ね合わせた宣言 | `wt_declaration` が返す、共有の宣言に個人の宣言を反映した JSON |
| 上書きできる項目 | 個人の宣言から反映する最上位の項目。`localenv` / `testenv` / `follow_branch` の 3 つ |

## 不変条件

- `wt_declaration` は、個人の宣言の状態では失敗しない（共有の宣言の状態だけで 0 / 1 が決まる）
- `check` の終了コードは共有の宣言の状態だけで決まる（0 あり / 2 なし / 3 読めない / 1 判定できない）
- 重ね合わせた宣言の `testenv.expose` は、共有の宣言の値と常に一致する
- `wt_base_branch` / `wt_production_branch` / `wt_allow_paths` の出力は、個人の宣言の有無で変わらない

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 宣言の形（`version`） | 個人の宣言ファイルが増える。共有の宣言の項目は増えない | 上げない（項目の追加。`declaration.md` の互換性の規則） |
| `wt_declaration` の契約 | 戻り値の意味は変えず、出力の内容に個人の値が混ざる | 呼び出し側は変えない（決定 13） |
| `worktree-setup.sh` の出力 | 個人の宣言ファイルがあるときだけ行が増える | 無いときは変えない（決定 16、AC28） |
| 継続的統合 | 変わらない | 個人の宣言は追跡されないため届かない |

## 修正対象

- `plugins/ndf/scripts/lib/worktree-common.sh` — `_wt_local_overrides` / `wt_declaration_local_state` / `wt_declaration_local_ignored` の新設、`wt_declaration` の変更
- `plugins/ndf/scripts/worktree-setup.sh` — `status` / `check` の行、`init` の `.ndf/.gitignore` の作成
- `plugins/ndf/skills/worktree/references/declaration.md` — 個人の宣言の節
- `plugins/ndf/skills/worktree/SKILL.md` — 手順 0
- `plugins/ndf/skills/worktree/tests/test_declaration_local.py`（新設）
- `plugins/ndf/skills/worktree/tests/test_setup.py`
- `scripts/tests/test_pr_base_guard.py` / `scripts/tests/test_base_branch_consistency.py`
- `.ndf/.gitignore`（新設。このリポジトリ自身）
- `plugins/ndf/dev.kiro/` / `plugins/ndf/dev.agy/` の配布物（`bash scripts/build-runtime-plugins.sh` で同期）

## タスク分解

### Task 1: 重ね合わせを `wt_declaration` の中で行う

- **対象ファイル:** `plugins/ndf/scripts/lib/worktree-common.sh`、`plugins/ndf/skills/worktree/tests/test_declaration_local.py`（新設）
- **変更内容:** `WT_DECLARATION_LOCAL_FILE` を足し、`_wt_local_overrides`（反映する部分だけを 1 回の jq で取り出す）を新設し、`wt_declaration` が共有の宣言へ `*` で重ねる。失敗したら共有の宣言をそのまま返す
- **満たす受け入れ条件:** AC11〜AC16、AC18、AC20
- **進め方:** jq の挙動（`*` の併合・配列の置換・最上位が配列のときの失敗・`del(.testenv.expose)` の型依存）を先に手元で実行して確かめる → 失敗するテスト → 最小実装 → 整理

### Task 2: 個人の宣言の状態と反映しない項目を返す

- **対象ファイル:** `plugins/ndf/scripts/lib/worktree-common.sh`、`plugins/ndf/skills/worktree/tests/test_declaration_local.py`
- **変更内容:** `wt_declaration_local_state`（`absent` / `present` / `unreadable` / `unused`）と `wt_declaration_local_ignored`（反映しない項目名を 1 行 1 件。`version` と `$schema` は出さない）を新設する
- **満たす受け入れ条件:** AC19、AC21〜AC23 の判定の土台
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 3: `status` と `check` が個人の宣言を報告する

- **対象ファイル:** `plugins/ndf/scripts/worktree-setup.sh`、`plugins/ndf/skills/worktree/tests/test_setup.py`
- **変更内容:** 「宣言ファイル:」の行の直後に個人の宣言の行を足す（ファイルがあるときだけ）。反映しない項目がある場合は続けて 1 行。`status` だけが追跡から外れていないときの登録の案内を出す
- **満たす受け入れ条件:** AC19、AC21〜AC23、AC26、AC28
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 4: `init` が `.ndf/.gitignore` を作る

- **対象ファイル:** `plugins/ndf/scripts/worktree-setup.sh`、`plugins/ndf/skills/worktree/tests/test_setup.py`
- **変更内容:** 共有の宣言を書いたときだけ `.ndf/.gitignore` を作る。既にあれば触らない。書き方は宣言と同じ（同じディレクトリの一時ファイル → 名前の付け替え、symlink はたどらずに断る）
- **満たす受け入れ条件:** AC24、AC25
- **進め方:** `git check-ignore` の挙動を先に確かめる → 失敗するテスト → 最小実装 → 整理

### Task 5: 宛先の検査と起点の解決が個人の宣言で変わらないことを固定する

- **対象ファイル:** `scripts/tests/test_pr_base_guard.py`、`scripts/tests/test_base_branch_consistency.py`
- **変更内容:** 既存のパラメータへ「個人の宣言に `base_branch: main` がある」を足す
- **満たす受け入れ条件:** AC17
- **進め方:** 失敗しないことを確かめる形の追加（現状固定）。期待値は変えない

### Task 6: 文書とこのリポジトリの `.ndf/.gitignore`

- **対象ファイル:** `plugins/ndf/skills/worktree/references/declaration.md`、`plugins/ndf/skills/worktree/SKILL.md`、`.ndf/.gitignore`
- **変更内容:** 個人の宣言の節（置き場所・上書きできる項目・重ね合わせの規則・壊れたときの扱い）を足し、手順 0 に個人の値はコミットしないファイルへ書くことを足す。このリポジトリの `.ndf/.gitignore` を決定 15 の内容で作る
- **満たす受け入れ条件:** AC27、AC29
- **進め方:** 文書は実装の後に書き、`git check-ignore` で AC27 を確かめる

## 影響範囲

- `wt_declaration` を呼ぶすべての入口（`worktree-guard.sh` / `worktree-session.sh` / `worktree-localenv.sh` / `worktree-testenv.sh` / `worktree-setup.sh` / `wt_declaration_branch`）。呼び出し側のコードは変えないが、個人の宣言があるときは値が変わる
- `worktree-setup.sh` の `status` / `check` の出力（個人の宣言ファイルがあるときだけ）
- 配布物（`dev.kiro` / `dev.agy`）は同期で追随する

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `worktree-common.sh` が 2248 行の 1 ファイルで、宣言の節に関数を足す | 実装の後の構造改善（cross-refactoring）で足りる。宣言の節は 100 行程度でテストが厚く、触る範囲が `wt_declaration` の前後に収まる |
| `worktree-setup.sh` の `status` / `check` の出力に依存する既存テストが壊れる | AC28 を現状固定として先に通す（個人の宣言が無いときの出力を変えない） |
| jq の型に依存する式（`del(.testenv.expose)`）が、`testenv` が文字列のときに終了コード 5 で落ちる | 型を先に確かめる形で書き、AC20 のテストで固定する |
| 個人の宣言から `expose` を有効にできてしまう | 反映する部分から `testenv.expose` を消す。AC16 のテストで固定する |

## 切り戻し手順

Pull Request を戻せば元へ戻る。永続データの移行は無い。`.ndf/.gitignore` を消せば追跡の状態も戻る。

## 完了の定義

- [ ] AC11〜AC31 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `bash scripts/build-runtime-plugins.sh --check` と `claude plugin validate .` が終了コード 0
- [ ] cross-refactoring が収束し、最後の cross-review が両者 `APPROVE`
