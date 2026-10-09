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

## 目的

- 配布のたびに `release-steps.py notes` の出力で README が行数の上限を超え、`sync` が落ちて LLM の `fix` が入る流れを無くす。案内の削り方を worker に任せず、スクリプトが毎回同じ形で書く
- 利用者が更新のときに読む案内（README の更新の節）から、その版の変化の全件へ辿れる状態を保つ

## 前提

- 前提 0: この文書の「更新の節」は plugin の README の `## v<版> へ更新するとき` の見出しから次の `## ` までを、「版の節」は CHANGELOG.md の `## [<plugin> <版>]` の見出しから次の `## ` までを指す
- 前提 1: README の `## v<版> へ更新するとき` の見出しは残す。`bump` の `bump_update_heading` と `changelog` の `_update_plugin_readme` がこの見出しを探して動くためである
- 前提 2: 変化の全件の正は CHANGELOG.md の版の節に置く。CHANGELOG.md は `check-doc-line-limit.py` の `EXEMPT` にあり、行数の上限を受けない
- 前提 3: 行数の上限 500 は ai-plugins の `scripts/check-doc-line-limit.py` の値であり、NDF を使うほかのプロジェクトにあるとは限らない（Value 5）
- 前提 4: 10.17.68 の 2 回目の失敗（506 行）は、PR 本文の字下げした子の箇条（#1861 の「次の 3 点」の下の 3 行）を `gh_sections.section_items` が独立した項目として読み、`（#1861）（#1863）` を付けて 3 行に分けたことによる。子の箇条を正しく扱うことも、この課題で直す

## 対象範囲

含む:
- `release-steps.py notes`（`--approval` なし）が書く、plugin の README の更新の節の中身と行数
- `notes` が CHANGELOG.md の版の節へ書く箇条の形（子の箇条の扱い）
- 同じ PR の組で開発版と本番の `notes` を続けて走らせたときの結果の一致
- `release` Skill の `references/release-steps.md` の `notes` の説明（振る舞いが変わる分）

含まない:
- `check-doc-line-limit.py` の上限の値や `EXEMPT` の変更（README を上限の対象から外さない）
- README の更新の節の外（479 行）を縮める作業
- `notes --approval` が書く本番承認の承認資料の欄
- `changelog` サブコマンドが PR の題名を並べる処理
- 過去の版の README・CHANGELOG の書き直し

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 版を上げ、README の更新の節の見出しを新しい版へ書き換えた | 配布のプランの `bump` | 見出しが無ければ `manual` に手の作業として出る（今の振る舞い） | — |
| E2 | CHANGELOG と README の更新の節へ PR の題名を並べた | `changelog` | CHANGELOG.md が無ければ 3 で止まる | E1 |
| E3 | PR 本文の「利用者向けの変化」と「移行の手順」から、CHANGELOG の版の節を組み直した | `notes` | 版の節が無ければ 3 で止まる。PR がすべて未マージなら 3 で止まる | E2 |
| E4 | README の更新の節を組み直した | `notes`（E3 と同じ実行） | **今は行数を見ずに書く**。上限を守れないときの扱いが受け入れ条件 5 | E3 |
| E5 | 行数の検査が README を見た | `sync`（`validate-runtime-plugins.sh` の `check-doc-line-limit.py`） | 501 行以上で exit 1 → `judge[fix]` → `fix`（LLM）。**この経路を通らないことが目的** | E4 |
| E6 | 本番の `notes` が、開発版と同じ PR の組から README と CHANGELOG を組み直した | 本番の配布のプランの `notes` | 開発版で手で直した中身が消え、E5 で再び落ちる（#1834 と同じ仕組み） | E4・E5（開発版） |

## 受け入れ条件

- [ ] 1. PR #1863 の本文（10.17.68 の配布で使った実物）を入力に、10.17.68 の配布の直前の README と CHANGELOG へ `notes` を走らせると、`plugins/ndf/README.md` が `python3 scripts/check-doc-line-limit.py --root .` を exit 0 で通る
- [ ] 2. README の更新の節の外が 479 行のとき、更新の節に入る箇条が 40 行分ある入力でも、`notes` の後に README は 500 行以下で、手で直さずに `check-doc-line-limit.py` を exit 0 で通る
- [ ] 3. 受け入れ条件 2 の入力で、CHANGELOG.md の版の節には PR 本文の「利用者向けの変化」の箇条が 1 件も欠けずに並ぶ
- [ ] 4. README の更新の節に載せない箇条があるとき、更新の節の中に CHANGELOG.md の版の節へ辿る案内（リンクか版の見出しの名前）が 1 行ある。全件を載せたときは、この行の有無を問わない
- [ ] 5. 行数の上限を使う場合、上限を守れる形で README を書けない入力では、`notes` は README を書き換えずに非 0 で止まり、結果の JSON に原因（超える行数）を出す。上限を超えた README を書いて 0 を返さない
- [ ] 6. PR 本文の「移行の手順」の箇条がある版では、README の更新の節から移行の手順の全件か、その在りかへの案内が読める
- [ ] 7. 同じ PR の組で `notes` を 2 回続けて走らせると、2 回目の後の README と CHANGELOG.md は 1 回目の後と 1 バイトも変わらない
- [ ] 8. PR 本文の「利用者向けの変化」の字下げした子の箇条（#1861 の本文の「ファイルの名前」など 3 行）は、`（#番号）` の付いた独立した項目として CHANGELOG.md と README に並ばない。子の箇条の文は親の項目の中に残る
- [ ] 9. 行数の上限を使う場合、その値は `.ndf/` の宣言か `notes` の引数から受け、`release-steps.py` に 500 を既定として埋め込まない。上限の宣言も引数も無いプロジェクトでは、`notes` は上限で止まらない
- [ ] 10. `notes --approval` が書く承認資料の 2 つの欄・未検証の節・移行の手順の節は、今と同じ中身になる
- [ ] 11. 既存のテスト（`plugins/ndf/scripts/tests/test_release_steps.py` ほか全体）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 1 回の配布（開発版と本番）で、README の行数を理由とする `sync` の失敗と `fix` の LLM の呼び出しが 0 回になる（10.17.68 では各 1 回・約 $0.25） |
| 移行性 | 利用者の操作は要らない。README の更新の節は次の版の `notes` が書き換えるだけで、過去の版の節は残らない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`notes` の引数か `.ndf/` の宣言に行数の上限が加わる場合も、指定しなければ今の呼び方で動く（互換あり）。上限で止まるときの終了コードと結果の JSON の形が加わる |
| データ | 無し |
| 既存の振る舞い | README の更新の節の中身（全件から一部と案内へ変わりうる）。CHANGELOG.md の版の節の子の箇条の並び |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_release_steps.py -q`、全体は `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `bash scripts/validate-runtime-plugins.sh`（`check-doc-line-limit.py` を含む） |
| 手動確認 | 次の配布（開発版と本番）の配布のプランの `progress.jsonl` に、README の行数による `sync(exit=1) → fix` の並びが無いことを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | スクリプトは `plugins/ndf/scripts/`（`release-steps.py`・`release_lib/`・`lib/`）。ai-plugins に固有の値は `.ndf/` の宣言へ置き、スクリプトの既定に入れない（Value 5） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。外部コマンドとこのリポジトリの入力の形（PR 本文の子の箇条）は、書く前に実物で通す |
| テスト戦略 | `release-steps.py notes` の単体テストで、実物の PR 本文の形（子の箇条・移行の手順あり）を入力に受け入れ条件 1〜10 を確かめる。`.md` の文言は照合しない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`validate-runtime-plugins.sh` の実行、`release-steps.md` の `notes` の説明の更新 |
| 確認してから行う | `notes` の引数・終了コード・結果の JSON の形の追加（承認ゲート 1 の設計で見る） |
| 行わない | `check-doc-line-limit.py` の上限や除外の変更、README の更新の節の外の書き直し、過去の版の記録の書き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 更新の節の形: (a) 行数の予算の中で箇条を載せ、載らない分を CHANGELOG への案内 1 行に置き換える / (b) 更新の節は移行の手順と CHANGELOG の版の節への案内だけにし、変化の全件は CHANGELOG を正にする。(b) は上限の宣言を要らなくし（受け入れ条件 5・9 が働く場面が無くなる）、正本を 1 か所にする（Value 7）。(a) は README だけで主な変化が読める | 設計（`design`）。承認ゲート 1 で人が見る | 設計 PR |
| 字下げした子の箇条を、親の項目の行へつなぐか、Markdown の入れ子のまま残すか | 設計（`design`） | 設計 PR |
