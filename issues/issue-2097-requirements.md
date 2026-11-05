# #2097: release の雛形が本番のブランチを既定ブランチへ後退させず、worktree.json に production_branch が無いと本番のプランを組めない

正は課題の本文（#2097）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> `.ndf/worktree.json` に `production_branch` が無く、`.ndf/supervise.json` に `release.form: package-plugin` があるリポジトリで、`supervise.py new release --version 1.0.0 --prs 1 --channel prod --worktree <根>/.worktrees/release/v1.0.0 --out <パス>` を打つと、`supervise.py: error: 本番の配布に要る本番のブランチが無い（--production-branch か .ndf/worktree.json の production_branch）` で止まる（2026-10-24 に一時ディレクトリで再現）。
>
> 同じ宣言で、リリースの経路の判定（`lib/delivery.py`）は本番のブランチを既定ブランチ（origin/HEAD）へ後退して決め、経路を雛形（`template`）と判定する。
>
> ## どこで見つけたか
>
> - `plugins/ndf/scripts/supervise_lib/release_templates.py` の `_validate_release_decl`: `a.production_branch` だけを見る
> - `plugins/ndf/scripts/supervise_lib/decl.py:257`: `a.production_branch` は引数か `worktree.json` の `production_branch` だけから決まる
> - `plugins/ndf/scripts/lib/delivery.py:132-136`・`lib/repo.py:136` の `production_branch`: `production_branch` が無ければ既定ブランチ（origin/HEAD）を使う
> - 用語集の「本番」の行は「`production_branch`（無ければ既定ブランチ）」と書く
>
> #1914 の設計（normal の `new sprint` が本番のプランを書き出す）で、本番のプランを組むときに見つけた。
>
> ## なぜこの変更の範囲外なのか
>
> #1914 の受け入れ条件は、`pace: normal` の `new sprint` が承認ゲート 2 と本番のステージを書き出すことで、本番のブランチの決め方は含まない。この食い違いは fast / auto と単独の `new release --channel prod` にも同じ形である。#1914 の設計は、本番のブランチ（`a.production_branch`）が無いときは本番のステージを置かない形で、今の振る舞いを変えずに避ける。根拠: Value 6（MVV 版 2。根本原因の層で直すため、本番のブランチの決め方を 1 か所にそろえる変更として分ける）
>
> ## 直さないと何が起きるか
>
> `worktree.json` に `production_branch` を書かず既定ブランチを本番にしているリポジトリでは、経路は雛形と判定されるのに本番のプランを雛形で組めず、`new release --channel prod` も fast / auto の `new sprint` も止まる。#1914 の後は normal の `new sprint` が本番のステージを置かず、本番を手で配る手当てが残る。
>
> ## 起票者の推定
>
> - 雛形の本番のブランチは、経路の判定と同じく既定ブランチへ後退するのが正しい（推した出所: `lib/repo.py:137` の docstring と用語集の「本番」の行）
>
> ## 修正レイヤー
>
> `supervise_lib/decl.py` の `apply_decls`（255〜256 行）は `lib/repo.production_branch`（`worktree.json` の `production_branch` → 既定ブランチ）を呼ぶ形になっている。ただし既定ブランチを解く木に `decl_roots` の先頭（`--worktree` のパス）を渡すため、まだ作っていない worktree では既定ブランチ（origin/HEAD）を解けず、同じエラーで止まる。直す場所は、既定ブランチをメインディレクトリ（存在する木）から解く形にすることである。ai-plugins は `production_branch` を宣言しているため、この経路を通らない。
>
> 2026-10-31 に一時のリポジトリで再現した（`{"base_branch":"main"}` だけで止まり、`production_branch: main` を足すと 12 ステップのプランが ok）。2026-11-04 にも同じ形（origin/HEAD は main、`.ndf/supervise.json` は `release` の `form`・`plugin`・`runtimes`）で同じエラーになることを確かめた。
>
> ## 由来
>
> issue #1914

（書き手: 起票者（#1914 の設計の途中で範囲外の課題として起票した AI）、日付: 2026-10-24 起票、「修正レイヤー」の節は 2026-10-31・2026-11-04 の追記。利用者の指示の原文は含まない）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 雛形の本番のブランチは、リリースの経路の判定と同じく既定ブランチへ後退するのが正しい | `plugins/ndf/scripts/lib/repo.py` の `production_of` の docstring と、用語集の「本番」の行 | 受け入れ条件 1〜3・5 |
| S2 | 直す場所は、既定ブランチをメインディレクトリ（存在する木）から解く形にすることである | 本文の「修正レイヤー」の節（2026-10-31・2026-11-04 の再現） | 受け入れ条件 1・4。直し方の形は設計で決める |
| S3 | `new release --channel prod` だけでなく、fast / auto と normal の `new sprint` も同じ原因で本番を置けない | 本文の「なぜこの変更の範囲外なのか」「直さないと何が起きるか」 | 受け入れ条件 5 |

## 目的

- `.ndf/worktree.json` に `production_branch` を書かず、既定ブランチを本番にしているリポジトリで、`supervise.py new` が本番の配布を含むプランを組めるようにする
- 本番のブランチの決め方を、リリースの経路の判定（`lib/delivery.py`）とプランの雛形（`supervise_lib`）で同じ結果にそろえる（Value 6・Value 7）

## 前提

- 前提 1: 本番のブランチの決め方の正本は、今ある `lib/repo.py` の `production_of`（`production_branch` の宣言 → 既定ブランチ）とする。順序は変えない
- 前提 2: 既定ブランチは、まだ作っていない worktree のパスではなく、存在する git の木（`--repo`、`--worktree` のパスの `/.worktrees/` より前、今のディレクトリの順で最初に git として読める木）から解く。メインディレクトリから解いた既定ブランチと、そのリポジトリの worktree から解いた既定ブランチは同じ値になる（origin の HEAD はリポジトリで 1 つ）
- 前提 3: 本番のブランチが既定ブランチに決まっても、リリースプランは承認ゲート 2（`approve` のステップ）を持ったままにする。この変更は承認の要否を変えない
- 前提 4: 2026-11-05 に一時のリポジトリで確かめた今の振る舞いを基準にする。worktree を作る前は上の「依頼（原文）」と同じエラーで止まり、`git worktree add` で worktree を作った後は同じコマンドが 13 ステップのリリースプランを書く

## 対象範囲

含む:
- `supervise.py new` の `release`・`sprint`・`close`（本番を使う雛形）で、本番のブランチを既定ブランチへ後退させる経路が、まだ作っていない worktree でも働くこと
- 本番のブランチが決まらないときのエラーの文面に、3 つの出所（`--production-branch`、`.ndf/worktree.json` の `production_branch`、既定ブランチ）をそろえて書くこと

含まない:
- 本番のブランチの決め方の順序と出所を変えること（前提 1）
- 昇格のプラン（`delivery_templates.py` の本番チャネルの決め方）の振る舞いを変えること。同じ既定ブランチの補完を受けるかは設計で確かめ、変わる場合は受け入れ条件 6 の退行の確認に含める
- ai-plugins の `.ndf/worktree.json` の `production_branch` を消すこと（C7）
- 承認ゲート 2 の置き方・要否を変えること

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `supervise.py new <種類>` が起動された | 利用者か conductor のコマンド | — | — |
| E2 | 宣言（`.ndf/worktree.json`・`.ndf/supervise.json`）を読んだ | E1 | 宣言が壊れていれば宣言のエラーで止まる（今の振る舞い） | E1 |
| E3 | 本番のブランチが決まった（引数 → `production_branch` → 存在する木から解いた既定ブランチ） | E2 で種類が本番を使う雛形のとき | 3 つのどれでも決まらなければ、3 つの出所を挙げたエラーで止まる（受け入れ条件 4） | E2。worktree が作られていることを前提にしない |
| E4 | プランを書いた | E3 | 書けなければ今のエラーの経路 | E3 |

## 受け入れ条件

- [ ] 受け入れ条件 1: origin の HEAD が `main` を指し、`.ndf/worktree.json` が `{"base_branch":"main"}` だけで、`.ndf/supervise.json` の `release` に `form: package-plugin`・`plugin`・`runtimes` があるリポジトリで、まだ作っていない worktree のパス（`<メインディレクトリ>/.worktrees/release/v1.0.0`）を `--worktree` に渡して `supervise.py new release --version 1.0.0 --prs 1 --channel prod --out <パス>` を打つと、終了コード 0・結果 JSON の `status` が `ok` で、`approve`・`snapshot`・`release`・`verify` のステップを持つリリースプランを書く
- [ ] 受け入れ条件 2: 受け入れ条件 1 のプランで、本番の配布の確認に使うブランチ（`verify` などが参照する本番の ref）が `main` である
- [ ] 受け入れ条件 3: 受け入れ条件 1 のリポジトリで worktree を作った後に同じコマンドを打っても、受け入れ条件 1 と同じステップの並びのプランを書く（worktree の有無で結果が変わらない）
- [ ] 受け入れ条件 4: origin の HEAD もローカルの `main` / `master` も無く、`production_branch` も `--production-branch` も無いリポジトリでは、`new release --channel prod` が今と同じく止まり、エラーの文面が `--production-branch`・`.ndf/worktree.json` の `production_branch`・既定ブランチの 3 つを挙げる
- [ ] 受け入れ条件 5: 受け入れ条件 1 のリポジトリで、まだ作っていない worktree のパスを渡した `supervise.py new sprint`（`pace: normal`・`fast`・`auto` のそれぞれ）が、承認ゲート 2 と本番のステージを置いたプランを書き、「本番のブランチ（…）が無いため、承認ゲート 2 と本番は置いていない」の案内を出さない
- [ ] 受け入れ条件 6: 退行しないこと。`production_branch` を宣言したリポジトリ（宣言の値が既定ブランチと違う場合を含む）では宣言の値を、`--production-branch` を渡したときは引数の値を本番のブランチに使う。ai-plugins のメインディレクトリで受け入れ条件 1 と同じ `new release --channel prod` を打った結果のプランが、変更の前後で同じ JSON になる
- [ ] 受け入れ条件 7: 受け入れ条件 1 の宣言で、リリースの経路の判定（`lib/delivery.py`）が使う本番のブランチと、リリースプランが使う本番のブランチが同じ値になる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `production_branch` を宣言したリポジトリでは、既定ブランチを解く git の起動を足さない（今の `repo.production_branch` の早期の戻りを保つ） |
| 運用・保守性 | 本番のブランチの決め方を `lib/repo.py` の 1 か所に置いたままにし、雛形の側に別の決め方を足さない（Value 7） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`production_branch` を宣言していないリポジトリで、今は止まる `new release --channel prod` と `new sprint` が本番を含むプランを書くようになる。引数と宣言の形は変わらない。エラーの文面が出所を 3 つ挙げる |
| データ | 無し |
| 既存の振る舞い | `production_branch` を宣言したリポジトリ（ai-plugins を含む）は変わらない（受け入れ条件 6） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`。受け入れ条件 1〜7 は一時の git リポジトリ（bare の origin と clone、`git remote set-head origin main`）を作るテストで確かめる |
| 静的解析・型検査 | リポジトリの CI と同じ検査（`python3 scripts/check-skill-frontmatter.py` などは変更範囲に当たるものだけ） |
| 手動確認 | 無し |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「マーケットプレイスの構造」。変更は `plugins/ndf/scripts/` の中に置く。安定版の経路（hook・結果 JSON の契約に当たる）で、実験版の置き場は使わない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。外部コマンド（git）の挙動は書く前に実行して確かめる |
| テスト戦略 | 一時の git リポジトリを使う結合の階層のテストで `supervise.py new` を通す。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、受け入れ条件ごとのテストの追加 |
| 確認してから行う | 本番のブランチの決め方の順序を変えること（承認ゲート 1 で人が見る） |
| 行わない | `.ndf/` の宣言の書き換え（C7）、承認ゲート 2 の置き方の変更、範囲外のリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 既定ブランチを解く木の選び方（`decl_roots` の並びから最初の git の木を選ぶか、`lib/decl.py` の `main_dir` を使うか） | 設計（`design`） | 設計 Pull Request |
| 昇格のプランが同じ補完を受けるか（「含まない」の 2 行目） | 設計（`design`） | 設計 Pull Request |
