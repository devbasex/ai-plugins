# README の更新情報の節と CHANGELOG の版の節: 配布の手順は README に触れず、子の箇条は親の項目の中に残す

plugin の README は現行版の説明と、CHANGELOG.md を参照する固定の「更新情報」の節だけを持つ。版ごとの変化と移行の手順の
全件は CHANGELOG.md の版の節が正本である。配布の手順（`release-steps.py` の `bump`・`changelog`・`notes`）は README を読まず
書かないため、README の行数は配布で変わらない。あわせて、PR 本文の字下げした子の箇条は、親の項目の中に 1 段の入れ子として
残す。

例: PR #1861 の本文の「利用者向けの変化」が次の形のとき。

```markdown
- ndf の design Skill では、決定を分けたファイルの形が次の 3 点に決まっています。
  - ファイルの名前
  - `## 決定の記録` の節
  - `### 決定 N` の見出し
```

`gh_sections.section_items` は項目を 1 件だけ返し、PR の番号の印は親の 1 行目にだけ付く。CHANGELOG.md の版の節とスプリント PR の
本文には、親の箇条の下に子の箇条 3 行が入れ子で並ぶ。`plugins/ndf/README.md` は `notes` の前後でバイト単位で同じである。

```text
ndf の design Skill では、決定を分けたファイルの形が次の 3 点に決まっています。（#1861）
  - ファイルの名前
  - `## 決定の記録` の節
  - `### 決定 N` の見出し
```

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界・既知の限界・テスト観点）を残す。
**手順・引数・終了コード・結果 JSON の読み方の正は Skill とスクリプトにある。**

| 何を読むか | 正本 |
| --- | --- |
| `bump`・`changelog`・`notes` の引数・終了コード・結果 JSON | [`release-steps.py`](../../plugins/ndf/scripts/release-steps.py) の冒頭の説明と [`release` の `references/release-steps.md`](../../plugins/ndf/skills/release/references/release-steps.md) |
| 配布の手順と CHANGELOG の版の節の扱い | [`release` の SKILL.md](../../plugins/ndf/skills/release/SKILL.md) |
| PR 本文の `## 利用者向けの変化` の書き方 | [`pr` の SKILL.md](../../plugins/ndf/skills/pr/SKILL.md) |
| 版数を持つ箇所と、README が版ごとの更新の節を持たない決まり | [docs/versioning-and-distribution.md](../versioning-and-distribution.md) |
| CHANGELOG の版の節の形と移行の手順の写し | [ndf-release-other-plugin-levels-and-migration.md](ndf-release-other-plugin-levels-and-migration.md) |

## 用語

「更新情報の節」「版の節」「子の箇条」の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。この文書の
「版ごとの更新の節」は、README が持つことを禁じる `## v<版> へ更新するとき` の見出しから次の `## ` までを指す。

## 背景

`release-steps.py notes` は PR 本文の「利用者向けの変化」の箇条をすべて README の版ごとの更新の節へ写していた。10.17.68 では
`plugins/ndf/README.md` が 500 行の上限を超え、開発版と本番の両方で `sync` が落ちて、`fix` の worker（LLM。各約 $0.25）が README を
縮めた。縮め方は 2 回で違った（項目の併合と、1 項目 1 行の分割の取り消し）。更新の節の外が 478 行で、使えるのは 21 行だけだった。

2 回目の失敗（506 行）は、PR #1861 の本文の字下げした子の箇条を `section_items` が独立した項目として読み、2 つの PR の番号の印を
付けて 3 行に分けたことによる。割れたのはスプリント PR の本文を組む `pr_materials.collect_changes` の時点で、README を書かなくても
CHANGELOG.md とスプリント PR の本文で同じ割れ方が起きる。

版ごとの更新の節は 11 本の README に 12 節あり（mcp-playwright は 2 節）、ndf のほかは手書きだった。`bump` が見出しの版数だけを
書き換えるため、本文が前の版の説明のまま残りうる。MCP プラグインの多くは CHANGELOG.md に版の節を持たず、README にしか無い記述があった。

利用者の指示（承認ゲート 1）: 「そもそもREADME.mdに更新情報を記載する必要はない。as-isの現行版の情報のみを記載すれば良い。
『更新情報はCHANGELOG.md』を参照、と書かれていれば十分では？」

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | README に版ごとの更新の節を置かず、`bump`・`changelog`・`notes` は README に触れない | README の行数を配布の入力から切り離せる。行数の上限を `notes` が知る必要が無く、宣言も引数も足さずに済む。同じ箇条を 2 か所へ書かないため、片方を直して食い違う経路も無くなる | 更新の節を残し、版の節への案内と移行の手順の件数だけを毎版書き直す（見出しが版を名乗る限り毎版 README に触れ続ける）。行数の予算の中で箇条を載せ、載らない分を案内 1 行に置き換える（上限の宣言・順序・止まる経路が要り、二重に残る） |
| 2 | README には版を名乗らない固定の `## 更新情報` の節を 1 つ置き、`main` の CHANGELOG.md の GitHub の URL を指す | 版数を持たないため、版を上げても誰も書き換えない。plugin のディレクトリだけが配られるプラグインのキャッシュには CHANGELOG.md が無く、相対パスでは辿れない。URL は README の本文が持つため、スクリプトの既定にほかのプロジェクトの値が入らない | 案内を置かない（読み手が在りかを知る手がかりが無くなる）。相対パス `../../CHANGELOG.md`（キャッシュでは切れたリンク） |
| 3 | 今の節の記述は、CHANGELOG.md の版の節へ移してから外す（1 回だけ） | MCP プラグインの多くは版の節を持たず、そのまま消すと移行の手順（再インストールが要ること・旧名の Skill の対応）が git の履歴にしか残らない。版の節が無ければ足し、版に依らない更新のコマンドは README の導入の節へ寄せる | 節をそのまま消す |
| 4 | `check-doc-staleness.py` のチェックを逆向き（版ごとの更新の節があれば落とす）にし、全 plugin の README へ広げる | 配布の手順が見出しを書き換えなくなったため、人の手で書き足された節は版を上げても古いまま残る。それを CI で止める | チェックを消す（「README は現行版だけ」が崩れても気づけない） |
| 5 | 子の箇条は親の項目の中に Markdown の入れ子のまま残し、`（#番号）` は親の 1 行目にだけ付ける | 書き手が付けた構造をそのまま読み手へ渡せ、呼び手は今と同じ `f"- {item}"` で正しい入れ子になる。スプリント PR の本文から読み直しても同じ形に戻る | 子の箇条を親の 1 行へつなぐ（区切りの語を選ぶ規則が要り、親の文末の句点と組み合わせると文が崩れる） |
| 6 | 子の箇条は字下げの幅を問わず 1 段の `  - ` に揃える | 元の字下げを保つと、1 字下げの箇条は入れ子にならず親と並んだ別の箇条として描かれる。2 段以上の深さは PR 本文で使われていない | 元の字下げを保つ |
| 7 | 直す場所は `notes` でなく共通の部品の `section_items` にする | 割れるのは実装 PR からスプリント PR の本文へ写す時点で、`notes` はその割れた形を読む。割れた後の項目からは、どれが子だったかを読み取れない | `notes` の中で子の箇条をつなぎ直す |

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | `bump`・`changelog`・`notes`（`--approval` なし）は plugin の README を読まず書かない。走らせる前後で README はバイト単位で同じ | テストが落とす |
| I2 | plugin の README（`plugins/*/README.md`・`plugins/mcp/*/README.md`）に `## v<版> へ更新するとき` の見出しが無い | `check-doc-staleness.py` が非 0 で落とす（`validate-runtime-plugins.sh` から走る） |
| I3 | 版の節には、渡した PR のうちマージされたものの利用者向けの変化と移行の手順の項目が 1 件も欠けずに並ぶ | テストが落とす |
| I4 | 同じ版・同じ PR の組で `notes` を続けて走らせると、2 回目の後の CHANGELOG.md は 1 回目の後とバイト単位で同じ | テストが落とす |
| I5 | 字下げした子の箇条は親の項目の中に 1 段の入れ子として残り、独立した項目にならない。`（#番号）` は親の 1 行目にだけ付く | テストが落とす |
| I6 | `notes --approval` が書く 2 つの欄・未検証の節・移行の手順の節は、子の箇条を含まない入力で変わらない。表のセルでは改行が `<br>` になる | テストが落とす |

### 境界

`section_items` が節の行をどう読むか:

| 行 | 読み方 |
| --- | --- |
| 字下げの無い箇条（`- ` / `* `）か字下げの無い文 | 新しい項目 |
| 字下げした箇条（幅は問わない） | 前の項目へ改行と `  - <本文>` でつなぐ（子の箇条） |
| 字下げした箇条でない文 | 前の項目へ空白 1 つでつなぐ（続きの行） |
| 節の最初の行が字下げしている | 新しい項目 |
| 空行・`無し` だけの項目 | 読まない |
| 本文に `#<番号>` がすでにある項目 | `（#番号）` を付けない |

`section_items` の呼び手（`pr_materials.collect_changes`・`release_lib/others.migration_items`・`supervise_lib/pr.py` の移行の手順の
有無の判定）は、返す項目の形が変わるだけで変えていない。`check-doc-staleness.py` は ai-plugins の中のチェックで、
NDF を使うほかのプロジェクトの README は見ない。

### 更新情報の節の形

```markdown
## 更新情報

版ごとの変更と移行の手順は [CHANGELOG.md](https://github.com/devbasex/ai-plugins/blob/main/CHANGELOG.md) の `[ndf <版>]` の節にあります。
```

`[ndf <版>]` の `ndf` は plugin の名前（`playwright-kit`・`mcp-serena` など）に置き換える。版数は書かない。1 本の README に
1 つだけ置く（`plugins/ndf/`・`plugins/playwright-kit/`・`plugins/mcp/` の 10 本の計 12 本）。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 開発版の読み手 | CHANGELOG.md は `notes` が基底の版で作業中の版の節を書くが、更新情報の節のリンクは `main` を指す。開発版を入れた利用者は、次の正式版の節が `main` に載るまで README から辿れない。開発版の読み手がどこを読むかを測ってから、リンク先を変えるかを決める |
| 過去のスプリント PR | PR #1863 の本文の割れた子の箇条（3 行）は過去の記録として残す |
| ほかのプロジェクトの README | 版ごとの更新の節があっても、配布の手順はもう書き換えない。外すかはそのプロジェクトが決める |
| リリース後の実測 | 配布（開発版と本番）の `progress.jsonl` に、README の行数による `sync(exit=1) → fix` の並びが無いことをリリース後テストで見る |

## テスト観点

`notes` と `changelog` は [`test_release_steps.py`](../../plugins/ndf/scripts/tests/test_release_steps.py)・
[`test_release_notes_changelog.py`](../../plugins/ndf/scripts/tests/test_release_notes_changelog.py)、見出しのチェックは
[`scripts/tests/test_doc_staleness.py`](../../scripts/tests/test_doc_staleness.py) にある。入力は実物の PR 本文の形（子の箇条・移行の手順あり）から作る。

| 観点 | 満たすこと |
| --- | --- |
| README を変えない（I1） | PR #1863 の実物の本文と 10.17.68 の配布の直前の README・CHANGELOG へ `changelog` と `notes` を走らせると、README がバイト単位で同じで、`check-doc-line-limit.py --root` を exit 0 で通ること。箇条が 40 行分ある入力でも同じであること |
| 全件が並ぶ（I3） | 版の節に利用者向けの変化の項目が全件並び、移行の手順が 1 件以上なら `### 移行の手順` の下に全件、0 件なら見出しを作らないこと |
| 冪等（I4） | 同じ版・同じ PR の組で `notes` を 2 回走らせ、2 回目の後の CHANGELOG.md が 1 回目の後と同じであること |
| 子の箇条（I5） | PR #1861 の形で項目が 1 件、子の箇条が `  - ` の行として残り、`（#番号）` が 1 行目にだけ付くこと。スプリント PR の本文に組んで読み直しても同じ項目に戻ること |
| 承認資料（I6） | `notes --approval` の 2 つの欄・未検証の節・移行の手順の節が変更前の出力と同じであること |
| `bump` | 版ごとの更新の節が無い README のリポジトリで、README がバイト単位で同じで、`manual` に README の更新案内の項目が無いこと |
| 見出しのチェック（I2） | ndf・playwright-kit・MCP プラグインのいずれかの README に見出しがあると非 0、無ければ見出しを理由に落ちないこと |
| 手動 | 12 本の README に `## 更新情報` の節が 1 つあり、`check-doc-staleness.py --root .` と `check-markdown-links.py --root .` が exit 0 であること |

## 関連リンク

- [`lib/gh_sections.py`](../../plugins/ndf/scripts/lib/gh_sections.py) の `section_items` / [`release-steps.py`](../../plugins/ndf/scripts/release-steps.py) / [`scripts/check-doc-staleness.py`](../../scripts/check-doc-staleness.py)
- [ndf-release-other-plugin-levels-and-migration.md](ndf-release-other-plugin-levels-and-migration.md)（CHANGELOG の版の節と移行の手順）
- [docs/versioning-and-distribution.md](../versioning-and-distribution.md)（版数を持つ箇所）
- 課題 [#1867](https://github.com/devbasex/ai-plugins/issues/1867)・子の箇条が割れた PR [#1861](https://github.com/devbasex/ai-plugins/pull/1861) / [#1863](https://github.com/devbasex/ai-plugins/pull/1863)
