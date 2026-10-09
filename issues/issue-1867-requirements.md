# #1867: release-steps.py notes が README を 500 行超にし、配布のたびに sync が落ちて LLM の修正が入る

正は課題の本文（#1867）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> #### 何を見つけたか
>
> `release-steps.py notes` が `plugins/ndf/README.md` に更新の案内を書き足すと、README が 500 行を超える。
> そのため配布のプランの `sync`（`sync-check` の `validate`）が落ち、毎回 `fix` の worker（`claude -p`）が README を手で縮めている。
>
> | 版 | 修正のコミット | worker の費用 |
> | --- | --- | --- |
> | 10.17.68-dev.1 | `4f7abe29e`（更新案内を 500 行に収める） | 約 $0.25 |
> | 10.17.68 | `3c67921ac`（更新の案内を 1 項目 1 行の分割から戻し README を 500 行以内に収める） | 同程度 |
>
> 10.17.68 を出した後の `plugins/ndf/README.md` は 499 行である。次の配布でも同じ失敗が起きる見込みが高い。
>
> #### どこで見つけたか
>
> スプリント m1847 の配布のプラン `7-release.json`（開発版）と `8-release-prod.json`（本番）。どちらも
> `sync(exit=1) → judge[fix] → fix → sync(exit=0)` の順に通った。記録は `~/.local/state/ndf/sv/sprint-m1847/7-release-1863-state/progress.jsonl` と
> `8-release-prod-state/progress.jsonl` にある。
>
> #### なぜこの変更の範囲外なのか
>
> スプリント m1847（#1847・#1241・#1843・#1822・#635・#767）の受け入れ条件は、配布の手順にも README の行数にも触れていない。
> 即時修正の 4 条件のうち、方針の範囲は満たさない。更新の案内をどれだけ README に残し、どこへ逃がすかを決める必要があるためである。
> 根拠: 共通原則の優先順位の「効率」（トークン・処理の回数・止まらずに進むこと）。MVV なし。
>
> #### 直さないと何が起きるか
>
> 配布のたびに `sync` が 1 回落ち、LLM の修正が 1 回入る。修正の仕方は worker に任されるため、案内の削り方が版ごとに変わる。
> コーディングと設計文書のほかは LLM を使わない方針に反する。
>
> #### 仕組み（ndf 10.17.68 で確認）
>
> README の更新の節の外が 479 行あり、節に使えるのは 21 行までである（上限は `check-doc-line-limit.py` の `LIMIT = 500`）。`release-steps.py` の `write_notes`（965 行〜）は行数を見ずに節を差し替える。過去 12 版の節は 5〜20 行で、20 行は 10.17.68 が初めてである。同じ版で 2 回落ちたのは、開発版で縮めた節を本番の `notes` が PR の本文から作り直したため（#1834 と同じ仕組み）。コミット 3c67921ac は README と CHANGELOG だけを直し、スクリプトは変えていない。
>
> 直す場所は `write_notes` である。行数の予算内に収めるか、案内を README から外して CHANGELOG を正にするかを着手の時に決める。
>
> #### 由来
>
> PR #1863

## 利用者の指示（原文）

承認ゲート 1（設計 PR #1874）で、更新の案内の置き場について次の方針が出た。

> そもそもREADME.mdに更新情報を記載する必要はない。as-isの現行版の情報のみを記載すれば良い。『更新情報はCHANGELOG.md』を参照、と書かれていれば十分では？

## 目的

- 配布のたびに `release-steps.py notes` の出力で README が行数の上限を超え、`sync` が落ちて LLM の `fix` が入る流れを無くす。README を配布の手順で書き換えないことで、行数を入力から切り離す
- plugin の README は現行版の説明だけを持つ。版ごとの変化と移行の手順の全件は CHANGELOG.md の版の節を正本にし、README からは CHANGELOG.md を参照する固定の案内で辿れるようにする

## 前提

- 前提 0: この文書の「版の節」は CHANGELOG.md の `## [<plugin> <版>]` の見出しから次の `## ` までを、「版ごとの更新の節」は plugin の README の `## v<版> へ更新するとき` の見出しから次の `## ` までを指す
- 前提 1: 変化の全件の正本は CHANGELOG.md の版の節に置く。CHANGELOG.md は `check-doc-line-limit.py` の `EXEMPT` にあり、行数の上限を受けない
- 前提 2: 版ごとの更新の節を書き換えるのは今、`bump`（`release_lib/bump.py` の `bump_update_heading`）・`changelog`（`release-steps.py` の `_update_plugin_readme`）・`notes`（`release-steps.py` の `write_notes`）の 3 つで、見出しの版数を `scripts/check-doc-staleness.py` の `check_upgrade_heading` が見る
- 前提 3: 10.17.68 の 2 回目の失敗（506 行）は、PR 本文の字下げした子の箇条（#1861 の「次の 3 点」の下の 3 行）を `gh_sections.section_items` が独立した項目として読み、`（#1861）（#1863）` を付けて 3 行に分けたことによる。README を書かなくなっても CHANGELOG.md とスプリント PR の本文で同じ割れ方が起きるため、子の箇条を正しく扱うことも、この課題で直す

## 対象範囲

含む:
- `release-steps.py` の `notes`（`--approval` なし）と `changelog` が plugin の README を書かないこと
- `bump` が README の版ごとの更新の節の見出しを書き換えないこと、`check-doc-staleness.py` の見出しのチェック
- 版ごとの更新の節を持つ今の README 11 本（`plugins/ndf/`・`plugins/playwright-kit/` と `plugins/mcp/` の 9 本）から節を外し、CHANGELOG.md を参照する固定の節に置き換えること。節を持たない `plugins/mcp/mcp-bigquery/README.md` にも固定の節を置く。節にあった版の記述は CHANGELOG.md の版の節へ移す
- `notes` が CHANGELOG.md の版の節へ書く箇条の形（子の箇条の扱い）
- `release` Skill・`pr` Skill の手順書と、版と配布の文書の該当の説明

含まない:
- `check-doc-line-limit.py` の上限の値や `EXEMPT` の変更（README を上限の対象から外さない）
- README の版ごとの更新の節の外の書き直し（固定の節を置く 1 か所を除く）
- `notes --approval` が書く本番承認の承認資料の欄
- `changelog` サブコマンドが CHANGELOG.md へ PR の題名を並べる処理
- CHANGELOG.md の既存の版の節の書き換え（README から移す記述を足すことを除く）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 版を上げた | 配布のプランの `bump` | **README の版ごとの更新の節には触れない** | — |
| E2 | CHANGELOG の版の節へ PR の題名を並べた | `changelog` | CHANGELOG.md が無ければ 3 で止まる。**README は書かない** | E1 |
| E3 | PR 本文の「利用者向けの変化」と「移行の手順」から、CHANGELOG の版の節を組み直した | `notes` | 版の節が無ければ 3 で止まる。PR がすべて未マージなら 3 で止まる。**README は書かない** | E2 |
| E4 | 行数の検査が README を見た | `sync`（`validate-runtime-plugins.sh` の `check-doc-line-limit.py`） | README は配布の手順で変わらないため、配布で落ちない。**`fix`（LLM）を通らないことが目的** | E3 |
| E5 | 本番の `notes` が、開発版と同じ PR の組から CHANGELOG を組み直した | 本番の配布のプランの `notes` | 版の節の本文は開発版の後と同じになる | E3（開発版） |

## 受け入れ条件

- [ ] 1. PR #1863 の本文（10.17.68 の配布で使った実物）を入力に、10.17.68 の配布の直前の README と CHANGELOG へ `changelog` と `notes` を走らせると、`plugins/ndf/README.md` は走らせる前とバイト単位で同じで、`python3 scripts/check-doc-line-limit.py --root .` を exit 0 で通る
- [ ] 2. 利用者向けの変化の箇条が 40 行分ある入力でも、`changelog` と `notes` の後の plugin の README は走らせる前とバイト単位で同じで、CHANGELOG.md の版の節には PR 本文の「利用者向けの変化」の箇条が 1 件も欠けずに並ぶ
- [ ] 3. PR 本文の「移行の手順」の箇条がある版では、CHANGELOG.md の版の節の `### 移行の手順` の下にその全件が並ぶ
- [ ] 4. 実装の後、`plugins/ndf/README.md`・`plugins/playwright-kit/README.md`・`plugins/mcp/*/README.md` に版ごとの更新の節（`## v<版> へ更新するとき`）が無く、CHANGELOG.md を参照する固定の節が 1 つある
- [ ] 5. 実装の前に版ごとの更新の節にあった、その版に固有の記述（変わったこと・移行の手順）は、CHANGELOG.md の該当する版の節で読める。版の節が無かった版は節を足す
- [ ] 6. `bump` は plugin の README の版ごとの更新の節の見出しを書き換えず、その見出しが無いことを `manual`（手の作業）に出さない
- [ ] 7. `python3 scripts/check-doc-staleness.py --root .` は、plugin の README に版ごとの更新の節の見出しがあると非 0 で終わり、無ければ見出しを理由に落ちない
- [ ] 8. 同じ版・同じ PR の組で `notes` を 2 回続けて走らせると、2 回目の後の CHANGELOG.md は 1 回目の後と 1 バイトも変わらない
- [ ] 9. PR 本文の「利用者向けの変化」の字下げした子の箇条（#1861 の本文の「ファイルの名前」など 3 行）は、`（#番号）` の付いた独立した項目として CHANGELOG.md とスプリント PR の本文に並ばない。子の箇条の文は親の項目の中に残る
- [ ] 10. `notes --approval` が書く承認資料の 2 つの欄・未検証の節・移行の手順の節は、今と同じ中身になる
- [ ] 11. 既存のテスト（`plugins/ndf/scripts/tests/test_release_steps.py`・`scripts/tests/test_doc_staleness.py` ほか全体）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 1 回の配布（開発版と本番）で、README の行数を理由とする `sync` の失敗と `fix` の LLM の呼び出しが 0 回になる（10.17.68 では各 1 回・約 $0.25） |
| 移行性 | 利用者の操作は要らない。README の版ごとの更新の節は実装の PR で固定の節へ置き換わり、以後の配布は README に触れない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `notes`・`changelog` の引数・終了コードは変わらない。結果の JSON から README の項目が無くなり、`changelog` の `next`（更新案内の書き直し）が出なくなる。`bump` の `manual` から更新案内の項目が無くなる |
| データ | 無し |
| 既存の振る舞い | plugin の README の版ごとの更新の節が無くなり、固定の節が置かれる。`check-doc-staleness.py` の見出しのチェックが逆向き（あると落ちる）になる。CHANGELOG.md の版の節の子の箇条の並び |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_release_steps.py scripts/tests/test_doc_staleness.py -q`、全体は `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `bash scripts/validate-runtime-plugins.sh`（`check-doc-line-limit.py`・`check-doc-staleness.py` を含む）、`python3 scripts/check-markdown-links.py --root .` |
| 手動確認 | 次の配布（開発版と本番）の配布のプランの `progress.jsonl` に、README の行数による `sync(exit=1) → fix` の並びが無いことを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | スクリプトは `plugins/ndf/scripts/`（`release-steps.py`・`release_lib/`・`lib/`）。ai-plugins に固有の値は README の本文と `.ndf/` の宣言へ置き、スクリプトの既定に入れない（Value 5） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。外部コマンドとこのリポジトリの入力の形（PR 本文の子の箇条）は、書く前に実物で通す |
| テスト戦略 | `release-steps.py notes` と `changelog` の単体テストで、実物の PR 本文の形（子の箇条・移行の手順あり）を入力に受け入れ条件 1〜3・8〜10 を確かめる。`.md` の文言は照合しない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`validate-runtime-plugins.sh` の実行、`release-steps.md` の説明の更新 |
| 確認してから行う | 結果の JSON と `manual` の項目の削除（承認ゲート 1 の設計で見る） |
| 行わない | `check-doc-line-limit.py` の上限や除外の変更、README の版ごとの更新の節の外の書き直し、CHANGELOG.md の既存の記述の書き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 字下げした子の箇条を、親の項目の行へつなぐか、Markdown の入れ子のまま残すか | 設計（`design`） | 設計 PR |
